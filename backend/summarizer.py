"""AI 视频总结模块：字幕提取 + DeepSeek 大模型总结"""

import json
import os
import re
import tempfile
from typing import Optional

import httpx
import yt_dlp
from openai import OpenAI

from douyin import DEFAULT_HEADERS, DouyinParser, is_douyin_url, normalize_media_url


def _is_bilibili_url(url: str) -> bool:
    return "bilibili.com" in url or "b23.tv" in url


class SubtitleExtractor:
    """从视频 URL 提取平台字幕（人工字幕 > 自动字幕）"""

    PREFERRED_LANGS = ["zh-Hans", "zh", "zh-CN", "en", "ja", "ko"]
    SUBTITLE_FORMAT = "json3"

    def extract(self, url: str) -> dict:
        """
        提取视频字幕，返回:
        {
            "has_subtitle": bool,
            "language": str,
            "subtitle_type": "manual" | "auto" | "none",
            "segments": [{"start": float, "end": float, "text": str}, ...],
            "full_text": str
        }
        """
        url = normalize_media_url(url)
        if _is_bilibili_url(url):
            result = self._extract_bilibili(url)
            if result["has_subtitle"]:
                return result

        if is_douyin_url(url):
            return self._extract_douyin(url)

        info = self._get_video_info(url)

        manual_subs = info.get("subtitles") or {}
        auto_subs = info.get("automatic_captions") or {}

        manual_subs = {k: v for k, v in manual_subs.items() if k != "danmaku"}

        lang, sub_url, sub_type = self._pick_best_subtitle(manual_subs, auto_subs)
        if not sub_url:
            return {
                "has_subtitle": False,
                "language": "",
                "subtitle_type": "none",
                "segments": [],
                "full_text": "",
            }

        # 优先用 yt-dlp 已给出的字幕直链拉取（快、稳）；失败再让 yt-dlp 写本地 VTT
        segments = self._segments_from_subtitle_url(sub_url)
        if not segments:
            segments = self._download_and_parse(url, lang, sub_type)

        full_text = " ".join(seg["text"] for seg in segments).strip()
        if not full_text:
            return {
                "has_subtitle": False,
                "language": "",
                "subtitle_type": "none",
                "segments": [],
                "full_text": "",
            }

        return {
            "has_subtitle": True,
            "language": lang,
            "subtitle_type": sub_type,
            "segments": segments,
            "full_text": full_text,
        }

    def _extract_bilibili(self, url: str) -> dict:
        """B 站专用字幕提取（通过 dm/view API 获取 CC 字幕和 AI 字幕）"""
        empty = {
            "has_subtitle": False, "language": "", "subtitle_type": "none",
            "segments": [], "full_text": "",
        }
        try:
            bvid = self._parse_bvid(url)
            if not bvid:
                return empty

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": f"https://www.bilibili.com/video/{bvid}",
            }

            view_resp = httpx.get(
                f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
                headers=headers, timeout=15,
            )
            view_data = view_resp.json().get("data", {})
            cid = view_data.get("cid")
            aid = view_data.get("aid")
            if not cid or not aid:
                return empty

            dm_resp = httpx.get(
                f"https://api.bilibili.com/x/v2/dm/view?aid={aid}&oid={cid}&type=1",
                headers=headers, timeout=15,
            )
            dm_data = dm_resp.json().get("data", {})
            subtitle_list = dm_data.get("subtitle", {}).get("subtitles", [])

            if not subtitle_list:
                return empty

            best = subtitle_list[0]
            for s in subtitle_list:
                lang = s.get("lan", "")
                if lang == "zh" or lang == "zh-Hans":
                    best = s
                    break

            sub_type = "auto" if best.get("lan", "").startswith("ai-") else "manual"

            sub_url = best.get("subtitle_url", "")
            if sub_url.startswith("//"):
                sub_url = "https:" + sub_url
            if sub_url.startswith("http://"):
                sub_url = "https://" + sub_url[7:]

            if not sub_url:
                return empty

            sub_resp = httpx.get(sub_url, headers=headers, timeout=15)
            sub_json = sub_resp.json()
            body = sub_json.get("body", [])

            segments = []
            for item in body:
                content = item.get("content", "").strip()
                if not content:
                    continue
                segments.append({
                    "start": round(item.get("from", 0), 2),
                    "end": round(item.get("to", 0), 2),
                    "text": content,
                })

            full_text = " ".join(seg["text"] for seg in segments)
            return {
                "has_subtitle": True,
                "language": best.get("lan", "zh"),
                "subtitle_type": sub_type,
                "segments": segments,
                "full_text": full_text,
            }
        except Exception:
            return empty

    def _extract_douyin(self, url: str) -> dict:
        """
        抖音不走 yt-dlp：其 Douyin 提取器常要求浏览器 Cookie，会报
        Fresh cookies are needed。此处复用 DouyinParser 的公开接口取元数据，
        再尝试拉取字幕；若无字幕则用作品文案 desc 作为弱替代供 AI 总结。
        """
        empty = {
            "has_subtitle": False,
            "language": "",
            "subtitle_type": "none",
            "segments": [],
            "full_text": "",
        }
        tmp_root = os.path.join(tempfile.gettempdir(), "douyin_summarize")
        os.makedirs(tmp_root, exist_ok=True)
        try:
            parser = DouyinParser(download_dir=tmp_root)
            item = parser.get_item_info(url)
        except Exception as e:
            raise ValueError(f"抖音解析失败: {e}") from e

        video = item.get("video") if isinstance(item.get("video"), dict) else {}
        infos = (
            video.get("subtitle_infos")
            or item.get("subtitle_infos")
            or video.get("subtitle_info")
            or []
        )
        if not isinstance(infos, list):
            infos = []

        headers = {
            "User-Agent": DEFAULT_HEADERS["User-Agent"],
            "Referer": "https://www.douyin.com/",
            "Accept": "*/*",
        }

        for info in infos:
            if not isinstance(info, dict):
                continue
            sub_url = (
                info.get("Url")
                or info.get("url")
                or info.get("SubtitleUrl")
                or info.get("subtitle_url")
            )
            if not sub_url:
                continue
            try:
                resp = httpx.get(sub_url, headers=headers, timeout=20, follow_redirects=True)
                resp.raise_for_status()
                text = resp.text.strip()
                segments: list[dict] = []
                if text.upper().startswith("WEBVTT"):
                    tmp_path = None
                    try:
                        with tempfile.NamedTemporaryFile(
                            mode="w", suffix=".vtt", delete=False, encoding="utf-8"
                        ) as f:
                            tmp_path = f.name
                            f.write(text)
                        segments = self._parse_vtt(tmp_path)
                    finally:
                        if tmp_path:
                            try:
                                os.unlink(tmp_path)
                            except OSError:
                                pass
                else:
                    try:
                        segments = self._parse_douyin_subtitle_json(resp.json())
                    except Exception:
                        segments = []
                if segments:
                    full_text = " ".join(seg["text"] for seg in segments)
                    lang = str(
                        info.get("LanguageCodeName")
                        or info.get("Language")
                        or info.get("language")
                        or "zh"
                    )
                    return {
                        "has_subtitle": True,
                        "language": lang,
                        "subtitle_type": "manual",
                        "segments": segments,
                        "full_text": full_text,
                    }
            except Exception:
                continue

        desc = (item.get("desc") or "").strip()
        if desc:
            return {
                "has_subtitle": True,
                "language": "zh",
                "subtitle_type": "auto",
                "segments": [{"start": 0.0, "end": 0.0, "text": desc}],
                "full_text": desc,
            }
        return empty

    @staticmethod
    def _parse_douyin_subtitle_json(data: dict) -> list[dict]:
        """解析抖音字幕 JSON（字段名随版本可能不同）"""
        if not isinstance(data, dict):
            return []
        nested = data.get("data")
        if isinstance(nested, dict):
            inner = SubtitleExtractor._parse_douyin_subtitle_json(nested)
            if inner:
                return inner

        candidates: list = []
        for key in ("utterances", "caption_list", "captions", "sentences", "body"):
            v = data.get(key)
            if isinstance(v, list) and v:
                candidates = v
                break

        segments: list[dict] = []
        for item in candidates:
            if not isinstance(item, dict):
                continue
            text = (
                (item.get("text") or item.get("content") or item.get("value") or "")
                .strip()
            )
            if not text:
                continue
            st = item.get("start_time")
            et = item.get("end_time")
            if st is None:
                st = item.get("start") or item.get("from") or 0
            if et is None:
                et = item.get("end") or item.get("to") or st
            try:
                st_f = float(st)
                et_f = float(et)
            except (TypeError, ValueError):
                st_f, et_f = 0.0, 0.0
            if st_f > 500 or et_f > 500:
                st_f /= 1000.0
                et_f /= 1000.0
            segments.append({
                "start": round(st_f, 2),
                "end": round(max(et_f, st_f), 2),
                "text": text,
            })
        return segments

    @staticmethod
    def _parse_bvid(url: str) -> Optional[str]:
        m = re.search(r"(BV[a-zA-Z0-9]+)", url)
        return m.group(1) if m else None

    def _get_video_info(self, url: str) -> dict:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "extract_flat": False,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "skip_download": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            raise ValueError("无法解析该视频链接")
        return info

    def _pick_best_subtitle(
        self, manual_subs: dict, auto_subs: dict
    ) -> tuple[str, Optional[str], str]:
        """按优先级选择最佳字幕，返回 (lang, url, type)"""
        for lang in self.PREFERRED_LANGS:
            if lang in manual_subs:
                formats = manual_subs[lang]
                url = self._get_format_url(formats)
                if url:
                    return lang, url, "manual"

        for lang in self.PREFERRED_LANGS:
            if lang in auto_subs:
                formats = auto_subs[lang]
                url = self._get_format_url(formats)
                if url:
                    return lang, url, "auto"

        if manual_subs:
            first_lang = next(iter(manual_subs))
            url = self._get_format_url(manual_subs[first_lang])
            if url:
                return first_lang, url, "manual"

        if auto_subs:
            first_lang = next(iter(auto_subs))
            url = self._get_format_url(auto_subs[first_lang])
            if url:
                return first_lang, url, "auto"

        return "", None, "none"

    @staticmethod
    def _get_format_url(formats: list) -> Optional[str]:
        preferred = ["json3", "srv3", "vtt", "ttml"]
        for pref in preferred:
            for fmt in formats:
                if fmt.get("ext") == pref:
                    return fmt.get("url")
        return formats[0].get("url") if formats else None

    def _segments_from_subtitle_url(self, sub_url: str) -> list[dict]:
        """直接请求字幕 URL（如 YouTube json3 / WebVTT），避免二次 yt-dlp 下载失败却误判有字幕。"""
        if not sub_url or not sub_url.startswith("http"):
            return []
        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                ),
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
            }
            resp = httpx.get(sub_url, headers=headers, timeout=30, follow_redirects=True)
            resp.raise_for_status()
            text = resp.text
        except Exception:
            return []

        t = text.lstrip("\ufeff").strip()
        if t.upper().startswith("WEBVTT"):
            return self._parse_vtt_content(text)

        try:
            data = json.loads(resp.content.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, ValueError):
            return []

        if isinstance(data, dict) and "events" in data:
            return self._segments_from_youtube_json3(data)
        return []

    @staticmethod
    def _segments_from_youtube_json3(data: dict) -> list[dict]:
        """YouTube timedtext json3（events / segs / utf8）"""
        events = data.get("events") or []
        segments: list[dict] = []
        for ev in events:
            if not isinstance(ev, dict):
                continue
            segs = ev.get("segs")
            if not segs:
                continue
            parts: list[str] = []
            for s in segs:
                if not isinstance(s, dict):
                    continue
                u = s.get("utf8") or s.get("utf8_2")
                if u:
                    parts.append(str(u).replace("\n", " ").strip())
            line = "".join(parts).strip()
            if not line:
                continue
            try:
                start_ms = int(ev.get("tStartMs", 0))
            except (TypeError, ValueError):
                start_ms = 0
            try:
                dur_ms = int(ev.get("dDurationMs", 0) or 0)
            except (TypeError, ValueError):
                dur_ms = 0
            start = start_ms / 1000.0
            end = (start_ms + dur_ms) / 1000.0 if dur_ms > 0 else start + 0.5
            segments.append({
                "start": round(start, 2),
                "end": round(end, 2),
                "text": line,
            })
        return segments

    @staticmethod
    def _parse_vtt_content(content: str) -> list[dict]:
        """解析 WebVTT 文本（支持 MM:SS.mmm 与 HH:MM:SS.mmm）"""
        time_pattern = re.compile(
            r"(\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3})\s*-->\s*(\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3})"
        )
        segments: list[dict] = []
        blocks = re.split(r"\n\n+", content)
        seen_texts: set[str] = set()

        for block in blocks:
            lines = block.strip().split("\n")
            time_match = None
            text_lines: list[str] = []
            for line in lines:
                m = time_pattern.search(line)
                if m:
                    time_match = m
                elif time_match and line.strip() and not line.strip().isdigit():
                    clean = re.sub(r"<[^>]+>", "", line.strip())
                    if clean:
                        text_lines.append(clean)

            if time_match and text_lines:
                ttext = " ".join(text_lines)
                if ttext in seen_texts:
                    continue
                seen_texts.add(ttext)
                start = _time_to_seconds_flex(time_match.group(1))
                end = _time_to_seconds_flex(time_match.group(2))
                segments.append({
                    "start": round(start, 2),
                    "end": round(end, 2),
                    "text": ttext,
                })

        return segments

    def _download_and_parse(self, url: str, lang: str, sub_type: str) -> list[dict]:
        """通过 yt-dlp 下载字幕文件并解析为分段列表"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "skip_download": True,
                "writesubtitles": sub_type == "manual",
                "writeautomaticsub": sub_type == "auto",
                "subtitleslangs": [lang],
                "subtitlesformat": "vtt",
                "outtmpl": os.path.join(tmp_dir, "subtitle"),
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            vtt_files = [
                f for f in os.listdir(tmp_dir) if f.endswith(".vtt")
            ]
            if not vtt_files:
                return []

            vtt_path = os.path.join(tmp_dir, vtt_files[0])
            return self._parse_vtt(vtt_path)

    @staticmethod
    def _parse_vtt(filepath: str) -> list[dict]:
        """解析 VTT 字幕文件为结构化分段"""
        with open(filepath, "r", encoding="utf-8") as f:
            return SubtitleExtractor._parse_vtt_content(f.read())


class VideoSummarizer:
    """使用 DeepSeek API 生成视频总结、思维导图、问答"""

    def __init__(self):
        api_key = os.getenv("DEEPSEEK_API_KEY", "")
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY 环境变量未设置")
        self.client = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com",
        )
        self.model = "deepseek-chat"

    def summarize_stream(self, subtitle_text: str, language: str = "zh"):
        """流式生成视频总结，yield 每个 token"""
        prompt = self._build_summary_prompt(subtitle_text, language)
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "你是一个专业的视频内容分析助手，擅长提取关键信息并生成结构化的总结。"},
                {"role": "user", "content": prompt},
            ],
            stream=True,
            temperature=0.7,
            max_tokens=4096,
        )
        for chunk in response:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content

    def generate_mindmap(self, subtitle_text: str, language: str = "zh") -> str:
        """生成思维导图 Markdown（非流式，一次性返回）"""
        prompt = self._build_mindmap_prompt(subtitle_text, language)
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "你是一个专业的思维导图生成助手，擅长将内容组织为清晰的层级结构。"},
                {"role": "user", "content": prompt},
            ],
            stream=False,
            temperature=0.5,
            max_tokens=4096,
        )
        return response.choices[0].message.content

    def chat_stream(self, subtitle_text: str, question: str):
        """基于视频内容的 AI 问答，流式返回"""
        prompt = self._build_chat_prompt(subtitle_text, question)
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "你是一个视频内容问答助手。根据提供的视频字幕内容来回答用户的问题。如果问题超出视频内容范围，请诚实告知。"},
                {"role": "user", "content": prompt},
            ],
            stream=True,
            temperature=0.7,
            max_tokens=2048,
        )
        for chunk in response:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content

    @staticmethod
    def _build_summary_prompt(subtitle_text: str, language: str) -> str:
        truncated = subtitle_text[:15000]
        lang_hint = "中文" if language.startswith("zh") else "与原文相同的语言"
        return f"""请对以下视频字幕内容进行深度总结分析，使用{lang_hint}输出。

