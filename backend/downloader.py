import os
import re
import shutil
import yt_dlp
from typing import Any, Optional

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))


def _resolve_cookiefile_path(raw: str) -> Optional[str]:
    """解析 cookie 文件路径；只读卷则复制到临时可写文件供 yt-dlp 回写。"""
    path = os.path.expanduser((raw or "").strip())
    if not path:
        return None
    if not os.path.isabs(path):
        path = os.path.normpath(os.path.join(_BACKEND_DIR, path))
    if not os.path.isfile(path):
        return None
    if os.access(path, os.W_OK):
        return path
    try:
        import tempfile

        fd, tmp = tempfile.mkstemp(prefix="ytdlp_cookies_", suffix=".txt")
        os.close(fd)
        shutil.copy2(path, tmp)
        return tmp
    except OSError:
        return path


def _cookiefile_from_env() -> Optional[str]:
    """Netscape 格式 cookies.txt；用于云服务器上 B 站等站点的 412/风控缓解。

    secrets 常以只读卷挂载；yt-dlp 可能回写 cookiefile，故只读时复制到可写临时文件。
    """
    for key in ("YTDLP_COOKIEFILE", "BILIBILI_COOKIEFILE"):
        raw = (os.getenv(key) or "").strip()
        if not raw:
            continue
        path = _resolve_cookiefile_path(raw)
        if path:
            return path
    return None


def _js_runtimes_from_env() -> dict[str, Any]:
    """YouTube n-challenge 需要 JS runtime + yt-dlp-ejs。默认启用 node。

    YTDLP_JS_RUNTIMES=node|deno|node,deno；设 0/off 关闭。
    """
    raw = (os.getenv("YTDLP_JS_RUNTIMES") or "node").strip()
    if raw.lower() in ("0", "off", "false", "none", ""):
        return {}
    runtimes: dict[str, dict] = {}
    for part in raw.split(","):
        name = part.strip().lower()
        if name in ("node", "deno", "bun"):
            runtimes[name] = {}
    return {"js_runtimes": runtimes} if runtimes else {}


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
    elif "instagram.com" in u or "cdninstagram.com" in u or "fbcdn.net" in u:
        headers["Referer"] = "https://www.instagram.com/"
    return headers


def _is_youtube_url(url: str) -> bool:
    u = (url or "").lower()
    return any(
        x in u
        for x in (
            "youtube.com",
            "youtu.be",
            "youtube-nocookie.com",
            "music.youtube.com",
        )
    ) or u.startswith("ytsearch")


def _is_instagram_url(url: str) -> bool:
    u = (url or "").lower()
    return "instagram.com" in u or "instagr.am" in u


def _is_bilibili_url(url: str) -> bool:
    u = (url or "").lower()
    return "bilibili.com" in u or "b23.tv" in u


def _is_tiktok_url(url: str) -> bool:
    u = (url or "").lower()
    return any(
        x in u
        for x in (
            "tiktok.com",
            "tiktokv.com",
            "vm.tiktok.com",
            "vt.tiktok.com",
        )
    )


