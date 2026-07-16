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


def _is_live_info(info: Any) -> bool:
    """判断 yt-dlp info 是否为（正在/即将/刚结束仍处理中的）直播，需拒绝下载。

    直播用 bestvideo+bestaudio 下载不会结束，会拖垮批次并触发上游超时。
    live_status='was_live' 为已结束、已转普通录像，可正常下载，不拦。
    """
    if not isinstance(info, dict):
        return False
    status = (info.get("live_status") or "").strip().lower()
    if status in ("is_live", "is_upcoming", "post_live"):
        return True
    if info.get("is_live") is True and status not in ("was_live", "not_live"):
        return True
    return False


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


def _translate_technical_error(low: str) -> Optional[str]:
    """匹配常见英文/技术报错，返回中文；未命中返回 None。"""
    # —— 网络 / SSL / 代理（搜索与下载均常见）——
    if (
        "unexpected_eof_while_reading" in low
        or "eof occurred in violation of protocol" in low
        or "ssl: unexpected_eof" in low
        or "ssleoferror" in low
    ):
        return "SSL 连接异常中断，请检查网络或代理后重试。"
    if "certificate_verify_failed" in low or (
        "ssl" in low and ("certificate" in low or "cert verify" in low)
    ):
        return "SSL 证书校验失败，请检查网络或代理配置后重试。"
    if "ssl" in low and (
        "handshake" in low or "wrong_version_number" in low or "protocol" in low
    ):
        return "SSL 握手失败，请检查网络或代理后重试。"
    if (
        "connection reset" in low
        or "connection aborted" in low
        or "remote end closed connection" in low
        or "remotedisconnected" in low
        or "server disconnected" in low
    ):
        return "连接被对端关闭，请检查网络或代理后重试。"
    if "connection refused" in low:
        return "连接被拒绝，请检查网络或目标服务是否可用。"
    if (
        "name or service not known" in low
        or "temporary failure in name resolution" in low
        or "getaddrinfo failed" in low
        or "nodename nor servname" in low
    ):
        return "域名解析失败，请检查网络或 DNS 后重试。"
    if "network is unreachable" in low or "no route to host" in low:
        return "网络不可达，请检查网络连接后重试。"
    if "unable to connect to proxy" in low or "proxyerror" in low or "proxy error" in low:
        return "代理连接失败，请检查 HTTPS_PROXY / HTTP_PROXY 配置后重试。"
    if "timed out" in low or "timeout" in low or "time out" in low:
        return "请求超时，请检查网络或代理后重试。"
    if "connecterror" in low or "connection error" in low:
        return "无法建立网络连接，请检查网络或代理后重试。"
    if "readerror" in low or "writeerror" in low:
        return "网络读写失败，请稍后重试。"
    if "remoteprotocolerror" in low or "incomplete message" in low:
        return "远程服务响应异常，请稍后重试。"

    # —— Cookie / 登录通用 ——
    if "sign in to confirm" in low or "not a bot" in low:
        return (
            "YouTube 要求登录验证（bot 检测）。请配置 YOUTUBE_COOKIEFILE"
            "（Netscape cookies.txt，勿复用 B 站 Cookie），并确认出口代理可用后重试。"
        )
    if "failed to load cookies" in low:
        return (
            "无法加载浏览器 Cookie。请改为导出 cookies.txt 并设置对应 Cookie 环境变量，"
            "或关闭占用 Cookie 数据库的浏览器后重试。"
        )

    # —— X / Twitter：无视频 ——
    if "no video could be found in this tweet" in low:
        return (
            "该帖未包含可下载的视频（可能是纯文字、仅图片或引用/转发帖）。"
            "请换一条带原生视频的 X 链接后重试。"
        )

    # —— Instagram：无视频 / 风控 ——
    if "there is no video in this post" in low:
        return (
            "该 Instagram 帖未包含可下载的视频（可能是纯图片或图文轮播）。"
            "请换一条 Reel / 视频帖后重试。"
        )
    if "empty media response" in low:
        return (
            "Instagram 未返回媒体内容（帖子可能需登录、已删除、或匿名访问被限流）。"
            "请配置 INSTAGRAM_COOKIEFILE 后重试。"
        )
    if "rate-limit" in low or "rate limit" in low:
        return "访问过于频繁被限流。请稍后再试，或配置有效登录 Cookie 后重试。"
    if "redirected to the login page" in low or (
        "login required" in low and "instagram" in low
    ):
        return (
            "Instagram 要求登录后才能访问该内容。"
            "请配置 INSTAGRAM_COOKIEFILE（Netscape cookies.txt）后重试。"
        )
    if "only available for registered users" in low or "who follow this account" in low:
        return "该 Instagram 内容仅对已关注用户可见，当前 Cookie 无法访问。"
    if "unable to extract video url" in low and "instagram" in low:
        return "无法从该 Instagram 帖提取视频地址，请确认链接有效并配置 Cookie 后重试。"

    # —— YouTube：不可播 / 无视频 ——
    if "private video" in low or "this video is private" in low:
        return "该 YouTube 视频为私密视频，无法下载。"
    if "members-only" in low or "join this channel to get access" in low:
        return "该 YouTube 视频为会员专享，无法下载。"
    if "confirm your age" in low or "age-restricted" in low or "age restricted" in low:
        return (
            "该 YouTube 视频有年龄限制。请配置含已登录账号的 YOUTUBE_COOKIEFILE 后重试。"
        )
    if "not made this video available in your country" in low or (
        "not available in your country" in low
    ):
        return "该 YouTube 视频在当前地区不可用，请更换代理出口后重试。"
    if "video unavailable" in low or "this video is no longer available" in low:
        return "该 YouTube 视频不可用（可能已删除、设为私密或地区限制）。"
    if "has been removed" in low and ("youtube" in low or "video" in low):
        return "该视频已被删除，无法下载。"
    if "requested format is not available" in low:
        return "所选清晰度/格式不可用。请改用「最佳」或其它格式后重试。"

    # —— 通用：无视频 / 仅图片 / 链接无效（批量下载也走这里）——
    if "only images are available" in low:
        return "该链接只有图片，没有可下载的视频。"
    if (
        "no video formats" in low
        or "no formats found" in low
        or "there is no video" in low
        or "no video could be found" in low
    ):
        return "未找到可下载的视频（可能是纯文字、仅图片或内容已失效）。"
    if "unsupported url" in low:
        return "不支持该链接，或无法识别为可下载的视频地址。"
    if "unable to extract" in low:
        return "无法解析该链接中的视频信息，请确认链接有效后重试。"
    if "http error 404" in low or "404: not found" in low:
        return "链接不存在或内容已删除（HTTP 404）。"
    if "http error 403" in low or "403: forbidden" in low:
        return "访问被拒绝（HTTP 403），可能需要登录 Cookie 或更换代理。"
    if "http error 429" in low or "too many requests" in low:
        return "请求过于频繁（HTTP 429），请稍后重试。"
    if "http error 5" in low or re.search(r"\b50[0-9]\b", low):
        if "http" in low or "status" in low or "apify" in low:
            return "远程服务暂时不可用，请稍后重试。"
    return None


