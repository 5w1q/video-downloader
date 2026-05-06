"""网页批量上传链接表（Excel/CSV 等），服务端顺序下载，SSE 推送进度。"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
import zipfile
from functools import partial
from pathlib import Path
from typing import AsyncIterator

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from bulk_state import (
    load_state,
    record_success,
    save_state,
    should_skip_url,
)
from bulk_urls import extract_urls_from_upload
from bulk_zip_tokens import claim_bulk_zip, cleanup_after_download, register_bulk_zip, sweep_expired_bulk_tokens
from douyin import DouyinParser, is_douyin_url
from downloader import VideoDownloader

router = APIRouter(prefix="/api", tags=["批量下载"])

_downloader = VideoDownloader()
_douyin = DouyinParser(download_dir=_downloader.DOWNLOAD_DIR)


def _fmt_sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


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


def _zip_file_list(file_paths: list[str], zip_path: str) -> None:
    """将文件列表写入 ZIP；重名文件自动加序号。"""
    used: dict[str, int] = {}
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fp in file_paths:
            p = Path(fp)
            if not p.is_file():
                continue
            base = p.name
            n = used.get(base, 0)
            used[base] = n + 1
            if n == 0:
                arcname = base
            else:
                arcname = f"{p.stem}_{n}{p.suffix}"
            zf.write(p, arcname=arcname)


def _download_one(url: str, format_id: str, output_dir: Path) -> dict:
    out = str(output_dir)
    if is_douyin_url(url):
        return _douyin.download(url, out_dir=out)
    return _downloader.download_video(url, format_id, out_dir=out)


@router.get("/bulk-download/zip/{token}")
async def download_bulk_zip(token: str, background_tasks: BackgroundTasks):
    """一次性下载批量任务生成的 ZIP；响应结束后删除临时文件。"""
    info = claim_bulk_zip(token)
    if not info:
        raise HTTPException(status_code=404, detail="下载链接已失效或不存在。")
    zp = info["zip_path"]
    work = info["work_dir"]
    if not Path(zp).is_file():
        cleanup_after_download(zp, work)
        raise HTTPException(status_code=404, detail="文件已过期或已删除。")

    background_tasks.add_task(cleanup_after_download, zp, work)
    return FileResponse(
        zp,
        filename="batch-download.zip",
        media_type="application/zip",
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
):
    body = await file.read()
    name = file.filename or "upload"
    do_skip = str(skip_completed).lower() in ("1", "true", "yes", "on")
    do_verify = str(verify_file).lower() in ("1", "true", "yes", "on")
    delay = max(0.0, min(float(delay_seconds), 60.0))
    pack_browser = str(pack_for_browser).lower() in ("1", "true", "yes", "on")

    default_dir = Path(_downloader.DOWNLOAD_DIR)
    raw_dir = str(download_dir or "").strip()
    sweep_expired_bulk_tokens()

    actually_pack = False
    output_dir: Path
    try:
        if raw_dir:
            _validate_custom_download_dir(raw_dir)
            output_dir = _resolve_bulk_output_dir(raw_dir, default_dir)
            actually_pack = False
        else:
            if pack_browser:
                output_dir = Path(tempfile.mkdtemp(prefix="bulk_dl_", dir=None))
                actually_pack = True
            else:
                output_dir = _resolve_bulk_output_dir("", default_dir)
                actually_pack = False
    except ValueError as e:
        async def err_stream() -> AsyncIterator[str]:
            yield _fmt_sse({"event": "error", "message": str(e)})

        return StreamingResponse(
            err_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    async def event_stream() -> AsyncIterator[str]:
        success_paths: list[str] = []
        zip_path_made: str | None = None
        zip_registered = False
        try:
            try:
                urls = extract_urls_from_upload(name, body)
            except Exception as e:
                yield _fmt_sse({"event": "error", "message": f"解析文件失败: {e}"})
                return

            if not urls:
                yield _fmt_sse({"event": "error", "message": "文件中未识别到任何 http(s) 链接"})
                return

            state = load_state() if do_skip else {"version": 1, "entries": {}}
            dl_dir = output_dir

            start_payload = {
                "event": "start",
                "total": len(urls),
                "source_name": name,
                "skip_enabled": do_skip,
                "output_dir": str(output_dir),
                "browser_zip": bool(actually_pack),
            }
            yield _fmt_sse(start_payload)

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
                        None, partial(_download_one, url, format_id, output_dir)
                    )
                    fp = result.get("filepath", "")
                    if not fp or not os.path.isfile(fp):
                        raise RuntimeError("下载完成但未找到文件")

                    fn = result.get("filename", "")
                    title = result.get("title", "")
                    ok += 1
                    if actually_pack:
                        success_paths.append(fp)
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

            zip_url: str | None = None
            if actually_pack and ok > 0 and success_paths:
                try:
                    fd, zip_path = tempfile.mkstemp(suffix=".zip")
                    os.close(fd)
                    zip_path_made = zip_path
                    await loop.run_in_executor(
                        None, partial(_zip_file_list, success_paths, zip_path)
                    )
                    if not Path(zip_path).is_file() or Path(zip_path).stat().st_size == 0:
                        raise RuntimeError("ZIP 生成失败")
                    tok = register_bulk_zip(zip_path, str(output_dir))
                    zip_registered = True
                    zip_url = f"/api/bulk-download/zip/{tok}"
                    # ZIP 已含全部内容，立即删除临时目录中的源视频，减轻服务器磁盘占用
                    shutil.rmtree(str(output_dir), ignore_errors=True)
                except Exception as e:
                    yield _fmt_sse(
                        {
                            "event": "error",
                            "message": f"打包 ZIP 失败: {e}",
                        }
                    )
                    if zip_path_made:
                        try:
                            Path(zip_path_made).unlink(missing_ok=True)
                        except OSError:
                            pass
                        zip_path_made = None
                    if actually_pack:
                        shutil.rmtree(str(output_dir), ignore_errors=True)

            done_payload: dict = {
                "event": "done",
                "ok": ok,
                "skip": skip,
                "fail": fail,
                "total": len(urls),
                "browser_zip": bool(actually_pack),
            }
            if zip_url:
                done_payload["zip_url"] = zip_url
            yield _fmt_sse(done_payload)
        finally:
            if actually_pack and not zip_registered:
                shutil.rmtree(str(output_dir), ignore_errors=True)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