要求输出格式：
## 视频概述
（用2-3句话概括视频的主题和核心内容）

## 内容大纲
（按视频内容的逻辑顺序，列出主要章节/段落，每个章节包含要点）

## 核心知识要点
（提取视频中最重要的知识点、观点或结论，用编号列表形式）

## 总结
（用1-2句话给出整体评价或一句话总结）

---
视频字幕内容：
{truncated}"""

    @staticmethod
    def _build_mindmap_prompt(subtitle_text: str, language: str) -> str:
        truncated = subtitle_text[:15000]
        lang_hint = "中文" if language.startswith("zh") else "与原文相同的语言"
        return f"""请将以下视频字幕内容整理为思维导图结构，使用{lang_hint}输出。

要求：
1. 使用 Markdown 标题层级格式（# 一级标题，## 二级标题，### 三级标题）
2. 最外层是视频主题
3. 第二层是主要章节/模块
4. 第三层是各章节的要点
5. 可以有第四层做更细的展开
6. 每个节点的文字要简洁精炼
7. 只输出 Markdown 内容，不要其他说明文字

---
视频字幕内容：
{truncated}"""

    @staticmethod
    def _build_chat_prompt(subtitle_text: str, question: str) -> str:
        truncated = subtitle_text[:12000]
        return f"""以下是一个视频的字幕内容，请根据这些内容回答用户的问题。

视频字幕内容：
{truncated}

---
用户问题：{question}

请基于视频内容给出准确、详细的回答。如果视频内容中没有相关信息，请诚实说明。"""


def _time_to_seconds_flex(time_str: str) -> float:
    """WebVTT 时间戳：HH:MM:SS.mmm 或 MM:SS.mmm"""
    time_str = time_str.replace(",", ".")
    parts = time_str.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return float(parts[0])
