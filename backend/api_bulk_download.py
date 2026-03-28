"""网页批量上传链接表（Excel/CSV 等），服务端顺序下载，SSE 推送进度。"""

from __future__ import annotations

import asyncio
import json
import os
from functools import partial
from pathlib import Path
from typing import AsyncIterator

from fastapi import APIRouter, File, Form, UploadFile
from starlette.responses import StreamingResponse

from bulk_state import (
    load_state,
    record_success,
    save_state,
    should_skip_url,
)
from bulk_urls import extract_urls_from_upload
from douyin import DouyinParser, is_douyin_url
from downloader import VideoDownloader

router = APIRouter(prefix="/api", tags=["批量下载"])

_downloader = VideoDownloader()
_douyin = DouyinParser(download_dir=_downloader.DOWNLOAD_DIR)


def _fmt_sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _download_one(url: str, format_id: str) -> dict:
    if is_douyin_url(url):
        return _douyin.download(url)
    return _downloader.download_video(url, format_id)


@router.post("/bulk-download")
async def bulk_download(
    file: UploadFile = File(...),
    skip_completed: str = Form("true"),
    verify_file: str = Form("true"),
    format_id: str = Form("bestvideo+bestaudio/best"),
    delay_seconds: float = Form(2.0),
):
    body = await file.read()
    name = file.filename or "upload"
    do_skip = str(skip_completed).lower() in ("1", "true", "yes", "on")
    do_verify = str(verify_file).lower() in ("1", "true", "yes", "on")
    delay = max(0.0, min(float(delay_seconds), 60.0))

    async def event_stream() -> AsyncIterator[str]:
        try:
            urls = extract_urls_from_upload(name, body)
        except Exception as e:
            yield _fmt_sse({"event": "error", "message": f"解析文件失败: {e}"})
            return

        if not urls:
            yield _fmt_sse({"event": "error", "message": "文件中未识别到任何 http(s) 链接"})
            return

        state = load_state() if do_skip else {"version": 1, "entries": {}}
        dl_dir = Path(_downloader.DOWNLOAD_DIR)

        yield _fmt_sse(
            {
                "event": "start",
                "total": len(urls),
                "source_name": name,
                "skip_enabled": do_skip,
            }
        )

        loop = asyncio.get_event_loop()
        ok = skip = fail = 0

        for i, url in enumerate(urls):
            idx = i + 1
            if do_skip:
                sk, reason = should_skip_url(url, state, dl_dir, do_verify)
                if sk:
                    skip += 1
                    yield _fmt_sse(
                        {
                            "event": "item",
                            "index": idx,
                            "total": len(urls),
                            "url": url,
                            "status": "skip",
                            "message": reason,
                        }
                    )
                    if idx < len(urls) and delay > 0:
                        await asyncio.sleep(delay)
                    continue

            try:
                result = await loop.run_in_executor(
                    None, partial(_download_one, url, format_id)
                )
                fp = result.get("filepath", "")
                if not fp or not os.path.isfile(fp):
                    raise RuntimeError("下载完成但未找到文件")

                fn = result.get("filename", "")
                title = result.get("title", "")
                ok += 1
                if do_skip:
                    record_success(state, url, fn, title)
                    save_state(state)

                yield _fmt_sse(
                    {
                        "event": "item",
                        "index": idx,
                        "total": len(urls),
                        "url": url,
                        "status": "ok",
                        "filename": fn,
                        "title": title,
                    }
                )
            except Exception as e:
                fail += 1
                yield _fmt_sse(
                    {
                        "event": "item",
                        "index": idx,
                        "total": len(urls),
                        "url": url,
                        "status": "fail",
                        "message": str(e),
                    }
                )

            if idx < len(urls) and delay > 0:
                await asyncio.sleep(delay)

        yield _fmt_sse(
            {
                "event": "done",
                "ok": ok,
                "skip": skip,
                "fail": fail,
                "total": len(urls),
            }
        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
