import os
import re
import shutil
import yt_dlp
from typing import Any, Optional

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))


def _cookiefile_from_env() -> Optional[str]:
    """Netscape 格式 cookies.txt；用于云服务器上 B 站等站点的 412/风控缓解。"""
    for key in ("YTDLP_COOKIEFILE", "BILIBILI_COOKIEFILE"):
        raw = (os.getenv(key) or "").strip()
        if not raw:
            continue
        path = os.path.expanduser(raw)
        if not os.path.isabs(path):
            path = os.path.normpath(os.path.join(_BACKEND_DIR, path))
        if os.path.isfile(path):
            return path
    return None


def _cookies_from_browser_from_env() -> Optional[tuple]:
    """
    允许直接复用本机浏览器 Cookie（适合本地开发）：
    - YTDLP_COOKIES_FROM_BROWSER=chrome|edge|firefox|brave|chromium|safari
    - YTDLP_COOKIES_BROWSER_PROFILE=Default（可选）
    """
    browser = (os.getenv("YTDLP_COOKIES_FROM_BROWSER") or "").strip().lower()
    if not browser:
        return None
    supported = {"chrome", "edge", "firefox", "brave", "chromium", "safari"}
    if browser not in supported:
        return None
    profile = (os.getenv("YTDLP_COOKIES_BROWSER_PROFILE") or "").strip()
    if profile:
        return (browser, profile)
    return (browser,)


def _impersonate_from_env() -> Optional[Any]:
    """
    可选浏览器指纹模拟（需安装 curl-cffi）：
    例如 YTDLP_IMPERSONATE=chrome
    """
    v = (os.getenv("YTDLP_IMPERSONATE") or "").strip()
    if not v:
        return None
    # API 侧目标匹配区分大小写，统一转小写更稳（如 Edge-101 -> edge-101）
    v_norm = v.lower()
    try:
        from yt_dlp.networking.impersonate import ImpersonateTarget
        return ImpersonateTarget.from_str(v_norm)
    except Exception:
        # 兜底：交给 yt-dlp 自行处理（或忽略）
        return v_norm


def _http_headers_for_url(url: str) -> dict[str, str]:
    u = (url or "").lower()
    headers: dict[str, str] = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if "bilibili.com" in u or "b23.tv" in u:
        headers["Referer"] = "https://www.bilibili.com/"
    return headers


def _ytdlp_base_opts(url: str) -> dict[str, Any]:
    opts: dict[str, Any] = {"http_headers": _http_headers_for_url(url)}
    imp = _impersonate_from_env()
    if imp:
        opts["impersonate"] = imp
    cf = _cookiefile_from_env()
    if cf:
        opts["cookiefile"] = cf
        return opts
    cfb = _cookies_from_browser_from_env()
    if cfb:
        opts["cookiesfrombrowser"] = cfb
    return opts


def _parse_socket_timeout() -> float:
    raw = (os.getenv("YTDLP_PARSE_SOCKET_TIMEOUT") or "").strip()
    if not raw:
        return 30.0
    try:
        return max(5.0, min(120.0, float(raw)))
    except ValueError:
        return 30.0


def _find_ffmpeg_path() -> Optional[str]:
    """查找 ffmpeg 可执行文件路径"""
    if shutil.which("ffmpeg"):
        return os.path.dirname(shutil.which("ffmpeg"))
    try:
        import static_ffmpeg
        paths = static_ffmpeg.run.get_or_fetch_platform_executables_else_raise()
        return os.path.dirname(paths[0])
    except Exception:
        return None


