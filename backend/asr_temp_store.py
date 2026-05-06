"""短时 token → 音频文件路径，供阿里云 DashScope 通过公网 URL 拉取。"""

from __future__ import annotations

import os
import secrets
import shutil
import threading
import time

_lock = threading.Lock()
# token -> (abs_path, expire_mono, media_type)
_meta: dict[str, tuple[str, float, str]] = {}

DEFAULT_TTL_SEC = 1800


def _purge_locked() -> None:
    now = time.monotonic()
    dead = [k for k, (_, exp, _) in _meta.items() if exp < now]
    for k in dead:
        path, _, _ = _meta.pop(k)
        _cleanup_path(path)


def _cleanup_path(path: str) -> None:
    wd = os.path.dirname(path)
    try:
        if path and os.path.isfile(path):
            os.unlink(path)
    except OSError:
        pass
    try:
        if wd and os.path.basename(wd).startswith("asr_aliyun_") and os.path.isdir(wd):
            shutil.rmtree(wd, ignore_errors=True)
    except OSError:
        pass


def register_audio(path: str, media_type: str = "audio/mp4") -> str:
    token = secrets.token_urlsafe(32)
    with _lock:
        _purge_locked()
        _meta[token] = (path, time.monotonic() + DEFAULT_TTL_SEC, media_type)
    return token


def pop_audio(token: str) -> tuple[str, str] | None:
    with _lock:
        _purge_locked()
        row = _meta.pop(token, None)
        if not row:
            return None
        path, exp, media_type = row
        if exp < time.monotonic():
            _cleanup_path(path)
            return None
        if not os.path.isfile(path):
            return None
        return path, media_type


def abandon_token(token: str | None) -> None:
    """未成功拉取时释放 token 并删除临时目录。"""
    if not token:
        return
    with _lock:
        row = _meta.pop(token, None)
    if row:
        _cleanup_path(row[0])


def cleanup_after_response(path: str) -> None:
    """HTTP 响应发送完成后删除临时文件及目录。"""
    _cleanup_path(path)
