"""一次性批量下载 ZIP 令牌（短 TTL，供浏览器 GET 下载后删除临时文件）。"""

from __future__ import annotations

import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import Any

_lock = threading.Lock()
_store: dict[str, dict[str, Any]] = {}
TTL_SEC = 3600


def _cleanup_entry(entry: dict[str, Any]) -> None:
    zp = entry.get("zip_path")
    wd = entry.get("work_dir")
    try:
        if zp:
            Path(zp).unlink(missing_ok=True)
    except OSError:
        pass
    try:
        if wd:
            shutil.rmtree(wd, ignore_errors=True)
    except OSError:
        pass


def register_bulk_zip(zip_path: str, work_dir: str) -> str:
    token = secrets.token_urlsafe(32)
    with _lock:
        _store[token] = {
            "zip_path": zip_path,
            "work_dir": work_dir,
            "expires": time.monotonic() + TTL_SEC,
        }
    return token


def claim_bulk_zip(token: str) -> dict[str, Any] | None:
    """取出并校验令牌；过期则清理文件并返回 None。"""
    now = time.monotonic()
    with _lock:
        entry = _store.pop(token, None)
    if not entry:
        return None
    if now > float(entry["expires"]):
        _cleanup_entry(entry)
        return None
    return entry


def cleanup_after_download(zip_path: str, work_dir: str) -> None:
    """HTTP 响应发送完成后删除 ZIP 与工作目录。"""
    _cleanup_entry({"zip_path": zip_path, "work_dir": work_dir})


def sweep_expired_bulk_tokens() -> None:
    """清理已过期的未下载令牌及其临时文件。"""
    now = time.monotonic()
    dead_entries: list[dict[str, Any]] = []
    with _lock:
        for t, e in list(_store.items()):
            if now > float(e["expires"]):
                old = _store.pop(t, None)
                if old:
                    dead_entries.append(old)
    for e in dead_entries:
        _cleanup_entry(e)
