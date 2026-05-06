"""AI 视频总结相关 API 路由（独立模块，通过 include_router 挂载）"""

import asyncio
import json
import os
import time
from collections.abc import AsyncIterable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel

from ab_client import try_consume_from_request
from auth import get_optional_user
from summarizer import summarize_llm_configured

router = APIRouter(prefix="/api", tags=["AI 总结"])

# 字幕提取（含 yt-dlp / 阿里云转写）可能很慢；超时与心跳避免前端长期无响应
def _summarize_extract_timeout_sec() -> float:
    return float(os.getenv("SUMMARIZE_EXTRACT_TIMEOUT_SEC", "1200"))


def _summarize_extract_heartbeat_sec() -> float:
    return float(os.getenv("SUMMARIZE_EXTRACT_HEARTBEAT_SEC", "12"))


class SummarizeRequest(BaseModel):
    url: str
    language: str = "zh"


class ChatRequest(BaseModel):
    url: str
    question: str
    subtitle_text: str = ""


def _get_summarizer():
    """延迟初始化 VideoSummarizer（仅在首次调用时创建）"""
    from summarizer import VideoSummarizer
    if not hasattr(_get_summarizer, "_instance"):
        try:
            _get_summarizer._instance = VideoSummarizer()
        except ValueError as e:
            raise HTTPException(status_code=500, detail=str(e))
    return _get_summarizer._instance


def _get_extractor():
    """延迟初始化 SubtitleExtractor"""
    from summarizer import SubtitleExtractor
    if not hasattr(_get_extractor, "_instance"):
        _get_extractor._instance = SubtitleExtractor()
    return _get_extractor._instance


