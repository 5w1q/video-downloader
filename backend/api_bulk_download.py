"""网页批量上传链接表（Excel/CSV 等），服务端顺序下载，SSE 推送进度。"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import AsyncIterator

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from bulk_download_core import (
    default_download_dir,
    fmt_sse,
    run_bulk_download_stream,
)
from bulk_urls import extract_urls_from_upload
from bulk_zip_tokens import finalize_bulk_download, peek_bulk_zip, sweep_expired_bulk_tokens

router = APIRouter(prefix="/api", tags=["批量下载"])


class BulkUrlsRequest(BaseModel):
    urls: list[str] = Field(..., min_length=1, max_length=200)
    skip_completed: bool = True
    verify_file: bool = True
    format_id: str = "bestvideo+bestaudio/best"
    delay_seconds: float = 2.0
    download_dir: str = ""
    pack_for_browser: bool = False
    deliver_files: bool = True
    source_name: str = "preview"


def _validate_custom_download_dir(raw: str) -> None:
    """非空保存目录在 Linux/Docker 下的合法性校验。"""
    s = str(raw).strip()
    if not s:
        return
    if "\x00" in s:
        raise ValueError("路径包含非法字符")
    if os.name != "nt" and re.match(r"(?i)^[a-z]:[\\/]", s):
        raise ValueError(
            "当前为 Linux/Docker 环境，不能使用 Windows 盘符路径（如 D:\\…）。"
            "请使用以 / 开头的绝对路径并在 docker-compose 中挂载该目录；"
            "或留空并勾选「完成后下载 ZIP 到本机」。"
        )
    if os.name != "nt" and not s.startswith("/"):
        raise ValueError(
            "在 Linux/Docker 下保存目录请使用绝对路径（以 / 开头），例如 /data/bulk。"
        )


def _resolve_bulk_output_dir(raw: str | None, default: Path) -> Path:
    """将用户填写路径解析为绝对目录；空则使用默认 downloads。"""
    if not raw or not str(raw).strip():
        return default
    s = str(raw).strip()
    if "\x00" in s:
        raise ValueError("路径包含非法字符")
    p = Path(s).expanduser()
    try:
        out = p.resolve(strict=False)
    except (OSError, RuntimeError) as e:
        raise ValueError(f"无法解析路径: {e}") from e
    out.mkdir(parents=True, exist_ok=True)
    if not out.is_dir():
        raise ValueError("路径不是目录")
    return out


def resolve_bulk_output(
    download_dir: str,
    pack_for_browser: bool,
    *,
    deliver_files: bool = False,
) -> tuple[Path, bool, bool]:
    """解析输出目录与交付方式。

    返回 (output_dir, actually_pack, deliver_files)。
    - pack_for_browser=True 且填写服务器目录：服务器保存 + 打包 ZIP
    - pack_for_browser=False：浏览器逐文件下载（临时目录，不打 ZIP）
    - 仅填写服务器目录且不打包：服务器保存、不打包
    """
    default_dir = default_download_dir()
    raw_dir = str(download_dir or "").strip()
    want_zip = bool(pack_for_browser)
    want_deliver = bool(deliver_files) and not want_zip

    if raw_dir:
        _validate_custom_download_dir(raw_dir)
        out = _resolve_bulk_output_dir(raw_dir, default_dir)
        # 服务器目录 + 勾选打包 → ZIP；否则只落盘
        return out, want_zip, False

    if want_zip:
        # 打包 ZIP 后下到浏览器：用临时目录，不落默认 downloads
        return Path(tempfile.mkdtemp(prefix="bulk_dl_", dir=None)), True, False

    if want_deliver:
        return Path(tempfile.mkdtemp(prefix="bulk_dl_", dir=None)), False, True

    return _resolve_bulk_output_dir("", default_dir), False, False


def sse_streaming_response(gen: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        gen,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/bulk-download/zip/{token}")
async def download_bulk_zip(token: str, background_tasks: BackgroundTasks):
    """一次性下载批量任务生成的 ZIP；响应结束后删除临时文件。"""
    info = peek_bulk_zip(token)
    if not info:
        raise HTTPException(status_code=404, detail="下载链接已失效或不存在。")
    zp = info["zip_path"]
    work = info["work_dir"]
    if not Path(zp).is_file():
        finalize_bulk_download(token, zp, work)
        raise HTTPException(status_code=404, detail="文件已过期或已删除。")

    background_tasks.add_task(finalize_bulk_download, token, zp, work)
    return FileResponse(
        zp,
        filename="batch-download.zip",
        media_type="application/zip",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/bulk-download/file/{token}")
async def download_bulk_file(token: str, background_tasks: BackgroundTasks):
    """一次性下载单个视频文件到浏览器下载目录。"""
    info = peek_bulk_zip(token)
    if not info:
        raise HTTPException(status_code=404, detail="下载链接已失效或不存在。")
    fp = info["zip_path"]
    work = info.get("work_dir") or ""
    if not Path(fp).is_file():
        finalize_bulk_download(token, fp, work)
        raise HTTPException(status_code=404, detail="文件已过期或已删除。")

    background_tasks.add_task(finalize_bulk_download, token, fp, work)
    name = Path(fp).name or "video.mp4"
    return FileResponse(
        fp,
        filename=name,
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/bulk-download")
async def bulk_download(
    file: UploadFile = File(...),
    skip_completed: str = Form("true"),
    verify_file: str = Form("true"),
    format_id: str = Form("bestvideo+bestaudio/best"),
    delay_seconds: float = Form(2.0),
    download_dir: str = Form(""),
    pack_for_browser: str = Form("true"),
    deliver_files: str = Form("false"),
):
    body = await file.read()
    name = file.filename or "upload"
    do_skip = str(skip_completed).lower() in ("1", "true", "yes", "on")
    do_verify = str(verify_file).lower() in ("1", "true", "yes", "on")
    delay = max(0.0, min(float(delay_seconds), 60.0))
    pack_browser = str(pack_for_browser).lower() in ("1", "true", "yes", "on")
    do_deliver = str(deliver_files).lower() in ("1", "true", "yes", "on")

    sweep_expired_bulk_tokens()

    try:
        output_dir, actually_pack, do_deliver = resolve_bulk_output(
            download_dir, pack_browser, deliver_files=do_deliver
        )
    except ValueError as e:
        async def err_stream() -> AsyncIterator[str]:
            yield fmt_sse({"event": "error", "message": str(e)})

        return sse_streaming_response(err_stream())

    async def event_stream() -> AsyncIterator[str]:
        try:
            urls = extract_urls_from_upload(name, body)
        except Exception as e:
            yield fmt_sse({"event": "error", "message": f"解析文件失败: {e}"})
            return

        if not urls:
            yield fmt_sse({"event": "error", "message": "文件中未识别到任何 http(s) 链接"})
            return

        async for chunk in run_bulk_download_stream(
            urls,
            output_dir=output_dir,
            actually_pack=actually_pack,
            do_skip=do_skip,
            do_verify=do_verify,
            format_id=format_id,
            delay=delay,
            source_name=name,
            deliver_files=do_deliver,
        ):
            yield chunk

    return sse_streaming_response(event_stream())


@router.post("/bulk-download/urls")
async def bulk_download_urls(body: BulkUrlsRequest):
    """按 URL 列表批量下载（供 YouTube 预览后直接下载，无需重新搜索）。"""
    urls: list[str] = []
    seen: set[str] = set()
    for raw in body.urls:
        u = str(raw or "").strip()
        if not u.startswith(("http://", "https://")):
            continue
        if u in seen:
            continue
        seen.add(u)
        urls.append(u)
        if len(urls) >= 200:
            break

    if not urls:
        raise HTTPException(status_code=400, detail="没有有效的 http(s) 链接")

    delay = max(0.0, min(float(body.delay_seconds), 60.0))
    sweep_expired_bulk_tokens()

    try:
        output_dir, actually_pack, do_deliver = resolve_bulk_output(
            body.download_dir,
            body.pack_for_browser,
            deliver_files=body.deliver_files,
        )
    except ValueError as e:
        async def err_stream() -> AsyncIterator[str]:
            yield fmt_sse({"event": "error", "message": str(e)})

        return sse_streaming_response(err_stream())

    source = (body.source_name or "preview").strip() or "preview"

    async def event_stream() -> AsyncIterator[str]:
        async for chunk in run_bulk_download_stream(
            urls,
            output_dir=output_dir,
            actually_pack=actually_pack,
            do_skip=bool(body.skip_completed),
            do_verify=bool(body.verify_file),
            format_id=body.format_id or "bestvideo+bestaudio/best",
            delay=delay,
            source_name=source,
            deliver_files=do_deliver,
        ):
            yield chunk

    return sse_streaming_response(event_stream())