def _ytdlp_base_opts(url: str) -> dict[str, Any]:
    opts: dict[str, Any] = {"http_headers": _http_headers_for_url(url)}
    # 无 JS runtime 时 web 客户端常只剩 storyboard；android 仍可拿到可播格式。
    # 注意：带 cookiefile 时 yt-dlp 会跳过 android，故 YouTube 不用全局 B 站 cookies，
    # 并必须启用 js_runtimes（node/deno）+ yt-dlp-ejs 解 n-challenge。
    if _is_youtube_url(url):
        opts["extractor_args"] = {
            "youtube": {
                "player_client": ["android", "web"],
            }
        }
        opts.update(_js_runtimes_from_env())
        # YouTube 可选单独 Cookie（勿复用 bilibili_cookies.txt）
        yt_cookie = (os.getenv("YOUTUBE_COOKIEFILE") or "").strip()
        if yt_cookie:
            path = _resolve_cookiefile_path(yt_cookie)
            if path:
                opts["cookiefile"] = path
                return opts
        # 无独立 cookie 文件时，可回退浏览器 Cookie（本地调试 / 生产需自行导出文件）
        cfb = _cookies_from_browser_from_env()
        if cfb:
            opts["cookiesfrombrowser"] = cfb
            return opts
        imp = _impersonate_from_env()
        if imp:
            opts["impersonate"] = imp
        return opts

    # Instagram：登录态 Cookie 常必需（empty media response）；勿复用 B 站 cookies
    if _is_instagram_url(url):
        ig_cookie = (os.getenv("INSTAGRAM_COOKIEFILE") or "").strip()
        if ig_cookie:
            path = _resolve_cookiefile_path(ig_cookie)
            if path:
                opts["cookiefile"] = path
                return opts
        cfb = _cookies_from_browser_from_env()
        if cfb:
            opts["cookiesfrombrowser"] = cfb
            return opts
        imp = _impersonate_from_env()
        if imp:
            opts["impersonate"] = imp
        return opts

    # TikTok：HK/受限出口常被重定向到 /hk/about；勿套用含 tiktok 域的 B 站 cookies
    if _is_tiktok_url(url):
        tt_cookie = (os.getenv("TIKTOK_COOKIEFILE") or "").strip()
        if tt_cookie:
            path = _resolve_cookiefile_path(tt_cookie)
            if path:
                opts["cookiefile"] = path
        imp = _impersonate_from_env()
        if imp:
            opts["impersonate"] = imp
        return opts

    imp = _impersonate_from_env()
    if imp:
        opts["impersonate"] = imp
    # 全局 YTDLP_COOKIEFILE 主要用于 B 站 412；勿套到已单独处理的站点
    if _is_bilibili_url(url):
        cf = _cookiefile_from_env()
        if cf:
            opts["cookiefile"] = cf
            return opts
    else:
        # 其它站点：仅在未配置专用 cookie 时可选浏览器 Cookie
        pass
    cfb = _cookies_from_browser_from_env()
    if cfb:
        opts["cookiesfrombrowser"] = cfb
        return opts
    if not _is_bilibili_url(url):
        cf = _cookiefile_from_env()
        if cf:
            opts["cookiefile"] = cf
    return opts


def _format_candidates(format_id: str, *, has_ffmpeg: bool) -> list[str]:
    """生成格式回退链，缓解 YouTube「Requested format is not available」。"""
    primary = (format_id or "").strip() or "bestvideo+bestaudio/best"
    if not has_ffmpeg and "+" in primary:
        primary = "best"
    seen: set[str] = set()
    out: list[str] = []
    for cand in (
        primary,
        "bestvideo*+bestaudio/best",
        "best[ext=mp4]/best",
        "18/22/best",
        "best",
        "worst",
    ):
        c = (cand or "").strip()
        if not c or c in seen:
            continue
        if not has_ffmpeg and "+" in c:
            continue
        seen.add(c)
        out.append(c)
    return out


def _parse_socket_timeout() -> float:
    raw = (os.getenv("YTDLP_PARSE_SOCKET_TIMEOUT") or "").strip()
    if not raw:
        return 30.0
    try:
        return max(5.0, min(120.0, float(raw)))
    except ValueError:
        return 30.0


def _parse_download_retries() -> int:
    raw = (os.getenv("YTDLP_DOWNLOAD_RETRIES") or "").strip()
    if not raw:
        return 5
    try:
        return max(1, min(20, int(raw)))
    except ValueError:
        return 5


def _parse_fragment_retries() -> int:
    raw = (os.getenv("YTDLP_FRAGMENT_RETRIES") or "").strip()
    if not raw:
        return 5
    try:
        return max(1, min(20, int(raw)))
    except ValueError:
        return 5