def friendly_download_error(exc: BaseException | str) -> str:
    """将常见 yt-dlp / 网络 / 平台英文报错转成中文（单条 / 批量 / 搜索共用）。"""
    msg = (str(exc) if not isinstance(exc, str) else exc).strip()
    if not msg:
        return "下载失败"

    low = msg.lower()
    translated = _translate_technical_error(low)
    if translated:
        # 保留已有中文业务前缀，例如「Instagram 搜索请求失败：…」
        m = re.match(r"^([^:：]{1,80})[:：]\s*", msg)
        if m and any("\u4e00" <= ch <= "\u9fff" for ch in m.group(1)):
            prefix = m.group(1).strip().rstrip(":：") + "："
            # 避免前缀本身已是完整中文提示时重复拼接
            if translated not in prefix:
                return prefix + translated
        return translated

    # 中文前缀 + 未识别英文尾：去掉英文，避免控制台直接甩 SSL/堆栈
    m = re.match(r"^([^:：]{1,80})[:：]\s*(.+)$", msg, re.DOTALL)
    if m and any("\u4e00" <= ch <= "\u9fff" for ch in m.group(1)):
        prefix = m.group(1).strip().rstrip(":：") + "："
        tail = m.group(2).strip()
        tail_zh = any("\u4e00" <= ch <= "\u9fff" for ch in tail)
        if not tail_zh and re.search(r"[A-Za-z]{3,}", tail):
            return prefix + "请稍后重试。"
        return msg

    # 纯中文业务文案
    if any("\u4e00" <= ch <= "\u9fff" for ch in msg):
        return msg

    # 未知英文：不把原始堆栈甩给用户
    if re.search(r"[A-Za-z]{4,}", msg):
        return "操作失败，请稍后重试。"
    return msg


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

    def download_video(
        self,
        url: str,
        format_id: str,
        out_dir: Optional[str] = None,
        *,
        platform_title: Optional[str] = None,
        title_url: Optional[str] = None,
    ) -> dict:
        """下载视频到服务器目录，返回文件路径和元数据。out_dir 为空时使用默认 DOWNLOAD_DIR。

        platform_title: 搜索/调用方已知标题，优先于 yt-dlp title 作为命名回退。
        title_url: 字幕/AI 命名用 URL（默认 = 下载 url；CDN 直下时可传页面 URL）。
        """
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

        # 拒绝直播：直播用 bestvideo+bestaudio 下载不会结束，会拖垮批次并触发上游超时。
        # match_filter 在实际下载前判定，返回非空即跳过该条（不产生文件）。
        live_rejected = False

        def _reject_live(info_dict, *, incomplete=False):
            nonlocal live_rejected
            if _is_live_info(info_dict):
                live_rejected = True
                return "直播流不下载"
            return None

        for attempt in range(1, attempt_max + 1):
            info = None
            prepared_path = ""
            for fmt in _format_candidates(format_id, has_ffmpeg=self.has_ffmpeg):
                ydl_opts = {
                    **_ytdlp_base_opts(url),
                    "format": fmt,
                    "match_filter": _reject_live,
                    # 先用 id 落盘，避免 title 含 Windows 非法字符导致 Errno 22。
                    # id 截断到 50 字符：CDN 直链（如 IG fbcdn）的 id 会带完整查询串，
                    # 不截断会让路径超长触发 Windows Errno 22（Invalid argument）。
                    "outtmpl": os.path.join(target_dir, "%(id).50s.%(ext)s"),
                    "trim_file_name": 120,
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
                    if live_rejected:
                        break
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
            if live_rejected:
                break
            if last_err and _is_transient_download_error(last_err) and attempt < attempt_max:
                time.sleep(min(2 ** attempt, 15))
                continue
            break

        if live_rejected and not info:
            raise ValueError("该链接为直播，已跳过（不下载直播）。")

        if not info:
            raise ValueError(
                friendly_download_error(last_err) if last_err else "下载失败"
            )

        yt_title = info.get("title", "video")
        effective_title = (platform_title or "").strip() or yt_title
        rename_url = (title_url or url).strip() or url

        # 单帖多视频（如 X 多视频推文）会被 yt-dlp 当作 playlist 返回（noplaylist 对
        # 这种「同一页面多媒体」无效），需遍历 entries 把每个视频都取回。
        entries = info.get("entries")
        entry_list = [info] if entries is None else [e for e in entries if e]

        located: list[str] = []
        seen_paths: set[str] = set()
        for entry in entry_list:
            # playlist 场景 prepared_path 是列表级名（.NA），对子项无意义，仅单视频时用
            prep = prepared_path if entries is None else ""
            fp = self._locate_downloaded_file(entry, target_dir, prep)
            if not fp:
                continue
            key = os.path.normcase(os.path.abspath(fp))
            if key not in seen_paths:
                seen_paths.add(key)
                located.append(fp)

        if not located:
            raise ValueError("下载完成但未找到输出文件")

        # 命名标题按整帖计算一次，多个视频共用（_unique_filepath 自动加 _2/_3 去重），
        # 避免为每个子视频重复调用 LLM。
        try:
            from video_title import generate_download_title

            shared_title = generate_download_title(
                rename_url, platform_title=effective_title
            )
        except Exception:
            shared_title = ""

        files_out = [
            self._finalize_named_file(fp, shared_title, effective_title)
            for fp in located
        ]

        first = files_out[0]
        return {
            "filepath": first["filepath"],
            "filename": first["filename"],
            "title": first["title"],
            "ext": first["ext"],
            "files": files_out,
        }

    @staticmethod
    def _locate_downloaded_file(
        entry: dict, target_dir: str, prepared_path: str = ""
    ) -> str:
        """定位单个 entry 实际落盘的媒体文件；找不到返回空串。

        优先用 yt-dlp 记录的真实路径（requested_downloads/filepath），再回退到
        按 id 拼名与目录扫描，兼顾单视频与多视频（playlist）两种返回结构。
        """
        candidates: list[str] = []
        for rd in (entry.get("requested_downloads") or []):
            fp = rd.get("filepath")
            if fp:
                candidates.append(fp)
        fp = entry.get("filepath")
        if fp:
            candidates.append(fp)
        if prepared_path:
            candidates.append(prepared_path)
            # merge_output_format=mp4 时 prepare 可能仍带源后缀
            stem_prep, _ = os.path.splitext(prepared_path)
            candidates.append(stem_prep + ".mp4")
        vid = str(entry.get("id") or "").strip()
        ext = (entry.get("ext") or "mp4").strip() or "mp4"
        if vid:
            for e in (ext, "mp4", "webm", "mkv", "m4a", "mov"):
                candidates.append(os.path.join(target_dir, f"{vid}.{e}"))
        for c in candidates:
            if c and os.path.isfile(c):
                return c
        # 目录扫描兜底：id 前缀（outtmpl 固定用 %(id).50s，落盘必以 id 开头）
        if vid:
            try:
                for f in os.listdir(target_dir):
                    low = f.lower()
                    if not low.endswith(
                        (".mp4", ".webm", ".mkv", ".m4a", ".mov", ".opus", ".mp3")
                    ):
                        continue
                    if f == f"{vid}{os.path.splitext(f)[1]}" or f.startswith(f"{vid}."):
                        return os.path.join(target_dir, f)
            except OSError:
                pass
        return ""

    def _finalize_named_file(
        self, filepath: str, shared_title: str, effective_title: str
    ) -> dict:
        """把落盘文件重命名为内容标题；失败回退平台标题，最终回退裸文件名。"""
        from pathlib import Path as _Path

        ext = os.path.splitext(filepath)[1].lstrip(".") or "mp4"

        title = (shared_title or "").strip()
        if title and title != "video":
            try:
                from video_title import _unique_filepath

                src = _Path(filepath)
                dest = _unique_filepath(src.parent, title, ext)
                if dest.resolve() != src.resolve():
                    src.rename(dest)
                    src = dest
                return {
                    "filepath": str(src),
                    "filename": src.name,
                    "title": title,
                    "ext": ext,
                }
            except Exception:
                pass

        # 内容标题不可用时，至少改成平台标题 ≤30，禁止对外长期暴露裸 id
        try:
            from video_title import _unique_filepath, sanitize_download_basename

            stem = sanitize_download_basename(effective_title)
            src = _Path(filepath)
            dest = _unique_filepath(src.parent, stem, ext)
            if dest.resolve() != src.resolve():
                src.rename(dest)
                src = dest
            return {
                "filepath": str(src),
                "filename": src.name,
                "title": stem,
                "ext": ext,
            }
        except Exception:
            pass

        return {
            "filepath": filepath,
            "filename": os.path.basename(filepath),
            "title": effective_title,
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
