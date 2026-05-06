"""批量下载完成记录：避免同一 URL 重复下载。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

_STATE_VERSION = 1


def state_file_path() -> Path:
    p = Path(__file__).resolve().parent / "data" / "bulk_download_state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def url_state_key(url: str) -> str:
    u = url.strip().rstrip(").,;!?")
    try:
        p = urlparse(u)
        netloc = (p.netloc or "").lower()
        path = (p.path or "").rstrip("/") or "/"
        return urlunparse((p.scheme.lower(), netloc, path, "", p.query, ""))
    except Exception:
        return u


def load_state() -> dict[str, Any]:
    path = state_file_path()
    if not path.is_file():
        return {"version": _STATE_VERSION, "entries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"version": _STATE_VERSION, "entries": {}}
    if not isinstance(data, dict):
        return {"version": _STATE_VERSION, "entries": {}}
    entries = data.get("entries")
    if not isinstance(entries, dict):
        entries = {}
    return {"version": _STATE_VERSION, "entries": entries}


def save_state(state: dict[str, Any]) -> None:
    path = state_file_path()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def should_skip_url(
    url: str,
    state: dict[str, Any],
    download_dir: Path,
    verify_file: bool,
) -> tuple[bool, str]:
    key = url_state_key(url)
    entries: dict = state.get("entries", {})
    rec = entries.get(key) or entries.get(url.strip())
    if not isinstance(rec, dict):
        return False, ""
    fn = (rec.get("filename") or "").strip()
    if not fn:
        return False, ""
    if verify_file:
        fp = download_dir / fn
        if fp.is_file():
            return True, "本地已存在同名文件"
        return False, ""
    return True, "已在历史记录中标记完成"


def record_success(state: dict[str, Any], url: str, filename: str, title: str) -> None:
    key = url_state_key(url)
    entries = state.setdefault("entries", {})
    entries[key] = {
        "filename": filename,
        "title": title,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
