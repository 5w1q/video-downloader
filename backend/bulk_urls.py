"""从上传文件字节中提取视频链接（与 scripts/bulk_download_queue 逻辑一致）。

支持可选标题列 / JSON 字段，供统一命名回退（platform_titles）。
"""

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

# 页面/分享链优先；CDN 直链单独作 download_url
PAGE_URL_KEYS = (
    "share_url",
    "note_url",
    "aweme_url",
    "url",
    "link",
    "video_share_url",
    "share_link",
    "short_url",
    "web_video_url",
)
CDN_URL_KEYS = ("video_url", "video_src", "content_url")
TITLE_KEYS = ("title", "name", "caption", "video_title", "desc", "description")


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


def _pick_str(obj: dict, keys: tuple[str, ...]) -> str:
    for k in keys:
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            s = v.strip()
            if k in CDN_URL_KEYS or k in PAGE_URL_KEYS or k in DICT_URL_KEYS:
                if s.startswith("http"):
                    return s
            else:
                return s
        if isinstance(v, dict) and k in TITLE_KEYS:
            t = v.get("text")
            if isinstance(t, str) and t.strip():
                return t.strip()
    return ""


def entry_from_dict(obj: dict) -> list[dict[str, str]]:
    """从单个 dict 提取条目（含可选 title / download_url）。"""
    page = _pick_str(obj, PAGE_URL_KEYS)
    cdn = _pick_str(obj, CDN_URL_KEYS)
    title = _pick_str(obj, TITLE_KEYS)
    out: list[dict[str, str]] = []
    if page:
        e: dict[str, str] = {"url": page, "title": title}
        if cdn and cdn != page:
            e["download_url"] = cdn
        out.append(e)
    elif cdn:
        out.append({"url": cdn, "title": title})
    for v in obj.values():
        if isinstance(v, dict):
            out.extend(entry_from_dict(v))
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    out.extend(entry_from_dict(item))
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