class VideoDownloader:
    """yt-dlp 封装层，提供视频解析、下载、直链获取能力"""

    DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")

    def __init__(self):
        os.makedirs(self.DOWNLOAD_DIR, exist_ok=True)
        self.ffmpeg_path = _find_ffmpeg_path()
        self.has_ffmpeg = self.ffmpeg_path is not None

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        return re.sub(r'[\\/*?:"<>|]', "_", name)

    @staticmethod
    def _format_filesize(size: Optional[int]) -> str:
        if not size:
            return "未知大小"
        if size < 1024 * 1024:
            return f"{size / 1024:.0f}KB"
        if size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.1f}MB"
        return f"{size / (1024 * 1024 * 1024):.2f}GB"

    @staticmethod
    def _format_duration(seconds: Optional[int]) -> str:
        if not seconds:
            return "00:00"
        hours, remainder = divmod(int(seconds), 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"

    def parse_video(self, url: str) -> dict:
        """解析视频信息，不下载文件"""
        ydl_opts = {
            **_ytdlp_base_opts(url),
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "noplaylist": True,
            "socket_timeout": _parse_socket_timeout(),
            "retries": 1,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:
            msg = str(e)
            low = (url or "").lower()
            if "412" in msg and ("bilibili" in low or "BiliBili" in msg):
                raise ValueError(
                    "B 站返回 HTTP 412（云服务器出口 IP 常被风控）。请在 backend/.env 设置 YTDLP_COOKIEFILE 为"
                    "本机浏览器登录 bilibili.com 后导出的 Netscape 格式 cookies.txt（建议挂载只读）；"
                    "或为 backend 容器配置 HTTPS_PROXY 走可访问 B 站的代理；仍失败则需更换公网 IP。"
                ) from e
            if ("bilibili" in low or "b23.tv" in low) and (
                "geo-restricted" in msg.lower()
                or "deleted or geo" in msg.lower()
                or "try a VPN or a proxy" in msg
            ):
                raise ValueError(
                    "B 站判定该请求为地区不可用或稿件不可播（海外机房常见）。请在 backend/.env 为容器配置 "
                    "HTTPS_PROXY / HTTP_PROXY，使用可访问大陆 B 站网页与播放的出口（如大陆/住宅代理）；"
                    "并确保 cookies 与代理出口地区一致、未过期。"
                ) from e
            raise

        if not info:
            raise ValueError("无法解析该链接")

        formats = self._extract_formats(info)
        platform = info.get("extractor", info.get("extractor_key", "Unknown"))

        return {
            "id": info.get("id", ""),
            "title": info.get("title", "未知标题"),
            "thumbnail": info.get("thumbnail", ""),
            "duration": info.get("duration"),
            "duration_string": self._format_duration(info.get("duration")),
            "uploader": info.get("uploader", info.get("channel", "未知")),
            "platform": platform,
            "view_count": info.get("view_count"),
            "upload_date": info.get("upload_date", ""),
            "description": (info.get("description") or "")[:200],
            "formats": formats,
            "subtitles": list(info.get("subtitles", {}).keys()),
            "automatic_captions": list(info.get("automatic_captions", {}).keys())[:5],
        }

    def _extract_formats(self, info: dict) -> list:
        """从 yt-dlp info 中提取并整理可用格式"""
        raw_formats = info.get("formats", [])
        if not raw_formats:
            return []

        seen = set()
        results = []

        for f in raw_formats:
            vcodec = f.get("vcodec", "none")
            acodec = f.get("acodec", "none")
            height = f.get("height")
            ext = f.get("ext", "mp4")

            has_video = vcodec and vcodec != "none"
            has_audio = acodec and acodec != "none"

            if not has_video:
                continue

            resolution = f"{f.get('width', '?')}x{height}" if height else "未知"
            filesize = f.get("filesize") or f.get("filesize_approx")
            size_label = self._format_filesize(filesize)

            if has_audio:
                label = f"{height}p {ext.upper()} ({size_label})"
                key = (height, ext, "av")
            else:
                label = f"{height}p {ext.upper()} (仅视频, {size_label})"
                key = (height, ext, "v")

            if key in seen:
                continue
            seen.add(key)

            results.append({
                "format_id": f.get("format_id", ""),
                "ext": ext,
                "resolution": resolution,
                "height": height or 0,
                "filesize": filesize,
                "filesize_approx": filesize,
                "vcodec": vcodec,
                "acodec": acodec if has_audio else None,
                "has_audio": has_audio,
                "label": label,
            })

        results.sort(key=lambda x: x["height"], reverse=True)

        if not any(r["has_audio"] for r in results) and results:
            best_video = results[0]
            merged = {
                **best_video,
                "format_id": f"bestvideo+bestaudio/best",
                "label": f"{best_video['height']}p 最佳 (视频+音频合并)",
                "has_audio": True,
                "acodec": "merged",
            }
            results.insert(0, merged)

        return results[:15]

    def download_video(self, url: str, format_id: str, out_dir: Optional[str] = None) -> dict:
        """下载视频到服务器目录，返回文件路径和元数据。out_dir 为空时使用默认 DOWNLOAD_DIR。"""
        target_dir = out_dir if out_dir else self.DOWNLOAD_DIR
        os.makedirs(target_dir, exist_ok=True)

        if not self.has_ffmpeg and "+" in format_id:
            format_id = "best"

        ydl_opts = {
            **_ytdlp_base_opts(url),
            "format": format_id,
            "outtmpl": os.path.join(target_dir, "%(title)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }

        if self.has_ffmpeg:
            ydl_opts["ffmpeg_location"] = self.ffmpeg_path
            ydl_opts["merge_output_format"] = "mp4"

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        if not info:
            raise ValueError("下载失败")

        title = self._sanitize_filename(info.get("title", "video"))
        ext = info.get("ext", "mp4")
        filename = f"{title}.{ext}"
        filepath = os.path.join(target_dir, filename)

        if not os.path.exists(filepath):
            prepared = ydl.prepare_filename(info)
            if os.path.exists(prepared):
                filepath = prepared
                filename = os.path.basename(prepared)
            else:
                for f in os.listdir(target_dir):
                    if title in f:
                        filepath = os.path.join(target_dir, f)
                        filename = f
                        break

        return {
            "filepath": filepath,
            "filename": filename,
            "title": info.get("title", "video"),
            "ext": ext,
        }

    def get_direct_url(self, url: str, format_id: str) -> dict:
        """获取视频直链"""
        ydl_opts = {
            **_ytdlp_base_opts(url),
            "format": format_id,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "socket_timeout": _parse_socket_timeout(),
            "retries": 1,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            raise ValueError("无法获取直链")

        direct_url = info.get("url")
        if not direct_url:
            requested = info.get("requested_formats")
            if requested and len(requested) > 0:
                direct_url = requested[0].get("url")

        if not direct_url:
            raise ValueError("该视频不支持直链下载，请使用服务端下载模式")

        return {
            "direct_url": direct_url,
            "ext": info.get("ext", "mp4"),
            "filesize": info.get("filesize") or info.get("filesize_approx"),
            "title": info.get("title", "video"),
        }
