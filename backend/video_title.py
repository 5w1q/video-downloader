"""下载文件名：以「视频源标题」为准生成命名（默认最多 20 字）。

命名规则：
1. 取视频源标题（``platform_title``；缺失时按链接解析）作为命名依据。
2. 非简体中文（外文 / 繁体）→ 翻译为简体中文。
3. 源标题超过 ``MAX_TITLE_LEN`` 字 → 在保持原意前提下概括到 ``MAX_TITLE_LEN`` 以内。
4. 去除标点 / 符号，只保留文字与数字。

LLM 不可用时回退为「源标题简体化 + 去符号 + 截断」，保证仍有可读文件名。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Union

MAX_TITLE_LEN = 20

_ID_LIKE = re.compile(r"^[A-Za-z0-9_\-]{6,64}$")
_HAS_CJK = re.compile(r"[\u4e00-\u9fff]")
# 拉丁字母 / 日文假名 / 韩文谚文：出现即视为「非简体中文」，需要翻译
_NEEDS_TRANSLATION = re.compile(
    r"[A-Za-z\u3040-\u30ff\u31f0-\u31ff\uac00-\ud7af]"
)


def content_title_enabled() -> bool:
    """默认开启；设 CONTENT_TITLE_ON_DOWNLOAD=0 可关闭（此时仅做源标题清洗）。"""
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


def _strip_symbols(text: str) -> str:
    """去除标点 / 符号 / emoji，仅保留字母数字（含中日韩文字）与空格。"""
    out: list[str] = []
    for ch in text or "":
        if ch.isspace():
            out.append(" ")
        elif ch.isalnum():
            out.append(ch)
        # 其余（标点、符号、emoji）直接丢弃
    return re.sub(r"\s+", " ", "".join(out)).strip()


def sanitize_download_basename(text: str, max_len: int = MAX_TITLE_LEN) -> str:
    """清洗为安全文件名主干：繁→简、去符号、截断到 max_len。"""
    raw = (text or "").strip()
    if not raw:
        return "video"
    raw = to_simplified_chinese(raw)
    raw = raw.replace("\xa0", " ").replace("\u3000", " ")
    raw = re.sub(r"[\x00-\x1f\x7f]", "", raw)
    raw = _strip_symbols(raw)
    if not raw:
        return "video"
    if len(raw) > max_len:
        raw = raw[:max_len].strip()
    return raw or "video"


def looks_like_weak_title(title: str) -> bool:
    """平台标题是否像 ID / 无意义短码（用于 ZIP 成员名兜底判断）。"""
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


def _resolve_source_title(url: str) -> str:
    """调用方未提供源标题时，按链接解析视频源标题（best-effort）。"""
    url = (url or "").strip()
    if not url.startswith("http"):
        return ""
    try:
        import yt_dlp

        from downloader import _ytdlp_base_opts

        opts = {
            **_ytdlp_base_opts(url),
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "skip_download": True,
            "socket_timeout": 30,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return (info.get("title") or "").strip() if info else ""
    except Exception:
        return ""


def _llm_title_from_source(source_title: str) -> str:
    """用 LLM 把源标题转成 ≤MAX_TITLE_LEN 的简体中文文件名标题；失败返回空串。"""
    source_title = (source_title or "").strip()
    if not source_title:
        return ""
    try:
        from summarizer import VideoSummarizer, summarize_llm_configured

        if not summarize_llm_configured():
            return ""
        raw = VideoSummarizer().generate_filename_title(
            source_title, max_len=MAX_TITLE_LEN
        )
        safe = sanitize_download_basename(raw)
        if safe and safe != "video":
            return safe
    except Exception:
        pass
    return ""


def generate_download_title(url: str, platform_title: str = "") -> str:
    """以视频源标题为准生成文件名标题（≤ MAX_TITLE_LEN 字，简体中文，去符号）。

    - 非简体中文（外文 / 繁体）→ 翻译为简体中文
    - 超过 MAX_TITLE_LEN 字 → 保持原意概括到 MAX_TITLE_LEN 以内
    LLM 不可用 / 关闭内容标题时，回退为源标题简体化 + 去符号 + 截断。
    """
    source_title = (platform_title or "").strip()
    if not source_title:
        source_title = _resolve_source_title(url)
    if not source_title:
        return "video"

    simplified = to_simplified_chinese(source_title)

    # 关闭开关：只做清洗，不调用 LLM / 不翻译
    if not content_title_enabled():
        return sanitize_download_basename(simplified)

    needs_translation = bool(_NEEDS_TRANSLATION.search(simplified))
    too_long = len(_strip_symbols(simplified)) > MAX_TITLE_LEN

    # 已是简体中文且不超长：直接去符号截断，无需 LLM
    if not needs_translation and not too_long:
        return sanitize_download_basename(simplified)

    # 需翻译或需概括：走 LLM（翻译 + 保持原意压缩到 ≤MAX_TITLE_LEN）
    llm = _llm_title_from_source(source_title)
    if llm:
        return llm

    # LLM 不可用 / 失败：尽力回退
    return sanitize_download_basename(simplified)


def _unique_filepath(directory: Path, stem: str, ext: str) -> Path:
    """同目录重名时追加 _2、_3…（尽量保持主干不超过 MAX_TITLE_LEN）。"""
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
    将已下载文件重命名为源标题（简体中文、≤ MAX_TITLE_LEN 字、去符号）。
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