def _is_transient_download_error(exc: BaseException) -> bool:
    """代理断开 / SSL EOF 等瞬时网络错误，适合整次下载重试。"""
    msg = str(exc).lower()
    needles = (
        "unexpected_eof_while_reading",
        "eof occurred in violation of protocol",
        "unable to connect to proxy",
        "remote end closed connection",
        "remotedisconnected",
        "connection reset",
        "connection aborted",
        "timed out",
        "timeout",
        "temporary failure in name resolution",
        "network is unreachable",
        "ssl: unexpected_eof",
        "ssleoferror",
    )
    return any(n in msg for n in needles)


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
        # Windows 非法字符 + NBSP/控制符；避免 Facebook 等长标题直接写入 outtmpl 失败
        cleaned = (name or "video").replace("\xa0", " ").replace("\u3000", " ")
        cleaned = re.sub(r'[\x00-\x1f\x7f]', "", cleaned)
        cleaned = re.sub(r'[\\/*?:"<>|]', "_", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
        return (cleaned[:180] or "video")

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
        import time

        target_dir = out_dir if out_dir else self.DOWNLOAD_DIR
        os.makedirs(target_dir, exist_ok=True)

        last_err: Optional[Exception] = None
        info = None
        prepared_path = ""
        dl_retries = _parse_download_retries()
        frag_retries = _parse_fragment_retries()
        # 整次下载外层重试：覆盖代理断开 / SSL EOF（yt-dlp 内部 retries 有时不够）
        attempt_max = max(1, min(5, int(os.getenv("YTDLP_DOWNLOAD_ATTEMPTS", "3") or "3")))

        for attempt in range(1, attempt_max + 1):
            info = None
            prepared_path = ""
            for fmt in _format_candidates(format_id, has_ffmpeg=self.has_ffmpeg):
                ydl_opts = {
                    **_ytdlp_base_opts(url),
                    "format": fmt,
                    # 先用 id 落盘，避免 title 含 Windows 非法字符导致 Errno 22
                    "outtmpl": os.path.join(target_dir, "%(id)s.%(ext)s"),
                    "windowsfilenames": True,
                    "quiet": True,
                    "no_warnings": True,
                    "noplaylist": True,
                    "socket_timeout": max(30.0, _parse_socket_timeout()),
                    "retries": dl_retries,
                    "fragment_retries": frag_retries,
                    "extractor_retries": max(3, dl_retries),
                    "file_access_retries": 3,
                }
                if self.has_ffmpeg:
                    ydl_opts["ffmpeg_location"] = self.ffmpeg_path
                    ydl_opts["merge_output_format"] = "mp4"
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(url, download=True)
                        if info:
                            prepared_path = ydl.prepare_filename(info)
                    if info:
                        break
                except Exception as e:
                    last_err = e
                    msg = str(e).lower()
                    if "requested format is not available" in msg or "only images are available" in msg:
                        continue
                    if _is_transient_download_error(e) and attempt < attempt_max:
                        break
                    raise
            if info:
                break
            if last_err and _is_transient_download_error(last_err) and attempt < attempt_max:
                time.sleep(min(2 ** attempt, 15))
                continue
            break

        if not info:
            raise ValueError(str(last_err) if last_err else "下载失败")

        title = self._sanitize_filename(info.get("title", "video"))
        ext = info.get("ext", "mp4")
        filename = f"{title}.{ext}"
        filepath = os.path.join(target_dir, filename)

        if not os.path.exists(filepath):
            if prepared_path and os.path.exists(prepared_path):
                filepath = prepared_path
                filename = os.path.basename(prepared_path)
            else:
                for f in os.listdir(target_dir):
                    if title in f:
                        filepath = os.path.join(target_dir, f)
                        filename = f
                        break
        platform_title = info.get("title", "video")
        try:
            from video_title import apply_content_filename, content_title_enabled

            if content_title_enabled():
                renamed = apply_content_filename(filepath, url, platform_title=platform_title)
                return {
                    "filepath": renamed["filepath"],
                    "filename": renamed["filename"],
                    "title": renamed["title"],
                    "ext": renamed["ext"],
                }
        except Exception:
            pass

        return {
            "filepath": filepath,
            "filename": filename,
            "title": platform_title,
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
