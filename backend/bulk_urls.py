"""从上传文件字节中提取视频链接（与 scripts/bulk_download_queue 逻辑一致）。"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any, Iterable

URL_RE = re.compile(r"https?://[^\s\]\)\"\'<>,]+", re.IGNORECASE)

DICT_URL_KEYS = (
    "share_url",
    "video_url",
    "note_url",
    "aweme_url",
    "url",
    "link",
    "video_share_url",
    "share_link",
    "short_url",
    "web_video_url",
)


def normalize_line(line: str) -> list[str]:
    line = line.strip()
    if not line or line.startswith("#"):
        return []
    found = URL_RE.findall(line)
    return [u.rstrip(").,;!?") for u in found] if found else []


def urls_from_dict(obj: dict) -> list[str]:
    out: list[str] = []
    for k in DICT_URL_KEYS:
        v = obj.get(k)
        if isinstance(v, str) and v.startswith("http"):
            out.append(v.strip())
    for v in obj.values():
        if isinstance(v, dict):
            out.extend(urls_from_dict(v))
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    out.extend(urls_from_dict(item))
                elif isinstance(item, str) and item.startswith("http"):
                    out.append(item.strip())
    return out


def _dedupe(urls: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        u = u.strip()
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def load_urls_xlsx_bytes(content: bytes) -> list[str]:
    try:
        import openpyxl
    except ImportError as e:
        raise RuntimeError("读取 Excel 需要安装 openpyxl") from e

    urls: list[str] = []
    bio = io.BytesIO(content)
    wb = openpyxl.load_workbook(bio, read_only=True, data_only=True)
    try:
        for sheet in wb.worksheets:
            for row in sheet.iter_rows(values_only=True):
                for cell in row:
                    if cell is None:
                        continue
                    s = str(cell).strip()
                    if not s or s.startswith("#"):
                        continue
                    urls.extend(normalize_line(s))
    finally:
        wb.close()
    return _dedupe(urls)


def extract_urls_from_upload(filename: str, content: bytes) -> list[str]:
    if not content:
        return []
    suffix = Path(filename or "").suffix.lower()

    if suffix in (".xlsx", ".xlsm"):
        return load_urls_xlsx_bytes(content)

    raw = content.decode("utf-8-sig")

    if suffix == ".json":
        data = json.loads(raw)
        urls: list[str] = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, str) and item.startswith("http"):
                    urls.append(item.strip())
                elif isinstance(item, dict):
                    urls.extend(urls_from_dict(item))
        elif isinstance(data, dict):
            urls.extend(urls_from_dict(data))
        return _dedupe(urls)

    if suffix == ".jsonl":
        urls = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                urls.extend(normalize_line(line))
                continue
            if isinstance(obj, str) and obj.startswith("http"):
                urls.append(obj.strip())
            elif isinstance(obj, dict):
                urls.extend(urls_from_dict(obj))
        return _dedupe(urls)

    if suffix == ".csv":
        urls = []
        f = io.StringIO(raw)
        reader = csv.DictReader(f)
        if reader.fieldnames:
            lower_map = {h.lower(): h for h in reader.fieldnames}
            for key in ("share_url", "video_url", "url", "link", "note_url", "aweme_url"):
                if key in lower_map:
                    col = lower_map[key]
                    for row in reader:
                        v = (row.get(col) or "").strip()
                        if v.startswith("http"):
                            urls.append(v)
                    return _dedupe(urls)
        for row in csv.reader(io.StringIO(raw)):
            for cell in row:
                urls.extend(normalize_line(cell))
        return _dedupe(urls)

    urls = []
    for line in raw.splitlines():
        urls.extend(normalize_line(line))
    return _dedupe(urls)
