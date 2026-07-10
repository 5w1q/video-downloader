"""短时 token → 音频文件路径，供阿里云 DashScope 通过公网 URL 拉取。

须支持 Uvicorn 多 worker：元数据写入共享目录（默认 /app/data），避免 token 只在注册进程内存可见导致
阿里云拉音频时打到另一进程返回 404、Paraformer 静默失败。
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import threading
import time

try:
    import fcntl  # type: ignore[attr-defined]

    _HAS_FCNTL = True
except ImportError:
    fcntl = None  # type: ignore[assignment]
    _HAS_FCNTL = False

_lock = threading.Lock()
# 无 fcntl 时（例如 Windows）回退进程内 dict，仅适合单 worker 本地调试
_meta: dict[str, tuple[str, float, str]] = {}

DEFAULT_TTL_SEC = 1800


def _meta_dir() -> str:
    d = (os.getenv("ASR_PULL_META_DIR") or "/app/data/asr-pull-meta").strip()
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def _meta_path(token: str) -> str:
    # token_urlsafe 仅含 [A-Za-z0-9_\-]，可直接作文件名
    return os.path.join(_meta_dir(), token)


def _purge_expired_files() -> None:
    now = time.monotonic()
    base = _meta_dir()
    try:
        listing = os.listdir(base)
    except OSError:
        return
    for name in listing:
        fp = os.path.join(base, name)
        if not os.path.isfile(fp):
            continue
        try:
            data = None
            with open(fp, encoding="utf-8") as f:
                if _HAS_FCNTL:
                    fcntl.flock(f.fileno(), fcntl.LOCK_SH)  # type: ignore[union-attr]
                try:
                    data = json.load(f)
                finally:
                    if _HAS_FCNTL:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)  # type: ignore[union-attr]
            if data is None:
                continue
            exp = float(data.get("expire_mono", 0))
            if exp < now:
                try:
                    os.unlink(fp)
                except OSError:
                    pass
                _cleanup_path(str(data.get("path") or ""))
        except (OSError, ValueError, json.JSONDecodeError, TypeError, KeyError):
            try:
                os.unlink(fp)
            except OSError:
                pass


def _purge_locked_memory() -> None:
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
    exp = time.monotonic() + DEFAULT_TTL_SEC

    if _HAS_FCNTL:
        _purge_expired_files()
        mp = _meta_path(token)
        payload = json.dumps(
            {"path": path, "expire_mono": exp, "media_type": media_type},
            ensure_ascii=False,
        )
        tmp = mp + ".tmp"
        with open(tmp, "w", encoding="utf-8") as wf:
            wf.write(payload)
        os.replace(tmp, mp)
        return token

    with _lock:
        _purge_locked_memory()
        _meta[token] = (path, exp, media_type)
    return token


def pop_audio(token: str) -> tuple[str, str] | None:
    if not token or "/" in token or ".." in token:
        return None

    if _HAS_FCNTL:
        _purge_expired_files()
        mp = _meta_path(token)
        if not os.path.isfile(mp):
            return None
        path = ""
        exp = 0.0
        media_type = "audio/mp4"
        try:
            with open(mp, "r+", encoding="utf-8") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # type: ignore[union-attr]
                try:
                    raw = f.read()
                    data = json.loads(raw)
                    path = str(data.get("path") or "")
                    exp = float(data.get("expire_mono", 0))
                    media_type = str(data.get("media_type") or "audio/mp4")
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)  # type: ignore[union-attr]
            try:
                os.unlink(mp)
            except OSError:
                pass
        except (OSError, ValueError, json.JSONDecodeError, TypeError, KeyError):
            return None

        if exp < time.monotonic():
            _cleanup_path(path)
            return None
        if not path or not os.path.isfile(path):
            return None
        return path, media_type

    with _lock:
        _purge_locked_memory()
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
    if _HAS_FCNTL:
        mp = _meta_path(token)
        path = ""
        try:
            if os.path.isfile(mp):
                with open(mp, encoding="utf-8") as f:
                    if _HAS_FCNTL:
                        fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # type: ignore[union-attr]
                    try:
                        data = json.load(f)
                        path = str(data.get("path") or "")
                    finally:
                        if _HAS_FCNTL:
                            fcntl.flock(f.fileno(), fcntl.LOCK_UN)  # type: ignore[union-attr]
                try:
                    os.unlink(mp)
                except OSError:
                    pass
        except (OSError, ValueError, json.JSONDecodeError, TypeError, KeyError):
            try:
                os.unlink(mp)
            except OSError:
                pass
        if path:
            _cleanup_path(path)
        return

    with _lock:
        row = _meta.pop(token, None)
    if row:
        _cleanup_path(row[0])


def cleanup_after_response(path: str) -> None:
    """HTTP 响应发送完成后删除临时文件及目录。"""
    _cleanup_path(path)
