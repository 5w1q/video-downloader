"""批量下载 ZIP/文件令牌（短 TTL）。

令牌落盘到 data/bulk_zip_tokens/，供多 uvicorn worker 共享。
此前用进程内 dict 时，ZIP 在 worker A 注册、浏览器 GET 打到 worker B 会 404，
浏览器表现为「无法下载 - 没有文件」。
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import Any

_lock = threading.Lock()
TTL_SEC = 3600


def _tokens_dir() -> Path:
    p = Path(__file__).resolve().parent / "data" / "bulk_zip_tokens"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _token_path(token: str) -> Path:
    # token_urlsafe 仅含 URL 安全字符；再过滤路径分隔，防止异常输入
    safe = "".join(c for c in token if c.isalnum() or c in ("-", "_"))
    if not safe or safe != token:
        raise ValueError("invalid token")
    return _tokens_dir() / f"{safe}.json"


def _write_entry(token: str, entry: dict[str, Any]) -> None:
    path = _token_path(token)
    tmp = path.with_suffix(".json.tmp")
    payload = json.dumps(entry, ensure_ascii=False)
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def _read_entry(token: str) -> dict[str, Any] | None:
    try:
        path = _token_path(token)
    except ValueError:
        return None
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _pop_entry(token: str) -> dict[str, Any] | None:
    try:
        path = _token_path(token)
    except ValueError:
        return None
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = None
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    return data if isinstance(data, dict) else None


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


def _now() -> float:
    return time.time()


def register_bulk_zip(zip_path: str, work_dir: str) -> str:
    token = secrets.token_urlsafe(32)
    entry = {
        "zip_path": zip_path,
        "work_dir": work_dir,
        "expires": _now() + TTL_SEC,
        "kind": "zip",
    }
    with _lock:
        _write_entry(token, entry)
    return token


def register_bulk_file(file_path: str, *, delete_after: bool = True) -> str:
    """注册单个文件供浏览器一次性下载。"""
    token = secrets.token_urlsafe(32)
    entry = {
        "zip_path": file_path,
        "work_dir": "",
        "expires": _now() + TTL_SEC,
        "kind": "file",
        "delete_after": bool(delete_after),
    }
    with _lock:
        _write_entry(token, entry)
    return token


def claim_bulk_zip(token: str) -> dict[str, Any] | None:
    """取出并校验令牌；过期则清理文件并返回 None。"""
    with _lock:
        entry = _pop_entry(token)
    if not entry:
        return None
    if _now() > float(entry.get("expires", 0)):
        _cleanup_entry(entry)
        return None
    return entry


def peek_bulk_zip(token: str) -> dict[str, Any] | None:
    """
    校验令牌但不移除（供浏览器下载 ZIP 使用）。
    避免 Chromium/Edge 等对同一 URL 的探测或重复请求在第一跳就消费令牌导致后续 GET 404。
    """
    with _lock:
        entry = _read_entry(token)
        if not entry:
            return None
        if _now() > float(entry.get("expires", 0)):
            old = _pop_entry(token)
            if old:
                _cleanup_entry(old)
            return None
    return entry


def finalize_bulk_download(token: str, zip_path: str, work_dir: str) -> None:
    """HTTP 响应发送完毕后移除令牌并删除临时文件（可多次调用，幂等）。"""
    with _lock:
        entry = _pop_entry(token)
    if entry is not None and entry.get("kind") == "file" and not entry.get("delete_after", True):
        return
    _cleanup_entry({"zip_path": zip_path, "work_dir": work_dir})


def cleanup_after_download(zip_path: str, work_dir: str) -> None:
    """HTTP 响应发送完成后删除 ZIP 与工作目录。"""
    _cleanup_entry({"zip_path": zip_path, "work_dir": work_dir})


def sweep_expired_bulk_tokens() -> None:
    """清理已过期的未下载令牌及其临时文件。"""
    now = _now()
    dead_entries: list[dict[str, Any]] = []
    with _lock:
        for path in list(_tokens_dir().glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
                continue
            if not isinstance(data, dict):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
                continue
            if now > float(data.get("expires", 0)):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
                dead_entries.append(data)
    for e in dead_entries:
        _cleanup_entry(e)
