"""YouTube 关键词搜索 + 阈值筛选，并可直接接入批量下载 SSE。"""

from __future__ import annotations

import asyncio
from functools import partial
from typing import AsyncIterator, Optional

from fastapi import APIRouter, Form
from pydantic import BaseModel, Field

from api_bulk_download import resolve_bulk_output, sse_streaming_response
from bulk_download_core import fmt_sse, run_bulk_download_stream
from bulk_zip_tokens import sweep_expired_bulk_tokens
from downloader import friendly_download_error
from youtube_search import search_youtube

router = APIRouter(prefix="/api", tags=["YouTube 搜索"])


class YoutubeSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=200)
    max_results: int = Field(20, ge=1, le=50)
    min_views: int = Field(0, ge=0)
    min_likes: int = Field(0, ge=0)
    search_pool: Optional[int] = Field(None, ge=1, le=50)
    date_filter: str = Field("all", pattern="^(all|today|week|month|date)$")
    upload_date: Optional[str] = Field(None, max_length=16)


@router.post("/youtube/search")
async def youtube_search(body: YoutubeSearchRequest):
    """仅搜索并筛选，返回候选列表（不下载）。"""
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            partial(
                search_youtube,
                body.query,
                max_results=body.max_results,
                min_views=body.min_views,
                min_likes=body.min_likes,
                search_pool=body.search_pool,
                date_filter=body.date_filter,
                upload_date=body.upload_date,
            ),
        )
    except ValueError as e:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=400, detail=friendly_download_error(e)
        ) from e
    return result


@router.post("/youtube/search-download")
async def youtube_search_download(
    query: str = Form(...),
    max_results: int = Form(20),
    min_views: int = Form(0),
    min_likes: int = Form(0),
    search_pool: Optional[int] = Form(None),
    skip_completed: str = Form("true"),
    verify_file: str = Form("true"),
    format_id: str = Form("bestvideo+bestaudio/best"),
    delay_seconds: float = Form(2.0),
    download_dir: str = Form(""),
    pack_for_browser: str = Form("false"),
    deliver_files: str = Form("true"),
    date_filter: str = Form("all"),
    upload_date: str = Form(""),
):
    """搜索 → 阈值筛选 → 复用 bulk 流水线顺序下载（SSE）。"""
    do_skip = str(skip_completed).lower() in ("1", "true", "yes", "on")
    do_verify = str(verify_file).lower() in ("1", "true", "yes", "on")
    delay = max(0.0, min(float(delay_seconds), 60.0))
    pack_browser = str(pack_for_browser).lower() in ("1", "true", "yes", "on")
    do_deliver = str(deliver_files).lower() in ("1", "true", "yes", "on")
    max_results = max(1, min(int(max_results or 20), 50))
    min_views = max(0, int(min_views or 0))
    min_likes = max(0, int(min_likes or 0))
    pool = int(search_pool) if search_pool not in (None, "") else None
    df = (date_filter or "all").strip().lower()
    if df not in ("all", "today", "week", "month", "date"):
        df = "all"
    ud = (upload_date or "").strip()

    sweep_expired_bulk_tokens()

    try:
        output_dir, actually_pack, do_deliver = resolve_bulk_output(
            download_dir, pack_browser, deliver_files=do_deliver
        )
    except ValueError as e:
        async def err_stream() -> AsyncIterator[str]:
            yield fmt_sse({"event": "error", "message": friendly_download_error(e)})

        return sse_streaming_response(err_stream())

    async def event_stream() -> AsyncIterator[str]:
        yield fmt_sse(
            {
                "event": "searching",
                "query": query.strip(),
                "max_results": max_results,
                "min_views": min_views,
                "min_likes": min_likes,
            }
        )

        loop = asyncio.get_event_loop()
        try:
            found = await loop.run_in_executor(
                None,
                partial(
                    search_youtube,
                    query,
                    max_results=max_results,
                    min_views=min_views,
                    min_likes=min_likes,
                    search_pool=pool,
                    date_filter=df,
                    upload_date=ud or None,
                ),
            )
        except ValueError as e:
            yield fmt_sse({"event": "error", "message": friendly_download_error(e)})
            return
        except Exception as e:
            yield fmt_sse(
                {
                    "event": "error",
                    "message": friendly_download_error(f"YouTube 搜索失败: {e}"),
                }
            )
            return

        results = found.get("results") or []
        urls = found.get("urls") or [r.get("url") for r in results if r.get("url")]
        # 与 urls 同序的搜索标题，作统一命名回退（弱字幕时接近列表标题）
        platform_titles: list[str] | None = None
        if results and len(results) == len(urls):
            platform_titles = [(r.get("title") or "").strip() for r in results]
        yield fmt_sse(
            {
                "event": "search_done",
                "query": found.get("query"),
                "total": found.get("total", 0),
                "scanned": found.get("scanned", 0),
                "skipped_threshold": found.get("skipped_threshold", 0),
                "min_views": min_views,
                "min_likes": min_likes,
                "results": results,
                "below_threshold_results": found.get("below_threshold_results") or [],
            }
        )

        if not urls:
            skip_date = int(found.get("skipped_date") or 0)
            skip_thr = int(found.get("skipped_threshold") or 0)
            skip_live = int(found.get("skipped_live") or 0)
            scanned = int(found.get("scanned") or 0)
            if skip_live > 0 and skip_date == 0 and skip_thr == 0:
                msg = (
                    f"没有可下载的视频。已扫描 {scanned + skip_live} 条，"
                    f"其中 {skip_live} 条为直播已跳过（不下载直播）。"
                    "请更换关键词后重试。"
                )
            elif skip_date > 0 and df in ("today", "date"):
                label = "今日" if df == "today" else "所选日期"
                msg = (
                    f"没有符合条件的视频。已扫描 {scanned} 条，"
                    f"其中 {skip_date} 条不是{label}发布。"
                    "可将「发布日期」改为「不限」后重试。"
                )
            elif skip_thr > 0:
                msg = (
                    f"没有符合条件的视频。已扫描 {scanned} 条，"
                    f"因播放量/点赞未达标跳过 {skip_thr} 条。"
                    "可降低阈值后重试。"
                )
            else:
                msg = f"没有符合条件的视频。已扫描 {scanned} 条。"
            yield fmt_sse({"event": "error", "message": msg})
            return

        async for chunk in run_bulk_download_stream(
            urls,
            output_dir=output_dir,
            actually_pack=actually_pack,
            do_skip=do_skip,
            do_verify=do_verify,
            format_id=format_id,
            delay=delay,
            source_name=f"youtube:{found.get('query', query)}",
            start_extra={
                "platform": "youtube",
                "query": found.get("query"),
                "min_views": min_views,
                "min_likes": min_likes,
            },
            deliver_files=do_deliver,
            platform_titles=platform_titles,
        ):
            yield chunk

    return sse_streaming_response(event_stream())
