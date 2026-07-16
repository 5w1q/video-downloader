"""搜索预览列表标题的「展示用简体中文」翻译。

各平台（X / YouTube / Instagram）搜索结果的 ``title`` 常为外文（日文/英文/韩文）
或繁体中文。此模块把这些标题统一翻译为简体中文，写入结果项的 ``title_display``
字段供前端展示；**不改动** ``title`` 原文（下载命名仍以原始源标题为准）。

策略（一次搜索最多一次 LLM 调用，控制费用与延迟）：
1. 先对所有标题做繁体→简体本地转换（zhconv）。
2. 仅对仍含拉丁字母 / 日文假名 / 韩文谚文的标题，批量交给 LLM 翻译。
3. 未配置 LLM、被开关关闭或调用失败时，回退为「繁→简 + 原文」，保证仍可展示。

开关：``SEARCH_TITLE_TRANSLATE=0`` 可关闭 LLM 翻译（仅做繁→简）。
"""

from __future__ import annotations

import os
import re

from video_title import to_simplified_chinese

# 含拉丁字母 / 日文假名 / 韩文谚文 → 视为外文，需要翻译为中文
_NEEDS_TRANSLATION = re.compile(r"[A-Za-z\u3040-\u30ff\u31f0-\u31ff\uac00-\ud7af]")
# 单次搜索至多翻译的标题数（与搜索条数上限一致）
_MAX_TITLES = 50


def display_titles_enabled() -> bool:
    """默认开启；设 SEARCH_TITLE_TRANSLATE=0 关闭 LLM 翻译（仍做繁→简）。"""
    v = (os.getenv("SEARCH_TITLE_TRANSLATE") or "1").strip().lower()
    return v in ("1", "true", "yes", "on")


def translate_titles_to_simplified(titles: list[str]) -> list[str]:
    """返回与输入等长的「展示用简体中文标题」列表。"""
    base = [to_simplified_chinese((t or "").strip()) for t in titles]
    if not display_titles_enabled():
        return base

    idx = [i for i, t in enumerate(base) if t and _NEEDS_TRANSLATION.search(t)]
    if not idx:
        return base
    idx = idx[:_MAX_TITLES]

    try:
        from summarizer import VideoSummarizer, summarize_llm_configured

        if not summarize_llm_configured():
            return base
        subset = [base[i] for i in idx]
        translated = VideoSummarizer().translate_to_simplified(subset)
    except Exception:
        return base

    out = list(base)
    for i, tr in zip(idx, translated):
        text = (tr or "").strip()
        if text:
            out[i] = text
    return out


def annotate_result_titles(result: dict) -> dict:
    """给 ``result['results']`` 与 ``['below_threshold_results']`` 补 ``title_display``。

    就地修改并返回同一 dict；任何异常都被吞掉，不影响搜索主流程。
    """
    try:
        for key in ("results", "below_threshold_results"):
            items = result.get(key) or []
            if not items:
                continue
            titles = [(it.get("title") or "") for it in items]
            display = translate_titles_to_simplified(titles)
            for it, d in zip(items, display):
                it["title_display"] = d or (it.get("title") or "")
    except Exception:
        pass
    return result
