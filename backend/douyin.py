"""
抖音视频解析与下载模块
基于公开 API，无需 Cookie 和登录
原理：短链接重定向 → 提取 video_id → 公开 API 获取元数据 → 无水印播放地址
"""

import base64
import json
import hashlib
import os
import re
import time
import logging
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

import requests

logger = logging.getLogger("douyin")

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "Referer": "https://www.douyin.com/",
}

MOBILE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
        "Mobile/15E148 Safari/604.1"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.douyin.com/",
}

_URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)


def normalize_media_url(text: str) -> str:
    """去掉不可见字符；无协议时补 https://（分享文案里常见 v.douyin.com/...）"""
    t = (text or "").strip()
    for ch in ("\u200b", "\u200c", "\u200d", "\ufeff", "\u2060"):
        t = t.replace(ch, "")
    if not t:
        return t
    low = t.lower()
    if not low.startswith(("http://", "https://")):
        t = "https://" + t.lstrip("/")
    return t


def is_douyin_url(url: str) -> bool:
    """判断是否为抖音链接"""
    douyin_domains = [
        "douyin.com",
        "douyin.cn",
        "iesdouyin.com",
        "v.douyin.com",
        "www.douyin.com",
        "m.douyin.com",
    ]
    try:
        host = urlparse(normalize_media_url(url)).netloc.lower()
        return any(d in host for d in douyin_domains)
    except Exception:
        return False


