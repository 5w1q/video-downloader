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
from downloader import VideoDownloader, friendly_download_error
from tiktok import TikTokParser, is_tiktok_url

_downloader = VideoDownloader()
_douyin = DouyinParser(download_dir=_downloader.DOWNLOAD_DIR)
_tiktok = TikTokParser(download_dir=_downloader.DOWNLOAD_DIR)


def fmt_sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _friendly_download_error(exc: BaseException) -> str:
    return friendly_download_error(exc)


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


def _remove_paths(paths: list) -> None:
    for item in paths:
        fp = item[0] if isinstance(item, (tuple, list)) and item else item
        try:
            Path(str(fp)).unlink(missing_ok=True)
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


def _zip_write_member(zf, path: Path, arcname: str) -> None:
    """写入 ZIP 成员；arcname 为对外文件名（内容标题），流式写入避免大视频占内存。"""
    safe = Path(arcname).name or path.name or "video.bin"
    # ZipFile.write 对流式拷贝；含非 ASCII 时会自动打 UTF-8 语言编码标志
    zf.write(str(path), arcname=safe, compress_type=_zip_compress_type(path))
    if any(ord(c) > 127 for c in safe):
        try:
            zf.getinfo(safe).flag_bits |= 0x800
        except KeyError:
            pass


def _zip_file_list(
    file_paths: list,
    zip_path: str,
) -> dict[str, int]:
    """打包文件列表。file_paths 元素为 str 路径，或 (path, arcname) 元组。"""
    import zipfile

    used: dict[str, int] = {}
    source_bytes = 0
    file_count = 0
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for item in file_paths:
            if isinstance(item, (tuple, list)) and len(item) >= 2:
                fp, arc = str(item[0]), str(item[1] or "")
            else:
                fp, arc = str(item), ""
            p = Path(fp)
            if not p.is_file():
                continue
            file_count += 1
            source_bytes += p.stat().st_size
            base = Path(arc).name if arc else p.name
            if not base:
                base = p.name
            n = used.get(base, 0)
            used[base] = n + 1
            if n == 0:
                arcname = base
            else:
                stem = Path(base).stem
                suffix = Path(base).suffix
                arcname = f"{stem}_{n}{suffix}"
            _zip_write_member(zf, p, arcname)
    zip_bytes = Path(zip_path).stat().st_size
    return {"zip_bytes": zip_bytes, "source_bytes": source_bytes, "file_count": file_count}



def _bulk_zip_staging_dir() -> Path:
    """ZIP 落在 data 卷上，与令牌同卷，多 worker 均可读。"""
    p = Path(__file__).resolve().parent / "data" / "bulk_zips"
    p.mkdir(parents=True, exist_ok=True)
    return p


async def _pack_and_publish_chunk(
    loop: asyncio.AbstractEventLoop,
    file_paths: list,
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


def download_one(
    url: str,
    format_id: str,
    output_dir: Path,
    *,
    platform_title: str = "",
    title_url: str = "",
) -> dict:
    """下载单条。url 为实际下载地址；title_url / platform_title 供统一命名。"""
    out = str(output_dir)
    pt = (platform_title or "").strip()
    tu = (title_url or "").strip()
    if is_douyin_url(url):
        return _douyin.download(url, out_dir=out, platform_title=pt or None)
    if is_tiktok_url(url):
        return _tiktok.download(url, out_dir=out, platform_title=pt or None)
    return _downloader.download_video(
        url,
        format_id,
        out_dir=out,
        platform_title=pt or None,
        title_url=tu or None,
    )


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
    platform_titles: list[str] | None = None,
) -> AsyncIterator[str]:
    """对 URL 列表顺序下载，yield SSE 文本块。

    - actually_pack: 完成后/分卷打包 ZIP
    - deliver_files: 每下完一个文件就发浏览器可下载链接（不打 ZIP）
    - download_urls: 可选，与 urls 等长；跳过/历史用 urls[i]，实际下载用 download_urls[i]
      （Instagram 等：页面 URL 记历史，CDN video_url 直下）
    - platform_titles: 可选，与 urls 等长；搜索侧标题，作命名回退
    """
    zip_registered = False
    zip_parts: list[dict] = []
    # (filepath, arcname) — arcname 用下载结果 filename（内容标题），保证 ZIP 内名正确
    chunk_paths: list[tuple[str, str]] = []
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

        if platform_titles is not None and len(platform_titles) != len(urls):
            yield fmt_sse(
                {
                    "event": "error",
                    "message": "platform_titles 与 urls 长度不一致",
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
                pt = (
                    (platform_titles[i] or "").strip()
                    if platform_titles is not None
                    else ""
                )
                # 命名/字幕用页面 URL（urls[i]）；实际下载优先用直链（CDN）。
                # 直链可能过期或绑抓取侧 IP（如 Instagram/Apify），失败时回退到
                # 页面 URL，由 yt-dlp 重新解析出新鲜地址再下。
                candidate_urls = [dl_url] if dl_url == url else [dl_url, url]
                result = None
                last_exc: BaseException | None = None
                for cand in candidate_urls:
                    try:
                        result = await loop.run_in_executor(
                            None,
                            partial(
                                download_one,
                                cand,
                                format_id,
                                output_dir,
                                platform_title=pt,
                                title_url=url,
                            ),
                        )
                        last_exc = None
                        break
                    except Exception as inner:
                        last_exc = inner
                if last_exc is not None:
                    raise last_exc
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
                                "message": friendly_download_error(
                                    f"生成浏览器下载链接失败: {e}"
                                ),
                            }
                        )
                if actually_pack:
                    fsize = os.path.getsize(fp)
                    arc = (fn or Path(fp).name or "video.mp4").strip() or "video.mp4"
                    # 盘上若仍是裸 id，但已有可读 title，ZIP 内名强制用标题
                    try:
                        from video_title import looks_like_weak_title, sanitize_download_basename

                        stem = Path(arc).stem
                        if title and looks_like_weak_title(stem):
                            safe = sanitize_download_basename(title)
                            suf = Path(arc).suffix or ".mp4"
                            arc = f"{safe}{suf}"
                    except Exception:
                        pass
                    chunk_paths.append((fp, arc))
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
                                    "message": friendly_download_error(
                                        f"打包 ZIP 分卷失败: {e}"
                                    ),
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
                            "message": friendly_download_error(
                                f"打包 ZIP 分卷失败: {e}"
                            ),
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
