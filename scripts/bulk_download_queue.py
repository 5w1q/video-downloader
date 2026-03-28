#!/usr/bin/env python3
"""
按顺序调用本项目的 /api/download（return_json=true），文件留在服务端 backend/downloads。

适用：Excel / CSV / JSON / TXT 中多条链接，自动提取 URL 后批量下载。
依赖：httpx；读取 .xlsx/.xlsm 时需: pip install openpyxl

跳过已下载：默认写入状态文件（记录成功 URL -> 文件名）；下次同一 URL 自动跳过。
若本机可访问与后端相同的下载目录，可传 --download-dir 仅在文件仍存在时跳过
（避免误删文件后仍被跳过）。

本机直连后端：  --base-url http://127.0.0.1:8000
Docker 仅映射 8080： --base-url http://127.0.0.1:8080  （经 Nginx 转发 /api）

示例：
  python scripts/bulk_download_queue.py -i scripts/examples/urls.example.txt
  python scripts/bulk_download_queue.py -i links.xlsx --state-file ./bulk_state.json
  python scripts/bulk_download_queue.py -i links.xlsx --download-dir ../backend/downloads
  python scripts/bulk_download_queue.py -i export.json --base-url http://127.0.0.1:8080
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse, urlunparse

import httpx

URL_RE = re.compile(r"https?://[^\s\]\)\"\'<>,]+", re.IGNORECASE)

_STATE_VERSION = 1

# MediaCrawler / 常见导出字段名（按序尝试）
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


def url_state_key(url: str) -> str:
    """用于状态文件去重：规范化主机名小写、去掉片段与尾部斜杠。"""
    u = url.strip().rstrip(").,;!?")
    try:
        p = urlparse(u)
        netloc = (p.netloc or "").lower()
        path = (p.path or "").rstrip("/") or "/"
        # 保留 query：短链参数不同可能指向不同资源，一般不删
        return urlunparse((p.scheme.lower(), netloc, path, "", p.query, ""))
    except Exception:
        return u


def load_urls_xlsx(path: Path) -> list[str]:
    try:
        import openpyxl
    except ImportError as e:
        raise RuntimeError("读取 .xlsx/.xlsm 请先安装: pip install openpyxl") from e

    urls: list[str] = []
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
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


def load_state(path: Path) -> dict[str, Any]:
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


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def should_skip_url(
    url: str,
    state: dict[str, Any],
    download_dir: Path | None,
) -> tuple[bool, str]:
    key = url_state_key(url)
    entries: dict = state.get("entries", {})
    rec = entries.get(key) or entries.get(url.strip())
    if not isinstance(rec, dict):
        return False, ""
    fn = (rec.get("filename") or "").strip()
    if not fn:
        return False, ""
    if download_dir is not None:
        fp = download_dir / fn
        if fp.is_file():
            return True, f"已存在文件: {fp}"
        return False, ""
    return True, "状态文件中已标记完成（未校验磁盘）"


def record_success(state: dict[str, Any], url: str, filename: str, title: str) -> None:
    key = url_state_key(url)
    entries = state.setdefault("entries", {})
    entries[key] = {
        "filename": filename,
        "title": title,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


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


def load_urls(path: Path) -> list[str]:
    suffix = path.suffix.lower()

    if suffix in (".xlsx", ".xlsm"):
        return load_urls_xlsx(path)

    raw = path.read_text(encoding="utf-8-sig")

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

    # .txt 或其它：每行提取 URL
    urls = []
    for line in raw.splitlines():
        urls.extend(normalize_line(line))
    return _dedupe(urls)


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


def main() -> int:
    p = argparse.ArgumentParser(description="批量请求 /api/download（return_json）")
    p.add_argument(
        "-i",
        "--input",
        type=Path,
        required=True,
        help="链接文件：.xlsx/.xlsm、.csv、.txt、.json、.jsonl",
    )
    p.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="API 根地址。Docker 未暴露 8000 时用 http://127.0.0.1:8080",
    )
    p.add_argument(
        "--format-id",
        default="bestvideo+bestaudio/best",
        help="非抖音时的 yt-dlp format_id",
    )
    p.add_argument("--delay", type=float, default=3.0, help="每条请求间隔（秒）")
    p.add_argument("--timeout", type=float, default=600.0, help="单条下载超时（秒）")
    p.add_argument("--dry-run", action="store_true", help="只打印 URL，不请求")
    p.add_argument(
        "--state-file",
        type=Path,
        default=Path("bulk_download_state.json"),
        help="记录已成功下载的 URL，下次自动跳过（默认 ./bulk_download_state.json）",
    )
    p.add_argument(
        "--no-skip-completed",
        action="store_true",
        help="不跳过状态文件中已成功的 URL（强制全部重试）",
    )
    p.add_argument(
        "--download-dir",
        type=Path,
        default=None,
        help=(
            "服务端下载目录在本机的路径（如 backend/downloads）。若设置，仅当该文件仍存在时才跳过；"
            "不设则仅根据状态文件跳过"
        ),
    )
    args = p.parse_args()

    if not args.input.is_file():
        print(f"文件不存在: {args.input}", file=sys.stderr)
        return 1

    try:
        urls = load_urls(args.input)
    except Exception as e:
        print(f"解析输入失败: {e}", file=sys.stderr)
        return 1

    if not urls:
        print("未解析到任何 URL", file=sys.stderr)
        return 1

    print(f"共 {len(urls)} 条链接（已去重）")
    base = args.base_url.rstrip("/")
    endpoint = f"{base}/api/download"

    state = load_state(args.state_file) if not args.no_skip_completed else {"version": _STATE_VERSION, "entries": {}}
    download_dir = args.download_dir.resolve() if args.download_dir else None
    if download_dir and not download_dir.is_dir():
        print(f"警告: --download-dir 不是目录: {download_dir}，将仅按状态文件跳过", flush=True)
        download_dir = None

    if args.dry_run:
        for u in urls:
            skip, reason = (
                should_skip_url(u, state, download_dir)
                if not args.no_skip_completed
                else (False, "")
            )
            tag = f" [SKIP {reason}]" if skip else ""
            print(f"{u}{tag}")
        return 0

    ok, fail, skipped = 0, 0, 0
    with httpx.Client(timeout=args.timeout) as client:
        for i, url in enumerate(urls, 1):
            print(f"[{i}/{len(urls)}] {url[:80]}...", flush=True)
            if not args.no_skip_completed:
                skip, reason = should_skip_url(url, state, download_dir)
                if skip:
                    print(f"  SKIP: {reason}", flush=True)
                    skipped += 1
                    continue
            try:
                r = client.post(
                    endpoint,
                    json={
                        "url": url,
                        "format_id": args.format_id,
                        "return_json": True,
                    },
                )
                if r.status_code != 200:
                    print(f"  HTTP {r.status_code}: {r.text[:500]}", flush=True)
                    fail += 1
                else:
                    data = r.json()
                    if data.get("success"):
                        d = data.get("data") or {}
                        fn = d.get("filename", "")
                        title = d.get("title", "")
                        print(f"  OK -> {fn}", flush=True)
                        ok += 1
                        if not args.no_skip_completed:
                            record_success(state, url, fn, title)
                            save_state(args.state_file, state)
                    else:
                        print(f"  FAIL: {data}", flush=True)
                        fail += 1
            except Exception as e:
                print(f"  ERROR: {e}", flush=True)
                fail += 1
            if i < len(urls) and args.delay > 0:
                time.sleep(args.delay)

    print(f"完成：成功 {ok}，跳过 {skipped}，失败 {fail}")
    if not args.no_skip_completed and ok:
        print(f"状态已写入: {args.state_file.resolve()}", flush=True)
    return 0 if fail == 0 else 2


if os.name == "nt":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

if __name__ == "__main__":
    raise SystemExit(main())
