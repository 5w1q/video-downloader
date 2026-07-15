"""下载文件名：根据视频内容生成短标题（默认最多 30 字）。"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional, Union

MAX_TITLE_LEN = 30

_UNSAFE_CHARS = re.compile(r'[\\/*?:"<>|\n\r\t#@]+')
_MULTI_UNDERSCORE = re.compile(r"_+")
# 纯字母数字/短横线等，常见于平台把 ID 当标题
_ID_LIKE = re.compile(r"^[A-Za-z0-9_\-]{6,64}$")
_HAS_CJK = re.compile(r"[\u4e00-\u9fff]")


def content_title_enabled() -> bool:
    """默认开启；设 CONTENT_TITLE_ON_DOWNLOAD=0 可关闭。"""
    v = (os.getenv("CONTENT_TITLE_ON_DOWNLOAD") or "1").strip().lower()
    return v in ("1", "true", "yes", "on")


def to_simplified_chinese(text: str) -> str:
    """繁体中文转简体；无中文或转换失败时原样返回。"""
    raw = text or ""
    if not raw or not _HAS_CJK.search(raw):
        return raw
    try:
        import zhconv

        return zhconv.convert(raw, "zh-cn")
    except Exception:
        return raw


def sanitize_download_basename(text: str, max_len: int = MAX_TITLE_LEN) -> str:
    """清洗为安全文件名主干，截断到 max_len；含中文时统一为简体。"""
    raw = (text or "").strip()
    if not raw:
        return "video"
    raw = to_simplified_chinese(raw)
    # 去掉首尾引号/书名号等装饰
    raw = raw.strip(" \"'`「」『』【】[]()（）")
    raw = raw.replace("\xa0", " ").replace("\u3000", " ")
    raw = re.sub(r"[\x00-\x1f\x7f]", "", raw)
    raw = _UNSAFE_CHARS.sub("_", raw)
    raw = re.sub(r"\s+", " ", raw)
    raw = _MULTI_UNDERSCORE.sub("_", raw).strip("_. ")
    if not raw:
        return "video"
    if len(raw) > max_len:
        raw = raw[:max_len].rstrip("_. ")
    return raw or "video"


def looks_like_weak_title(title: str) -> bool:
    """平台标题是否像 ID / 无意义短码（更应走内容标题）。"""
    t = (title or "").strip()
    if not t:
        return True
    if _HAS_CJK.search(t):
        return False
    if _ID_LIKE.match(t):
        return True
    # 几乎全是数字
    alnum = re.sub(r"[\s_\-]", "", t)
    if alnum.isdigit() and len(alnum) >= 6:
        return True
    return False


def _fallback_from_text(text: str, platform_title: str) -> str:
    for candidate in (text, platform_title):
        safe = sanitize_download_basename(candidate)
        if safe and safe != "video":
            return safe
    return sanitize_download_basename(platform_title or "video")


def _llm_short_title(text: str) -> str:
    """用 LLM 压成 ≤30 字短标题；失败返回空串。"""
    text = (text or "").strip()
    if not text:
        return ""
    try:
        from summarizer import VideoSummarizer, summarize_llm_configured

        if not summarize_llm_configured():
            return ""
        raw = VideoSummarizer().generate_short_title(text, max_len=MAX_TITLE_LEN)
        safe = sanitize_download_basename(raw)
        if safe and safe != "video":
            return safe
    except Exception:
        pass
    return ""


def generate_download_title(url: str, platform_title: str = "") -> str:
    """
    根据字幕/转写内容生成最多 30 字标题；失败则回退平台标题（同样截断）。
    无字幕但已配置 LLM 时，仍用平台标题走一遍短标题总结，避免长期停留在裸 ID。
    """
    platform_title = (platform_title or "").strip()
    if not content_title_enabled():
        return sanitize_download_basename(platform_title or "video")

    content = ""
    try:
        from summarizer import SubtitleExtractor

        sub = SubtitleExtractor().extract(url)
        content = (sub.get("full_text") or "").strip()
        if content:
            short = _llm_short_title(content)
            if short:
                return short
    except Exception:
        pass

    if content:
        return _fallback_from_text(content, platform_title)

    # 无字幕：对平台标题做 LLM 短标题（YouTube 搜索标题 / X 推文截断等）
    if platform_title:
        short = _llm_short_title(platform_title)
        if short:
            return short
    return sanitize_download_basename(platform_title or "video")


def _unique_filepath(directory: Path, stem: str, ext: str) -> Path:
    """同目录重名时追加 _2、_3…（尽量保持主干不超过 max_len）。"""
    ext = ext if ext.startswith(".") else f".{ext}" if ext else ""
    candidate = directory / f"{stem}{ext}"
    if not candidate.exists():
        return candidate
    for i in range(2, 1000):
        suffix = f"_{i}"
        budget = max(1, MAX_TITLE_LEN - len(suffix))
        truncated = stem[:budget].rstrip("_. ") or "video"
        candidate = directory / f"{truncated}{suffix}{ext}"
        if not candidate.exists():
            return candidate
    return directory / f"{stem}_{os.getpid()}{ext}"


def apply_content_filename(
    filepath: Union[str, Path],
    url: str,
    platform_title: str = "",
) -> dict:
    """
    将已下载文件重命名为内容标题。
    返回 filepath / filename / title（title 为展示用短标题，不含扩展名）。
    """
    path = Path(filepath)
    if not path.is_file():
        raise FileNotFoundError(f"下载文件不存在: {filepath}")

    title = generate_download_title(url, platform_title=platform_title)
    ext = path.suffix.lstrip(".") or "mp4"
    dest = _unique_filepath(path.parent, title, ext)

    if dest.resolve() != path.resolve():
        path.rename(dest)
        path = dest

    return {
        "filepath": str(path),
        "filename": path.name,
        "title": title,
        "ext": ext,
    }
