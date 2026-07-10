"""批量下载 SSE 核心：供文件上传 / URL 列表 / YouTube 搜索等入口复用。"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from functools import partial
from pathlib import Path
from typing import AsyncIterator

from bulk_state import load_state, record_success, save_state, should_skip_url
from bulk_zip_oss import oss_bulk_zip_enabled, upload_bulk_zip_get_download_url
from bulk_zip_tokens import register_bulk_file, register_bulk_zip
from douyin import DouyinParser, is_douyin_url
from downloader import VideoDownloader
from tiktok import TikTokParser, is_tiktok_url

_downloader = VideoDownloader()
_douyin = DouyinParser(download_dir=_downloader.DOWNLOAD_DIR)
_tiktok = TikTokParser(download_dir=_downloader.DOWNLOAD_DIR)


def fmt_sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _friendly_download_error(exc: BaseException) -> str:
    msg = str(exc)
    low = msg.lower()
    if "sign in to confirm" in low or "not a bot" in low:
        return (
            "YouTube 要求登录验证（bot 检测）。请在生产环境配置 YOUTUBE_COOKIEFILE"
            "（Netscape cookies.txt，勿复用 B 站 Cookie），并确认出口代理可用后重试。"
        )
    if "failed to load cookies" in low:
        return (
            "无法加载浏览器 Cookie。请改为导出 YouTube cookies.txt 并设置 YOUTUBE_COOKIEFILE，"
            "或关闭占用 Cookie 数据库的浏览器后重试。"
        )
    return msg


def default_download_dir() -> Path:
    return Path(_downloader.DOWNLOAD_DIR)


# 视频/音频/图片等已是压缩格式，DEFLATE 几乎无法再缩小，仅增加 CPU；改用 ZIP_STORED
_STORE_SUFFIXES = frozenset({
    ".mp4", ".webm", ".mkv", ".mov", ".avi", ".flv", ".m4v", ".ts", ".wmv", ".3gp",
    ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".flac",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic",
})


def bulk_zip_limits() -> tuple[int, int]:
    try:
        max_bytes = int(os.getenv("BULK_ZIP_MAX_BYTES", str(2 * 1024**3)))
    except ValueError:
        max_bytes = 2 * 1024**3
    try:
        max_files = int(os.getenv("BULK_ZIP_MAX_FILES", "25"))
    except ValueError:
        max_files = 25
    max_bytes = max(100 * 1024 * 1024, min(max_bytes, 10 * 1024**3))
    max_files = max(1, min(max_files, 200))
    return max_bytes, max_files


def _remove_paths(paths: list[str]) -> None:
    for fp in paths:
        try:
            Path(fp).unlink(missing_ok=True)
        except OSError:
            pass


def _publish_zip_file(zip_path: str) -> str:
    if oss_bulk_zip_enabled():
        return upload_bulk_zip_get_download_url(zip_path)
    tok = register_bulk_zip(zip_path, "")
    return f"/api/bulk-download/zip/{tok}"


def _publish_single_file(file_path: str, *, delete_after: bool = True) -> str:
    tok = register_bulk_file(file_path, delete_after=delete_after)
    return f"/api/bulk-download/file/{tok}"


def _zip_compress_type(path: Path) -> int:
    import zipfile

    if path.suffix.lower() in _STORE_SUFFIXES:
        return zipfile.ZIP_STORED
    return zipfile.ZIP_DEFLATED


def _zip_file_list(file_paths: list[str], zip_path: str) -> dict[str, int]:
    import zipfile

    used: dict[str, int] = {}
    source_bytes = 0
    file_count = 0
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fp in file_paths:
            p = Path(fp)
            if not p.is_file():
                continue
            file_count += 1
            source_bytes += p.stat().st_size
            base = p.name
            n = used.get(base, 0)
            used[base] = n + 1
            if n == 0:
                arcname = base
            else:
                arcname = f"{p.stem}_{n}{p.suffix}"
            zf.write(p, arcname=arcname, compress_type=_zip_compress_type(p))
    zip_bytes = Path(zip_path).stat().st_size
    return {"zip_bytes": zip_bytes, "source_bytes": source_bytes, "file_count": file_count}


def _bulk_zip_staging_dir() -> Path:
    """ZIP 落在 data 卷上，与令牌同卷，多 worker 均可读。"""
    p = Path(__file__).resolve().parent / "data" / "bulk_zips"
    p.mkdir(parents=True, exist_ok=True)
    return p


async def _pack_and_publish_chunk(
    loop: asyncio.AbstractEventLoop,
    file_paths: list[str],
    part_num: int,
) -> dict:
    fd, zip_path = tempfile.mkstemp(suffix=".zip", dir=str(_bulk_zip_staging_dir()))
    os.close(fd)
    try:
        stats = await loop.run_in_executor(
            None, partial(_zip_file_list, file_paths, zip_path)
        )
        if not Path(zip_path).is_file() or Path(zip_path).stat().st_size == 0:
            raise RuntimeError("ZIP 生成失败")
        zip_url = await loop.run_in_executor(None, partial(_publish_zip_file, zip_path))
        await loop.run_in_executor(None, partial(_remove_paths, file_paths))
        return {
            "part": part_num,
            "url": zip_url,
            "zip_bytes": stats["zip_bytes"],
            "source_bytes": stats["source_bytes"],
            "file_count": stats["file_count"],
        }
    except Exception:
        try:
            Path(zip_path).unlink(missing_ok=True)
        except OSError:
            pass
        raise


def download_one(url: str, format_id: str, output_dir: Path) -> dict:
    out = str(output_dir)
    if is_douyin_url(url):
        return _douyin.download(url, out_dir=out)
    if is_tiktok_url(url):
        return _tiktok.download(url, out_dir=out)
    return _downloader.download_video(url, format_id, out_dir=out)


async def run_bulk_download_stream(
    urls: list[str],
    *,
    output_dir: Path,
    actually_pack: bool,
    do_skip: bool,
    do_verify: bool,
    format_id: str,
    delay: float,
    source_name: str = "urls",
    start_extra: dict | None = None,
    deliver_files: bool = False,
    download_urls: list[str] | None = None,
) -> AsyncIterator[str]:
    """对 URL 列表顺序下载，yield SSE 文本块。

    - actually_pack: 完成后/分卷打包 ZIP
    - deliver_files: 每下完一个文件就发浏览器可下载链接（不打 ZIP）
    - download_urls: 可选，与 urls 等长；跳过/历史用 urls[i]，实际下载用 download_urls[i]
      （Instagram 等：页面 URL 记历史，CDN video_url 直下）
    """
    zip_registered = False
    zip_parts: list[dict] = []
    chunk_paths: list[str] = []
    chunk_bytes = 0
    chunk_max_bytes, chunk_max_files = bulk_zip_limits()

    try:
        if not urls:
            yield fmt_sse({"event": "error", "message": "没有可下载的链接"})
            return

        if download_urls is not None and len(download_urls) != len(urls):
            yield fmt_sse(
                {
                    "event": "error",
                    "message": "download_urls 与 urls 长度不一致",
                }
            )
            return

        state = load_state() if do_skip else {"version": 1, "entries": {}}
        dl_dir = output_dir
        # 浏览器投递 / ZIP 临时目录下完即删，验本地文件永远失败；
        # 跳过开启时强制按历史记录跳过（忽略 verify_file）。
        ephemeral = bool(deliver_files) or (
            bool(actually_pack) and str(getattr(output_dir, "name", "")).startswith("bulk_dl_")
        )
        effective_verify = bool(do_verify) and not ephemeral

        start_payload = {
            "event": "start",
            "total": len(urls),
            "source_name": source_name,
            "skip_enabled": do_skip,
            "verify_file": effective_verify,
            "ephemeral_output": ephemeral,
            "output_dir": str(output_dir),
            "browser_zip": bool(actually_pack),
            "deliver_files": bool(deliver_files),
        }
        if start_extra:
            start_payload.update(start_extra)
        if actually_pack:
            start_payload["zip_chunk_max_bytes"] = chunk_max_bytes
            start_payload["zip_chunk_max_files"] = chunk_max_files
        yield fmt_sse(start_payload)

        loop = asyncio.get_event_loop()
        ok = skip = fail = 0

        async def flush_zip_chunk(force: bool = False) -> dict | None:
            nonlocal chunk_paths, chunk_bytes
            if not chunk_paths:
                return None
            if not force and len(chunk_paths) < chunk_max_files and chunk_bytes < chunk_max_bytes:
                return None
            paths = chunk_paths
            chunk_paths = []
            chunk_bytes = 0
            part_num = len(zip_parts) + 1
            part = await _pack_and_publish_chunk(loop, paths, part_num)
            zip_parts.append(part)
            return part

        for i, url in enumerate(urls):
            idx = i + 1
            dl_url = (download_urls[i] if download_urls else url) or url
            if do_skip:
                sk, reason = should_skip_url(url, state, dl_dir, effective_verify)
                if sk:
                    skip += 1
                    yield fmt_sse(
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
                    None, partial(download_one, dl_url, format_id, output_dir)
                )
                fp = result.get("filepath", "")
                if not fp or not os.path.isfile(fp):
                    raise RuntimeError("下载完成但未找到文件")

                fn = result.get("filename", "")
                title = result.get("title", "")
                ok += 1
                file_url = ""
                if deliver_files and not actually_pack:
                    try:
                        file_url = await loop.run_in_executor(
                            None,
                            partial(_publish_single_file, fp, delete_after=True),
                        )
                    except Exception as e:
                        yield fmt_sse(
                            {
                                "event": "error",
                                "message": f"生成浏览器下载链接失败: {e}",
                            }
                        )
                if actually_pack:
                    fsize = os.path.getsize(fp)
                    chunk_paths.append(fp)
                    chunk_bytes += fsize
                    if len(chunk_paths) >= chunk_max_files or chunk_bytes >= chunk_max_bytes:
                        try:
                            part = await flush_zip_chunk(force=True)
                            if part:
                                zip_registered = True
                                yield fmt_sse(
                                    {
                                        "event": "zip_part",
                                        **part,
                                        "parts_ready": len(zip_parts),
                                    }
                                )
                        except Exception as e:
                            yield fmt_sse(
                                {
                                    "event": "error",
                                    "message": f"打包 ZIP 分卷失败: {e}",
                                }
                            )
                if do_skip:
                    record_success(state, url, fn, title)
                    save_state(state)

                item_payload = {
                    "event": "item",
                    "index": idx,
                    "total": len(urls),
                    "url": url,
                    "status": "ok",
                    "filename": fn,
                    "title": title,
                }
                if file_url:
                    item_payload["file_url"] = file_url
                    item_payload["deliver"] = True
                yield fmt_sse(item_payload)
                if file_url:
                    yield fmt_sse(
                        {
                            "event": "file_part",
                            "index": idx,
                            "total": len(urls),
                            "url": file_url,
                            "filename": fn,
                            "title": title,
                        }
                    )

            except Exception as e:
                fail += 1
                yield fmt_sse(
                    {
                        "event": "item",
                        "index": idx,
                        "total": len(urls),
                        "url": url,
                        "status": "fail",
                        "message": _friendly_download_error(e),
                    }
                )

            if idx < len(urls) and delay > 0:
                await asyncio.sleep(delay)

        if actually_pack:
            if chunk_paths:
                try:
                    part = await flush_zip_chunk(force=True)
                    if part:
                        zip_registered = True
                        yield fmt_sse(
                            {
                                "event": "zip_part",
                                **part,
                                "parts_ready": len(zip_parts),
                            }
                        )
                except Exception as e:
                    yield fmt_sse(
                        {
                            "event": "error",
                            "message": f"打包 ZIP 分卷失败: {e}",
                        }
                    )
            shutil.rmtree(str(output_dir), ignore_errors=True)
            if zip_parts:
                zip_registered = True

        done_payload: dict = {
            "event": "done",
            "ok": ok,
            "skip": skip,
            "fail": fail,
            "total": len(urls),
            "browser_zip": bool(actually_pack),
            "deliver_files": bool(deliver_files),
        }
        if zip_parts:
            done_payload["zip_parts"] = zip_parts
            done_payload["zip_url"] = zip_parts[0]["url"]
            done_payload["zip_bytes"] = sum(p["zip_bytes"] for p in zip_parts)
            done_payload["source_bytes"] = sum(p["source_bytes"] for p in zip_parts)
            done_payload["zip_file_count"] = sum(p["file_count"] for p in zip_parts)
            done_payload["zip_part_count"] = len(zip_parts)
        yield fmt_sse(done_payload)
    finally:
        if actually_pack and not zip_registered:
            shutil.rmtree(str(output_dir), ignore_errors=True)
