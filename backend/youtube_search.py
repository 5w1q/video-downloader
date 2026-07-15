"""YouTube 关键词搜索 + 播放量/点赞/日期筛选（基于 yt-dlp）。"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import yt_dlp

from downloader import _parse_socket_timeout, _ytdlp_base_opts


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(lo, min(hi, int(raw)))
    except ValueError:
        return default


def _default_max_results() -> int:
    return _env_int("YOUTUBE_SEARCH_MAX_RESULTS", 20, 1, 50)


def _default_search_pool() -> int:
    return _env_int("YOUTUBE_SEARCH_POOL", 40, 1, 50)


def _video_url(entry: dict[str, Any]) -> str:
    url = (entry.get("webpage_url") or entry.get("url") or "").strip()
    if url.startswith("http"):
        return url
    vid = (entry.get("id") or "").strip()
    if vid and not vid.startswith("ytsearch"):
        return f"https://www.youtube.com/watch?v={vid}"
    return ""


def _as_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _normalize_upload_date(raw: Any) -> str:
    """统一为 YYYYMMDD；无法解析则返回空串。"""
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 8:
        return digits[:8]
    return ""


def _parse_filter_date(raw: Optional[str]) -> Optional[str]:
    """接受 YYYY-MM-DD / YYYYMMDD，返回 YYYYMMDD。"""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) != 8:
        raise ValueError("日期格式应为 YYYY-MM-DD")
    try:
        datetime.strptime(digits, "%Y%m%d")
    except ValueError as e:
        raise ValueError("无效日期") from e
    return digits


def _today_yyyymmdd() -> str:
    # 用东八区「今天」，更符合国内使用习惯
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d")


def _add_days_yyyymmdd(d: str, days: int) -> str:
    dt = datetime.strptime(d, "%Y%m%d") + timedelta(days=days)
    return dt.strftime("%Y%m%d")


def _resolve_date_range(
    date_filter: str,
    upload_date: Optional[str],
) -> tuple[str, Optional[str], Optional[str]]:
    """返回 (mode, date_min_yyyymmdd, date_max_yyyymmdd)，闭区间；不限则为 (all, None, None)。"""
    mode = (date_filter or "all").strip().lower()
    if mode not in ("all", "today", "week", "month", "date"):
        mode = "all"

    today = _today_yyyymmdd()
    if mode == "all":
        return mode, None, None
    if mode == "today":
        return mode, today, today
    if mode == "week":
        return mode, _add_days_yyyymmdd(today, -7), today
    if mode == "month":
        return mode, _add_days_yyyymmdd(today, -30), today
    # date
    target = _parse_filter_date(upload_date)
    if not target:
        raise ValueError("请选择筛选日期")
    return mode, target, target


def _in_date_range(
    upload_date_val: str,
    date_min: Optional[str],
    date_max: Optional[str],
) -> bool:
    if not date_min and not date_max:
        return True
    if not upload_date_val:
        return False
    if date_min and upload_date_val < date_min:
        return False
    if date_max and upload_date_val > date_max:
        return False
    return True


def _passes_thresholds(
    views: Optional[int],
    likes: Optional[int],
    min_views: int,
    min_likes: int,
    *,
    require_known_stats: bool,
) -> bool:
    if min_views > 0:
        if views is None:
            if require_known_stats:
                return False
        elif views < min_views:
            return False
    if min_likes > 0:
        if likes is None:
            if require_known_stats:
                return False
        elif likes < min_likes:
            return False
    return True


class _YtdlpErrorLogger:
    def __init__(self, on_error):
        self._on_error = on_error

    def debug(self, msg):  # noqa: ANN001
        pass

    def info(self, msg):  # noqa: ANN001
        pass

    def warning(self, msg):  # noqa: ANN001
        pass

    def error(self, msg):  # noqa: ANN001
        self._on_error(str(msg))


def _base_search_opts() -> dict[str, Any]:
    return {
        **_ytdlp_base_opts("https://www.youtube.com/"),
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "socket_timeout": _parse_socket_timeout(),
        "retries": 1,
    }


def _enrich_entry(url: str) -> dict[str, Any]:
    """补全 view_count / like_count / upload_date。"""
    opts = {
        **_base_search_opts(),
        "skip_download": True,
        "noplaylist": True,
        "ignore_no_formats_error": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False, process=False)
    if not isinstance(info, dict):
        return {}
    if (
        info.get("view_count") is None
        or info.get("like_count") is None
        or not info.get("upload_date")
    ):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                full = ydl.extract_info(url, download=False)
            if isinstance(full, dict):
                for k in (
                    "view_count",
                    "like_count",
                    "title",
                    "uploader",
                    "channel",
                    "duration",
                    "thumbnail",
                    "upload_date",
                    "id",
                    "webpage_url",
                ):
                    if info.get(k) is None and full.get(k) is not None:
                        info[k] = full[k]
        except Exception:
            pass
    return info


def _result_item(
    *,
    vid: str,
    title: str,
    url: str,
    uploader: str,
    duration: Any,
    duration_string: str,
    views: Optional[int],
    likes: Optional[int],
    thumbnail: str,
    upload_date: str,
    below_threshold: bool = False,
) -> dict[str, Any]:
    return {
        "id": vid,
        "title": title,
        "url": url,
        "uploader": uploader,
        "duration": duration,
        "duration_string": duration_string,
        "view_count": views,
        "like_count": likes,
        "thumbnail": thumbnail,
        "upload_date": upload_date,
        "upload_date_display": (
            f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
            if len(upload_date) == 8
            else ""
        ),
        "below_threshold": below_threshold,
    }


def search_youtube(
    query: str,
    *,
    max_results: int = 20,
    min_views: int = 0,
    min_likes: int = 0,
    search_pool: Optional[int] = None,
    date_filter: str = "all",
    upload_date: Optional[str] = None,
) -> dict[str, Any]:
    """按关键词搜索 YouTube，并按播放量/点赞/发布日期筛选。

    date_filter:
      - all: 不限日期
      - today: 仅今天发布（东八区）
      - week: 近 7 天（含今天）
      - month: 近 30 天（含今天）
      - date: 指定 upload_date（YYYY-MM-DD）

    始终补全 like_count。日期筛选开启时，未达播放量/点赞阈值但日期命中的视频
    会放入 below_threshold_results，便于预览区展示其播放量。
    """
    q = (query or "").strip()
    if not q:
        raise ValueError("请输入搜索关键词")

    max_results = max(1, min(int(max_results or _default_max_results()), 50))
    min_views = max(0, int(min_views or 0))
    min_likes = max(0, int(min_likes or 0))
    mode, date_min, date_max = _resolve_date_range(date_filter, upload_date)
    date_active = bool(date_min or date_max)
    # 指定单日时回传该日；区间筛选不回传单一 upload_date
    target_date = date_min if (date_min and date_min == date_max) else None

    if search_pool is None:
        if min_views > 0 or min_likes > 0 or date_active:
            pool = max(max_results, min(_default_search_pool(), 50))
        else:
            pool = max_results
    else:
        pool = max(1, min(int(search_pool), 50))
    pool = max(pool, max_results)

    search_url = f"ytsearch{pool}:{q}"
    ydl_opts: dict[str, Any] = {
        **_base_search_opts(),
        "extract_flat": "in_playlist",
        "noplaylist": False,
    }

    errors: list[str] = []

    def _capture_error(msg: str) -> None:
        text = (msg or "").strip()
        if text:
            errors.append(text[:300])

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.params["logger"] = _YtdlpErrorLogger(_capture_error)
            info = ydl.extract_info(search_url, download=False)
    except Exception as e:
        from downloader import friendly_download_error

        raise ValueError(
            f"YouTube 搜索失败: {friendly_download_error(e)}"
        ) from e

    if not info:
        hint = errors[0] if errors else "无返回数据"
        from downloader import friendly_download_error

        raise ValueError(f"YouTube 搜索失败: {friendly_download_error(hint)}")

    entries = info.get("entries") or []
    matched: list[dict[str, Any]] = []
    below: list[dict[str, Any]] = []
    scanned = 0
    skipped_threshold = 0
    skipped_date = 0
    skipped_no_url = 0
    skipped_error = 0

    require_known = min_views > 0 or min_likes > 0

    for entry in entries:
        if not entry or not isinstance(entry, dict):
            skipped_error += 1
            continue

        url = _video_url(entry)
        if not url:
            skipped_no_url += 1
            continue

        views = _as_int(entry.get("view_count"))
        likes = _as_int(entry.get("like_count"))
        title = entry.get("title") or "未知标题"
        uploader = entry.get("uploader") or entry.get("channel") or ""
        duration = entry.get("duration")
        thumbnail = entry.get("thumbnail") or ""
        upload_date_val = _normalize_upload_date(entry.get("upload_date"))
        vid = entry.get("id") or ""

        # 始终补全点赞/日期（flat 结果通常没有 like_count）
        try:
            full = _enrich_entry(url)
            if full:
                views = _as_int(full.get("view_count")) if full.get("view_count") is not None else views
                likes = _as_int(full.get("like_count")) if full.get("like_count") is not None else likes
                title = full.get("title") or title
                uploader = full.get("uploader") or full.get("channel") or uploader
                duration = full.get("duration") if full.get("duration") is not None else duration
                thumbnail = full.get("thumbnail") or thumbnail
                upload_date_val = _normalize_upload_date(full.get("upload_date")) or upload_date_val
                vid = full.get("id") or vid
                url = _video_url(full) or url
        except Exception as e:
            errors.append(str(e)[:300])
            if require_known and (
                (min_views > 0 and views is None) or (min_likes > 0 and likes is None)
            ):
                skipped_error += 1
                continue

        scanned += 1

        if not _in_date_range(upload_date_val, date_min, date_max):
            skipped_date += 1
            continue

        duration_string = entry.get("duration_string") or ""
        if not duration_string and duration:
            try:
                d = int(duration)
                duration_string = f"{d // 60}:{d % 60:02d}"
            except (TypeError, ValueError):
                duration_string = ""

        item = _result_item(
            vid=vid,
            title=title,
            url=url,
            uploader=uploader,
            duration=duration,
            duration_string=duration_string,
            views=views,
            likes=likes,
            thumbnail=thumbnail,
            upload_date=upload_date_val,
            below_threshold=False,
        )

        if _passes_thresholds(
            views, likes, min_views, min_likes, require_known_stats=require_known
        ):
            if len(matched) < max_results:
                matched.append(item)
        else:
            skipped_threshold += 1
            if date_active and len(below) < max_results:
                below.append({**item, "below_threshold": True})

        if len(matched) >= max_results and (not date_active or len(below) >= max_results):
            break

    if scanned == 0 and not matched and not below:
        hint = errors[0] if errors else "未获取到任何搜索结果（网络/地区限制或需 Cookie）"
        from downloader import friendly_download_error

        raise ValueError(f"YouTube 搜索失败: {friendly_download_error(hint)}")

    return {
        "query": q,
        "platform": "youtube",
        "max_results": max_results,
        "min_views": min_views,
        "min_likes": min_likes,
        "date_filter": mode,
        "upload_date": target_date or "",
        "upload_date_display": (
            f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:8]}"
            if target_date
            else ""
        ),
        "search_pool": pool,
        "scanned": scanned,
        "skipped_threshold": skipped_threshold,
        "skipped_date": skipped_date,
        "skipped_no_url": skipped_no_url,
        "skipped_error": skipped_error,
        "total": len(matched),
        "results": matched,
        "urls": [m["url"] for m in matched],
        "below_threshold_results": below,
    }
