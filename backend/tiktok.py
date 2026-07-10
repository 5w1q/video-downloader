"""TikTok 单链解析 / 下载。

本机代理出口常为香港时，TikTok 网页会重定向到 /hk/about，yt-dlp 无法取到
UNIVERSAL_DATA（表现为 Unexpected response from webpage request）。
因此优先走已配置的 ScrapeCreators Video Info API，拿到 CDN 直链后再本地下载。
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
import requests

_API_URL = "https://api.scrapecreators.com/v2/tiktok/video"
_CACHE_TTL_SEC = 300
_aweme_cache: dict[str, tuple[float, dict[str, Any]]] = {}

_TT_HOST_MARKERS = (
    "tiktok.com",
    "tiktokv.com",
    "vm.tiktok.com",
    "vt.tiktok.com",
    "m.tiktok.com",
)

_DOWNLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.tiktok.com/",
    "Accept": "*/*",
}


def is_tiktok_url(url: str) -> bool:
    try:
        host = urlparse((url or "").strip()).netloc.lower()
    except Exception:
        return False
    return any(m in host for m in _TT_HOST_MARKERS)


def _api_key() -> str:
    return (os.getenv("SCRAPECREATORS_API_KEY") or "").strip()


def _region() -> Optional[str]:
    raw = (os.getenv("TIKTOK_SCRAPECREATORS_REGION") or "").strip().upper()
    return raw or None


def _video_id_from_url(url: str) -> str:
    m = re.search(r"/video/(\d+)", url or "")
    if m:
        return m.group(1)
    m = re.search(r"(\d{15,})", url or "")
    return m.group(1) if m else ""


def _cache_get(key: str) -> Optional[dict[str, Any]]:
    item = _aweme_cache.get(key)
    if not item:
        return None
    ts, data = item
    if time.time() - ts > _CACHE_TTL_SEC:
        _aweme_cache.pop(key, None)
        return None
    return data


def _cache_set(key: str, data: dict[str, Any]) -> None:
    _aweme_cache[key] = (time.time(), data)


def _first_url(addr: Any) -> str:
    if not isinstance(addr, dict):
        return ""
    for u in addr.get("url_list") or []:
        s = (u or "").strip()
        if s.startswith("http"):
            return s
    return ""


def _pick_media_url(video: dict[str, Any]) -> str:
    """优先无水印；否则 play_addr / bit_rate。"""
    for key in (
        "download_no_watermark_addr",
        "play_addr_h264",
        "play_addr",
        "download_addr",
    ):
        u = _first_url(video.get(key))
        if u:
            return u
    for br in video.get("bit_rate") or []:
        if not isinstance(br, dict):
            continue
        u = _first_url(br.get("play_addr"))
        if u:
            return u
    return ""


def _fmt_duration(seconds: Optional[int]) -> str:
    if not seconds:
        return "00:00"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _sanitize_filename(name: str) -> str:
    cleaned = (name or "tiktok").replace("\xa0", " ").replace("\u3000", " ")
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", cleaned)
    cleaned = re.sub(r'[\\/*?:"<>|\n\r\t#@]', "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_. ")
    return (cleaned[:80] or "tiktok")


class TikTokParser:
    """TikTok 解析器：ScrapeCreators → CDN 直链下载。"""

    def __init__(self, download_dir: str = "downloads"):
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = httpx.Timeout(connect=30.0, read=90.0, write=30.0, pool=30.0)
        self.max_retries = 3

    def _fetch_aweme(self, url: str) -> dict[str, Any]:
        key = _api_key()
        if not key:
            raise ValueError(
                "TikTok 解析需要 SCRAPECREATORS_API_KEY（当前代理出口常被 TikTok "
                "重定向到 /hk/about，yt-dlp 网页提取不可用）。请在 backend/.env 配置后重启。"
            )

        cache_key = _video_id_from_url(url) or url.strip()
        cached = _cache_get(cache_key)
        if cached:
            return cached

        params: dict[str, Any] = {"url": url, "trim": "true"}
        region = _region()
        if region:
            params["region"] = region

        last_err: Optional[Exception] = None
        resp: Optional[httpx.Response] = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.get(
                        _API_URL,
                        params=params,
                        headers={"x-api-key": key, "Accept": "application/json"},
                    )
                break
            except httpx.TimeoutException as e:
                raise ValueError("TikTok 解析超时（ScrapeCreators 未在时限内返回）") from e
            except httpx.HTTPError as e:
                last_err = e
                if attempt >= 2:
                    raise ValueError(f"TikTok 解析请求失败: {e}") from e
        if resp is None:
            raise ValueError(f"TikTok 解析请求失败: {last_err}")

        if resp.status_code in (401, 403):
            raise ValueError("SCRAPECREATORS_API_KEY 无效或已过期")
        if resp.status_code == 402:
            raise ValueError("ScrapeCreators 额度不足，请充值后重试")
        if resp.status_code == 404:
            raise ValueError("未找到该 TikTok 视频（可能已删除或设为私密）")
        if resp.status_code >= 400:
            detail = (resp.text or "")[:400]
            raise ValueError(f"ScrapeCreators 失败 (HTTP {resp.status_code}): {detail}")

        try:
            data = resp.json()
        except Exception as e:
            raise ValueError("ScrapeCreators 返回了无法解析的响应") from e

        if not isinstance(data, dict) or not data.get("success", True):
            msg = ""
            if isinstance(data, dict):
                msg = str(data.get("message") or data.get("error") or "")[:200]
            raise ValueError(msg or "ScrapeCreators 未返回有效 TikTok 数据")

        aweme = data.get("aweme_detail")
        if not isinstance(aweme, dict) or not aweme:
            raise ValueError("ScrapeCreators 未返回 aweme_detail")

        _cache_set(cache_key, aweme)
        vid = str(aweme.get("aweme_id") or "")
        if vid and vid != cache_key:
            _cache_set(vid, aweme)
        return aweme

    def _build_result(self, aweme: dict[str, Any], page_url: str) -> dict[str, Any]:
        video = aweme.get("video") if isinstance(aweme.get("video"), dict) else {}
        author = aweme.get("author") if isinstance(aweme.get("author"), dict) else {}
        stats = aweme.get("statistics") if isinstance(aweme.get("statistics"), dict) else {}

        video_id = str(aweme.get("aweme_id") or _video_id_from_url(page_url) or "")
        title = (aweme.get("desc") or "").strip() or f"TikTok_{video_id}"
        media_url = _pick_media_url(video)
        if not media_url:
            raise ValueError("未找到 TikTok 可下载视频地址")

        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
        duration_ms = int(video.get("duration") or 0)
        duration_sec = duration_ms // 1000 if duration_ms > 1000 else duration_ms

        cover = ""
        for key in ("cover", "origin_cover", "dynamic_cover", "ai_dynamic_cover"):
            cover = _first_url(video.get(key))
            if cover:
                break

        formats = [
            {
                "format_id": "tiktok_sc",
                "ext": "mp4",
                "resolution": f"{width}x{height}" if width and height else "原始",
                "height": height or 720,
                "filesize": None,
                "filesize_approx": None,
                "vcodec": "h264",
                "acodec": "aac",
                "has_audio": True,
                "label": f"MP4 ({height}p)" if height else "MP4 (原始画质)",
                "_direct_url": media_url,
            }
        ]

        return {
            "id": video_id,
            "title": title,
            "thumbnail": cover,
            "duration": duration_sec,
            "duration_string": _fmt_duration(duration_sec),
            "uploader": author.get("nickname") or author.get("unique_id") or "TikTok",
            "platform": "tiktok",
            "view_count": stats.get("play_count") or stats.get("digg_count"),
            "upload_date": "",
            "description": title[:200],
            "formats": formats,
            "subtitles": [],
            "automatic_captions": [],
        }

    def parse(self, url: str) -> dict[str, Any]:
        aweme = self._fetch_aweme(url)
        return self._build_result(aweme, url)

    def _download_file(self, media_url: str, filepath: Path) -> None:
        for attempt in range(self.max_retries):
            try:
                with requests.get(
                    media_url,
                    headers=_DOWNLOAD_HEADERS,
                    stream=True,
                    timeout=(15, 120),
                    allow_redirects=True,
                ) as resp:
                    resp.raise_for_status()
                    temp_path = filepath.with_suffix(filepath.suffix + ".part")
                    with temp_path.open("wb") as f:
                        for chunk in resp.iter_content(chunk_size=64 * 1024):
                            if chunk:
                                f.write(chunk)
                    temp_path.replace(filepath)
                return
            except Exception as e:
                if attempt >= self.max_retries - 1:
                    raise ValueError(f"TikTok 文件下载失败: {e}") from e
                time.sleep(1 * (2**attempt))

    def download(self, url: str, out_dir: Optional[str | Path] = None) -> dict[str, Any]:
        dest = Path(out_dir) if out_dir is not None else self.download_dir
        dest.mkdir(parents=True, exist_ok=True)

        aweme = self._fetch_aweme(url)
        result = self._build_result(aweme, url)
        media_url = (result["formats"][0] or {}).get("_direct_url") or ""
        if not media_url:
            raise ValueError("未找到 TikTok 可下载视频地址")

        video_id = result.get("id") or _video_id_from_url(url) or "tiktok"
        safe_title = _sanitize_filename(result.get("title") or f"tiktok_{video_id}")
        filename = f"{safe_title}.mp4"
        filepath = dest / filename
        # 同名冲突时用 id 区分
        if filepath.exists():
            filename = f"{safe_title}_{video_id}.mp4"
            filepath = dest / filename

        self._download_file(media_url, filepath)

        try:
            from video_title import apply_content_filename, content_title_enabled

            if content_title_enabled():
                renamed = apply_content_filename(
                    str(filepath), url, platform_title=result.get("title") or ""
                )
                return {
                    "filepath": renamed["filepath"],
                    "filename": renamed["filename"],
                    "title": renamed["title"],
                    "ext": renamed["ext"],
                }
        except Exception:
            pass

        return {
            "filepath": str(filepath),
            "filename": filename,
            "title": result.get("title") or safe_title,
            "ext": ".mp4",
        }