class DouyinParser:
    """抖音视频解析器，无需 Cookie"""

    API_URL = "https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/"

    def __init__(self, download_dir: str = "downloads"):
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self.timeout = (15, 45)
        self.max_retries = 3

    def _with_text(self, url: str) -> str:
        return normalize_media_url(url)

    def get_item_info(self, url: str) -> dict:
        """获取抖音作品原始元数据（供字幕提取等复用解析流程）"""
        url = self._with_text(url)
        share_url = self._extract_url(url)
        resolved_url = self._resolve_redirect(share_url)
        video_id = self._extract_video_id(resolved_url)
        return self._fetch_item_info(video_id, resolved_url)

    def parse(self, url: str) -> dict:
        """解析抖音视频信息，返回统一格式"""
        url = self._with_text(url)
        share_url = self._extract_url(url)
        resolved_url = self._resolve_redirect(share_url)
        video_id = self._extract_video_id(resolved_url)

        item_info = self._fetch_item_info(video_id, resolved_url)
        return self._build_result(item_info, video_id)

    def download(self, url: str, mode: str = "video") -> dict:
        """下载抖音视频，返回文件路径"""
        url = self._with_text(url)
        share_url = self._extract_url(url)
        resolved_url = self._resolve_redirect(share_url)
        video_id = self._extract_video_id(resolved_url)

        item_info = self._fetch_item_info(video_id, resolved_url)
        media_url = self._get_media_url(item_info, mode)
        title = item_info.get("desc") or f"douyin_{video_id}"
        safe_title = re.sub(r'[\\/*?:"<>|\n\r\t#@]', "_", title).strip("_. ")[:60]
        safe_title = re.sub(r'_+', '_', safe_title)
        if not safe_title:
            safe_title = f"douyin_{video_id}"

        ext = ".mp4" if mode == "video" else ".mp3"
        filename = f"{safe_title}{ext}"
        filepath = self.download_dir / filename

        self._download_file(media_url, filepath)

        return {
            "filepath": str(filepath),
            "filename": filename,
            "title": title,
            "ext": ext.lstrip("."),
        }

    def _extract_url(self, text: str) -> str:
        match = _URL_PATTERN.search(text)
        if not match:
            raise ValueError("未找到有效的抖音链接")
        candidate = match.group(0).strip().strip('"').strip("'")
        return candidate.rstrip(").,;!?")

    def _resolve_redirect(self, share_url: str) -> str:
        """解析短链接重定向"""
        for attempt in range(self.max_retries):
            try:
                resp = self.session.get(
                    share_url, timeout=self.timeout,
                    allow_redirects=True, headers=DEFAULT_HEADERS,
                )
                resp.raise_for_status()
                return resp.url
            except requests.RequestException as e:
                if attempt == self.max_retries - 1:
                    raise ValueError(f"链接解析失败: {e}")
                time.sleep(1 * (2 ** attempt))
        raise ValueError("链接解析失败")

    def _extract_video_id(self, url: str) -> str:
        """从 URL 中提取视频 ID"""
        parsed = urlparse(url)
        query = parse_qs(parsed.query)

        for key in ("modal_id", "item_ids", "group_id", "aweme_id"):
            values = query.get(key)
            if values:
                match = re.search(r"(\d{8,24})", values[0])
                if match:
                    return match.group(1)

        for pattern in (r"/video/(\d{8,24})", r"/note/(\d{8,24})", r"/(\d{8,24})(?:/|$)"):
            match = re.search(pattern, parsed.path)
            if match:
                return match.group(1)

        fallback = re.search(r"(\d{15,24})", url)
        if fallback:
            return fallback.group(1)

        raise ValueError("无法从链接中提取视频ID")

    def _fetch_item_info(self, video_id: str, resolved_url: str) -> dict:
        """获取视频元数据：Web detail → 旧 iteminfo → 分享页/嵌入 JSON"""
        last_err: Exception | None = None
        for label, fn in (
            ("web_aweme_detail", lambda: self._fetch_via_web_detail(video_id)),
            ("legacy_iteminfo", lambda: self._fetch_via_api(video_id)),
            ("share_page", lambda: self._fetch_via_share_page(video_id, resolved_url)),
        ):
            try:
                return fn()
            except Exception as e:
                last_err = e
                logger.warning("抖音元数据 %s 失败: %s", label, e)
        raise ValueError(f"抖音解析失败（已尝试多种方式）: {last_err}")

    def _fetch_via_web_detail(self, video_id: str) -> dict:
        """与 yt-dlp 一致的 Web detail 接口（无 Cookie 时也可能返回数据）"""
        url = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
        params = {
            "aweme_id": video_id,
            "aid": "1128",
            "version_name": "33.0.0",
            "device_platform": "webapp",
        }
        headers = {
            **DEFAULT_HEADERS,
            "Referer": f"https://www.douyin.com/video/{video_id}",
            "Accept": "application/json, text/plain, */*",
        }
        resp = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        detail = data.get("aweme_detail")
        if not isinstance(detail, dict) or not detail:
            raise ValueError("aweme_detail 为空")
        return detail

    def _fetch_via_api(self, video_id: str) -> dict:
        params = {"item_ids": video_id}
        for attempt in range(self.max_retries):
            try:
                resp = self.session.get(
                    self.API_URL, params=params, timeout=self.timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                items = data.get("item_list") or []
                if items:
                    return items[0]
                raise ValueError("API 返回空数据")
            except Exception as e:
                if attempt == self.max_retries - 1:
                    raise
                time.sleep(1 * (2 ** attempt))
        raise ValueError("API 请求失败")

    def _fetch_via_share_page(self, video_id: str, resolved_url: str) -> dict:
        """从分享页面 HTML 中解析视频信息"""
        parsed = urlparse(resolved_url)
        if "iesdouyin.com" in (parsed.netloc or ""):
            share_url = resolved_url
        else:
            share_url = f"https://www.iesdouyin.com/share/video/{video_id}/"

        resp = self.session.get(share_url, headers=MOBILE_HEADERS, timeout=self.timeout)
        resp.raise_for_status()
        html = resp.text or ""

        if "Please wait..." in html and "wci=" in html and "cs=" in html:
            html = self._solve_waf_and_retry(html, share_url)

        router_data = self._extract_router_data(html)
        if router_data:
            loader_data = router_data.get("loaderData", {})
            for node in loader_data.values():
                if not isinstance(node, dict):
                    continue
                video_info_res = node.get("videoInfoRes", {})
                if not isinstance(video_info_res, dict):
                    continue
                item_list = video_info_res.get("item_list", [])
                if item_list and isinstance(item_list[0], dict):
                    return item_list[0]

        for blob in self._extract_embedded_json_blobs(html):
            item = self._find_aweme_like_item(blob)
            if item:
                return item

        raise ValueError("分享页中未找到视频信息（页面结构可能已变更）")

    def _extract_embedded_json_blobs(self, html: str) -> list[dict]:
        """从分享页/客户端渲染页抽取 JSON（RENDER_DATA、__NEXT_DATA__、SIGI_STATE）"""
        blobs: list[dict] = []
        for pattern in (
            r'<script[^>]*\bid=["\']RENDER_DATA["\'][^>]*>([^<]+)</script>',
            r'<script[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>([^<]+)</script>',
        ):
            for m in re.finditer(pattern, html, re.I | re.DOTALL):
                raw = m.group(1).strip()
                try:
                    blobs.append(json.loads(unquote(raw)))
                except json.JSONDecodeError:
                    try:
                        blobs.append(json.loads(raw))
                    except json.JSONDecodeError:
                        continue
        m = re.search(
            r"window\.SIGI_STATE\s*=\s*(\{.+?\})\s*</script>",
            html,
            re.DOTALL,
        )
        if m:
            try:
                blobs.append(json.loads(m.group(1)))
            except json.JSONDecodeError:
                pass
        return blobs

    @staticmethod
    def _find_aweme_like_item(obj) -> dict | None:
        """在嵌套 JSON 中查找含 aweme_id + video 的作品节点"""

        def walk(x, depth: int) -> dict | None:
            if depth > 16:
                return None
            if isinstance(x, dict):
                if x.get("aweme_id") and isinstance(x.get("video"), dict):
                    return x
                for v in x.values():
                    r = walk(v, depth + 1)
                    if r:
                        return r
            elif isinstance(x, list):
                for v in x:
                    r = walk(v, depth + 1)
                    if r:
                        return r
            return None

        return walk(obj, 0)

    @staticmethod
    def _play_url_lists_from_video(video: dict) -> list[str]:
        """从 video 对象收集播放地址列表（兼容多码率结构）"""
        if not isinstance(video, dict):
            return []
        for key in ("play_addr", "play_addr_h264", "play_addr_bytevc1"):
            addr = video.get(key)
            if isinstance(addr, dict):
                urls = addr.get("url_list") or []
                if urls:
                    return list(urls)
        for br in video.get("bit_rate") or []:
            if not isinstance(br, dict):
                continue
            pa = br.get("play_addr")
            if isinstance(pa, dict):
                urls = pa.get("url_list") or []
                if urls:
                    return list(urls)
        return []

    def _solve_waf_and_retry(self, html: str, page_url: str) -> str:
        """解决抖音 WAF 反爬验证"""
        match = re.search(r'wci="([^"]+)"\s*,\s*cs="([^"]+)"', html)
        if not match:
            return html

        cookie_name, challenge_blob = match.groups()
        try:
            decoded = self._decode_b64(challenge_blob).decode("utf-8")
            challenge_data = json.loads(decoded)
            prefix = self._decode_b64(challenge_data["v"]["a"])
            expected = self._decode_b64(challenge_data["v"]["c"]).hex()
        except (KeyError, ValueError):
            return html

        deadline = time.monotonic() + 15.0
        for candidate in range(1_000_001):
            if time.monotonic() > deadline:
                logger.warning("抖音 WAF 求解超时，放弃本轮")
                return html
            digest = hashlib.sha256(prefix + str(candidate).encode()).hexdigest()
            if digest == expected:
                challenge_data["d"] = base64.b64encode(
                    str(candidate).encode()
                ).decode()
                cookie_val = base64.b64encode(
                    json.dumps(challenge_data, separators=(",", ":")).encode()
                ).decode()
                domain = urlparse(page_url).hostname or "www.iesdouyin.com"
                self.session.cookies.set(cookie_name, cookie_val, domain=domain, path="/")
                resp = self.session.get(page_url, headers=MOBILE_HEADERS, timeout=self.timeout)
                return resp.text or ""

        return html

    @staticmethod
    def _decode_b64(value: str) -> bytes:
        normalized = value.replace("-", "+").replace("_", "/")
        normalized += "=" * (-len(normalized) % 4)
        return base64.b64decode(normalized)

    def _extract_router_data(self, html: str) -> dict:
        marker = "window._ROUTER_DATA = "
        start = html.find(marker)
        if start < 0:
            return {}

        idx = start + len(marker)
        while idx < len(html) and html[idx].isspace():
            idx += 1
        if idx >= len(html) or html[idx] != "{":
            return {}

        depth = 0
        in_str = False
        escaped = False
        for cursor in range(idx, len(html)):
            ch = html[cursor]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(html[idx: cursor + 1])
                    except ValueError:
                        return {}
        return {}

    def _get_media_url(self, item_info: dict, mode: str = "video") -> str:
        """提取无水印播放地址"""
        if mode == "video":
            play_urls = self._play_url_lists_from_video(item_info.get("video") or {})
            if not play_urls:
                raise ValueError("未找到视频播放地址")
            return play_urls[0].replace("playwm", "play")

        if mode == "audio":
            music = item_info.get("music", {})
            audio_urls = music.get("play_url", {}).get("url_list", [])
            if not audio_urls:
                raise ValueError("未找到音频地址")
            return audio_urls[0]

        raise ValueError(f"不支持的模式: {mode}")

    def _build_result(self, item_info: dict, video_id: str) -> dict:
        """构建与 yt-dlp 解析结果兼容的统一格式"""
        title = item_info.get("desc") or f"抖音视频_{video_id}"
        author = item_info.get("author", {})
        stats = item_info.get("statistics", {})

        video_info = item_info.get("video", {})
        play_urls = self._play_url_lists_from_video(video_info)
        cover_urls = video_info.get("cover", {}).get("url_list", [])
        duration = video_info.get("duration", 0)
        duration_sec = duration // 1000 if duration > 1000 else duration

        formats = []
        if play_urls:
            clean_url = play_urls[0].replace("playwm", "play")
            width = video_info.get("width", 0)
            height = video_info.get("height", 0)
            formats.append({
                "format_id": "douyin_nowm",
                "ext": "mp4",
                "resolution": f"{width}x{height}" if width and height else "原始",
                "height": height or 720,
                "filesize": None,
                "filesize_approx": None,
                "vcodec": "h264",
                "acodec": "aac",
                "has_audio": True,
                "label": f"无水印 MP4 ({height}p)" if height else "无水印 MP4 (原始画质)",
                "_direct_url": clean_url,
            })

        return {
            "id": str(item_info.get("aweme_id") or video_id),
            "title": title,
            "thumbnail": cover_urls[0] if cover_urls else "",
            "duration": duration_sec,
            "duration_string": self._fmt_duration(duration_sec),
            "uploader": author.get("nickname", "抖音用户"),
            "platform": "抖音",
            "view_count": stats.get("play_count") or stats.get("digg_count"),
            "upload_date": "",
            "description": title[:200],
            "formats": formats,
            "subtitles": [],
            "automatic_captions": [],
        }

    @staticmethod
    def _fmt_duration(seconds: Optional[int]) -> str:
        if not seconds:
            return "00:00"
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    def _download_file(self, url: str, filepath: Path, chunk_size: int = 64 * 1024):
        """下载文件到本地"""
        for attempt in range(self.max_retries):
            try:
                resp = self.session.get(
                    url, stream=True, timeout=self.timeout, allow_redirects=True,
                )
                resp.raise_for_status()

                temp_path = filepath.with_suffix(filepath.suffix + ".part")
                with temp_path.open("wb") as f:
                    for chunk in resp.iter_content(chunk_size=chunk_size):
                        if chunk:
                            f.write(chunk)
                temp_path.replace(filepath)
                return
            except Exception as e:
                if attempt == self.max_retries - 1:
                    raise ValueError(f"文件下载失败: {e}")
                time.sleep(1 * (2 ** attempt))