def _dedupe_entries(entries: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    """按页面 url 去重，保留首次出现的 title / download_url。"""
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for raw in entries:
        url = (raw.get("url") or "").strip()
        if not url or not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        e: dict[str, str] = {"url": url}
        title = (raw.get("title") or "").strip()
        if title:
            e["title"] = title
        dl = (raw.get("download_url") or "").strip()
        if dl.startswith("http") and dl != url:
            e["download_url"] = dl
        out.append(e)
    return out


def _entries_from_urls(urls: Iterable[str]) -> list[dict[str, str]]:
    return [{"url": u} for u in _dedupe(urls)]


def _header_lower_map(fieldnames: Iterable[str] | None) -> dict[str, str]:
    if not fieldnames:
        return {}
    return {str(h).strip().lower(): h for h in fieldnames if h is not None and str(h).strip()}


def _find_col(lower_map: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        if key in lower_map:
            return lower_map[key]
    return None


def load_entries_xlsx_bytes(content: bytes) -> list[dict[str, str]]:
    try:
        import openpyxl
    except ImportError as e:
        raise RuntimeError("读取 Excel 需要安装 openpyxl") from e

    bio = io.BytesIO(content)
    wb = openpyxl.load_workbook(bio, read_only=True, data_only=True)
    entries: list[dict[str, str]] = []
    try:
        for sheet in wb.worksheets:
            rows = list(sheet.iter_rows(values_only=True))
            if not rows:
                continue
            first = ["" if c is None else str(c).strip() for c in rows[0]]
            lower = [c.lower() for c in first]
            url_idx = next(
                (i for i, h in enumerate(lower) if h in PAGE_URL_KEYS or h in ("video_url",)),
                None,
            )
            title_idx = next((i for i, h in enumerate(lower) if h in TITLE_KEYS), None)
            cdn_idx = next((i for i, h in enumerate(lower) if h in CDN_URL_KEYS), None)
            # 首行像表头且含 url 类列名 → 按列解析
            if url_idx is not None and any(h in PAGE_URL_KEYS or h in CDN_URL_KEYS for h in lower):
                for row in rows[1:]:
                    cells = list(row) if row else []
                    def cell(i: int | None) -> str:
                        if i is None or i >= len(cells) or cells[i] is None:
                            return ""
                        return str(cells[i]).strip()

                    page = cell(url_idx)
                    if not page.startswith("http"):
                        found = normalize_line(page)
                        page = found[0] if found else ""
                    if not page:
                        continue
                    title = cell(title_idx)
                    cdn = cell(cdn_idx)
                    e: dict[str, str] = {"url": page}
                    if title:
                        e["title"] = title
                    if cdn.startswith("http") and cdn != page:
                        e["download_url"] = cdn
                    entries.append(e)
            else:
                for row in rows:
                    for cell_v in row or ():
                        if cell_v is None:
                            continue
                        s = str(cell_v).strip()
                        if not s or s.startswith("#"):
                            continue
                        for u in normalize_line(s):
                            entries.append({"url": u})
    finally:
        wb.close()
    return _dedupe_entries(entries)


def load_urls_xlsx_bytes(content: bytes) -> list[str]:
    return [e["url"] for e in load_entries_xlsx_bytes(content)]


def extract_entries_from_upload(filename: str, content: bytes) -> list[dict[str, str]]:
    """解析上传文件为条目列表：url 必填，title / download_url 可选。"""
    if not content:
        return []
    suffix = Path(filename or "").suffix.lower()

    if suffix in (".xlsx", ".xlsm"):
        return load_entries_xlsx_bytes(content)

    raw = content.decode("utf-8-sig")

    if suffix == ".json":
        data = json.loads(raw)
        entries: list[dict[str, str]] = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, str) and item.startswith("http"):
                    entries.append({"url": item.strip()})
                elif isinstance(item, dict):
                    entries.extend(entry_from_dict(item))
        elif isinstance(data, dict):
            entries.extend(entry_from_dict(data))
        return _dedupe_entries(entries)

    if suffix == ".jsonl":
        entries = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                for u in normalize_line(line):
                    entries.append({"url": u})
                continue
            if isinstance(obj, str) and obj.startswith("http"):
                entries.append({"url": obj.strip()})
            elif isinstance(obj, dict):
                entries.extend(entry_from_dict(obj))
        return _dedupe_entries(entries)

    if suffix == ".csv":
        f = io.StringIO(raw)
        reader = csv.DictReader(f)
        if reader.fieldnames:
            lower_map = _header_lower_map(reader.fieldnames)
            url_col = _find_col(lower_map, PAGE_URL_KEYS + ("video_url",))
            title_col = _find_col(lower_map, TITLE_KEYS)
            cdn_col = _find_col(lower_map, CDN_URL_KEYS)
            if url_col:
                entries = []
                for row in reader:
                    v = (row.get(url_col) or "").strip()
                    if not v.startswith("http"):
                        continue
                    e: dict[str, str] = {"url": v}
                    if title_col:
                        t = (row.get(title_col) or "").strip()
                        if t:
                            e["title"] = t
                    if cdn_col:
                        cdn = (row.get(cdn_col) or "").strip()
                        if cdn.startswith("http") and cdn != v:
                            e["download_url"] = cdn
                    entries.append(e)
                return _dedupe_entries(entries)
        entries = []
        for row in csv.reader(io.StringIO(raw)):
            for cell in row:
                for u in normalize_line(cell):
                    entries.append({"url": u})
        return _dedupe_entries(entries)

    entries = []
    for line in raw.splitlines():
        for u in normalize_line(line):
            entries.append({"url": u})
    return _dedupe_entries(entries)


def extract_urls_from_upload(filename: str, content: bytes) -> list[str]:
    return [e["url"] for e in extract_entries_from_upload(filename, content)]