@router.post("/summarize", response_class=EventSourceResponse)
async def summarize_video(
    req: SummarizeRequest,
    user: dict | None = Depends(get_optional_user),
    request: Request = None,
) -> AsyncIterable[ServerSentEvent]:
    """
    AI 视频总结（SSE 流式）
    事件类型: progress / subtitle / summary / mindmap / done / error / quota
    """
    if not user:
        yield ServerSentEvent(
            raw_data=json.dumps(
                {
                    "message": "请先登录后使用 AI 总结功能",
                    "need_login": True,
                    "upgrade_vip": False,
                },
                ensure_ascii=False,
            ),
            event="error",
        )
        return

    try:
        loop = asyncio.get_running_loop()
        extractor = _get_extractor()
        yield ServerSentEvent(
            raw_data=json.dumps(
                {
                    "stage": "extract",
                    "message": "正在提取字幕（长视频或语音转写可能需数分钟）…",
                },
                ensure_ascii=False,
            ),
            event="progress",
        )
        fut = loop.run_in_executor(None, extractor.extract, req.url)
        total = _summarize_extract_timeout_sec()
        hb = _summarize_extract_heartbeat_sec()
        t0 = time.monotonic()
        while True:
            elapsed = time.monotonic() - t0
            if elapsed >= total:
                yield ServerSentEvent(
                    raw_data=json.dumps(
                        {
                            "message": (
                                f"字幕提取超时（>{int(total)} 秒）。"
                                "可在环境变量 SUMMARIZE_EXTRACT_TIMEOUT_SEC 调大；"
                                "或检查网络、抖音/B 站接口与阿里云 ASR 配置。"
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    event="error",
                )
                return
            slice_wait = min(hb, max(0.5, total - elapsed))
            try:
                subtitle_data = await asyncio.wait_for(asyncio.shield(fut), timeout=slice_wait)
                break
            except asyncio.TimeoutError:
                yield ServerSentEvent(
                    raw_data=json.dumps(
                        {
                            "stage": "extract",
                            "message": "仍在提取字幕，请稍候（含下载音频或云端转写时较慢）…",
                        },
                        ensure_ascii=False,
                    ),
                    event="progress",
                )

        yield ServerSentEvent(
            raw_data=json.dumps(subtitle_data, ensure_ascii=False),
            event="subtitle",
        )

        if not subtitle_data["has_subtitle"]:
            yield ServerSentEvent(
                raw_data=json.dumps({"message": "该视频没有可用的字幕，无法生成总结"}, ensure_ascii=False),
                event="error",
            )
            return

        if not summarize_llm_configured():
            yield ServerSentEvent(
                raw_data=json.dumps(
                    {
                        "message": (
                            "服务器未配置大模型（SUMMARIZE_LLM_API_KEY 或 DEEPSEEK_API_KEY），无法生成 AI 总结。"
                            "请在 backend/.env 中配置后重启后端。"
                        ),
                    },
                    ensure_ascii=False,
                ),
                event="error",
            )
            return

        consume = await try_consume_from_request(request, action="summarize")
        if not consume.get("ok", False):
            raise HTTPException(status_code=502, detail="主站计费服务异常")
        if not consume.get("allowed", False):
            reason = consume.get("reason")
            msg = consume.get("message") or ("需要开通会员" if reason == "need_membership" else "积分不足")
            yield ServerSentEvent(
                raw_data=json.dumps(
                    {
                        "message": msg,
                        "need_login": False,
                        "upgrade_vip": reason == "need_membership",
                    },
                    ensure_ascii=False,
                ),
                event="error",
            )
            return

        full_text = subtitle_data["full_text"]
        summarizer = _get_summarizer()

        for token in summarizer.summarize_stream(full_text, req.language):
            yield ServerSentEvent(raw_data=json.dumps(token, ensure_ascii=False), event="summary")

        mindmap_md = await loop.run_in_executor(
            None, summarizer.generate_mindmap, full_text, req.language
        )
        yield ServerSentEvent(
            raw_data=json.dumps({"markdown": mindmap_md}, ensure_ascii=False),
            event="mindmap",
        )

        if consume.get("mode") == "free_trial":
            limit = int(consume.get("free_trial_limit") or 0)
            used_after = int(consume.get("used_free_trials_after") or 0)
            remaining = max(0, limit - used_after)
            quota_info = {"remaining": remaining, "limit": limit}
        else:
            quota_info = {"remaining": -1, "limit": -1}
        yield ServerSentEvent(
            raw_data=json.dumps(quota_info, ensure_ascii=False),
            event="quota",
        )

        yield ServerSentEvent(raw_data="[DONE]", event="done")

    except Exception as e:
        yield ServerSentEvent(
            raw_data=json.dumps({"message": f"总结失败: {str(e)}"}, ensure_ascii=False),
            event="error",
        )


@router.post("/chat", response_class=EventSourceResponse)
async def chat_with_video(
    req: ChatRequest,
    user: dict | None = Depends(get_optional_user),
) -> AsyncIterable[ServerSentEvent]:
    """AI 视频问答（SSE 流式）"""
    try:
        if not user:
            yield ServerSentEvent(
                raw_data=json.dumps(
                    {"message": "请先登录后使用 AI 问答功能", "need_login": True},
                    ensure_ascii=False,
                ),
                event="error",
            )
            return

        if not summarize_llm_configured():
            yield ServerSentEvent(
                raw_data=json.dumps(
                    {
                        "message": (
                            "服务器未配置大模型（SUMMARIZE_LLM_API_KEY 或 DEEPSEEK_API_KEY），无法使用 AI 问答。"
                            "请在 backend/.env 中配置后重启后端。"
                        ),
                    },
                    ensure_ascii=False,
                ),
                event="error",
            )
            return

        if not req.subtitle_text.strip():
            loop = asyncio.get_running_loop()
            extractor = _get_extractor()
            try:
                subtitle_data = await asyncio.wait_for(
                    loop.run_in_executor(None, extractor.extract, req.url),
                    timeout=_summarize_extract_timeout_sec(),
                )
            except asyncio.TimeoutError:
                yield ServerSentEvent(
                    raw_data=json.dumps(
                        {
                            "message": (
                                "字幕提取超时。可增大 SUMMARIZE_EXTRACT_TIMEOUT_SEC 或检查网络。"
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    event="error",
                )
                return
            if not subtitle_data["has_subtitle"]:
                yield ServerSentEvent(
                    raw_data=json.dumps({"message": "该视频没有可用的字幕，无法回答问题"}, ensure_ascii=False),
                    event="error",
                )
                return
            subtitle_text = subtitle_data["full_text"]
        else:
            subtitle_text = req.subtitle_text

        summarizer = _get_summarizer()
        for token in summarizer.chat_stream(subtitle_text, req.question):
            yield ServerSentEvent(raw_data=json.dumps(token, ensure_ascii=False), event="answer")

        yield ServerSentEvent(raw_data="[DONE]", event="done")

    except Exception as e:
        yield ServerSentEvent(
            raw_data=json.dumps({"message": f"回答失败: {str(e)}"}, ensure_ascii=False),
            event="error",
        )
