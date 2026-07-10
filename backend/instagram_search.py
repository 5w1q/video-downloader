"""Instagram Reels 关键词搜索 + 互动/日期筛选（基于 ScrapeCreators）。"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

_TITLE_MAX = 120
_API_URL = "https://api.scrapecreators.com/v2/instagram/reels/search"

# 前端 date_filter → ScrapeCreators date_posted
_DATE_POSTED_MAP = {
    "today": "last-day",
    "week": "last-week",
    "month": "last-month",
    "year": "last-year",
}


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(lo, min(hi, int(raw)))
    except ValueError:
        return default


def _default_max_results() -> int:
    return _env_int("INSTAGRAM_SEARCH_MAX_RESULTS", 20, 1, 50)


def _default_search_pool() -> int:
    return _env_int("INSTAGRAM_SEARCH_POOL", 40, 1, 50)


def _api_key() -> str:
    return (os.getenv("SCRAPECREATORS_API_KEY") or "").strip()


def _as_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _today_yyyymmdd() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d")


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


def _normalize_upload_date(raw: Any) -> str:
    """统一为 YYYYMMDD；无法解析则返回空串。"""
    if raw is None:
        return ""
    if isinstance(raw, (int, float)):
        try:
            ts = float(raw)
            if ts > 1e12:
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y%m%d")
        except (OverflowError, OSError, ValueError):
            return ""
    s = str(raw).strip()
    if not s:
        return ""
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) == 8:
        try:
            datetime.strptime(digits, "%Y%m%d")
            return digits
        except ValueError:
            pass
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).strftime("%Y%m%d")
        except ValueError:
            continue
    try:
        iso = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y%m%d")
    except ValueError:
        return ""


def map_date_posted(date_filter: str) -> Optional[str]:
    """前端 date_filter → API date_posted；all/date 不传。"""
    mode = (date_filter or "all").strip().lower()
    return _DATE_POSTED_MAP.get(mode)


def _truncate_title(text: str) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    if not t:
        return "无标题"
    if len(t) <= _TITLE_MAX:
        return t
    return t[: _TITLE_MAX - 1] + "…"


def _reel_id(item: dict[str, Any]) -> str:
    for key in ("id", "pk", "media_id"):
        v = item.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    sc = (item.get("shortcode") or item.get("code") or "").strip()
    return sc


def _reel_url(item: dict[str, Any]) -> str:
    url = (item.get("url") or item.get("permalink") or "").strip()
    if url.startswith("http"):
        return url
    sc = (item.get("shortcode") or item.get("code") or "").strip()
    if sc:
        return f"https://www.instagram.com/reel/{sc}/"
    return ""


def _uploader(item: dict[str, Any]) -> str:
    owner = item.get("owner") or item.get("user") or {}
    if isinstance(owner, dict):
        return (
            owner.get("username")
            or owner.get("user_name")
            or owner.get("full_name")
            or ""
        )
    return str(owner or "")


def _thumbnail(item: dict[str, Any]) -> str:
    for key in ("thumbnail_src", "display_url", "thumbnail", "thumbnail_url"):
        v = item.get(key)
        if isinstance(v, str) and v.startswith("http"):
            return v
    versions = item.get("image_versions2") or item.get("image_versions") or {}
    if isinstance(versions, dict):
        candidates = versions.get("candidates") or []
        if candidates and isinstance(candidates[0], dict):
            u = candidates[0].get("url")
            if isinstance(u, str) and u.startswith("http"):
                return u
    return ""


def _duration(item: dict[str, Any]) -> tuple[Any, str]:
    dur = item.get("video_duration") or item.get("duration")
    if dur is None:
        return None, ""
    try:
        d = float(dur)
        if d > 1000:  # 毫秒
            d = d / 1000.0
        secs = int(d)
        return secs, f"{secs // 60}:{secs % 60:02d}"
    except (TypeError, ValueError):
        return None, ""


def normalize_reel(item: dict[str, Any]) -> Optional[dict[str, Any]]:
    if not isinstance(item, dict):
        return None

    url = _reel_url(item)
    if not url:
        return None

    rid = _reel_id(item)
    caption = item.get("caption") or item.get("title") or ""
    if isinstance(caption, dict):
        caption = caption.get("text") or ""

    likes = _as_int(item.get("like_count") or item.get("likes"))
    comments = _as_int(item.get("comment_count") or item.get("comments_count"))
    views = _as_int(
        item.get("video_play_count")
        if item.get("video_play_count") is not None
        else item.get("video_view_count") or item.get("view_count") or item.get("play_count")
    )
    upload_date = _normalize_upload_date(
        item.get("taken_at")
        or item.get("taken_at_timestamp")
        or item.get("timestamp")
        or item.get("created_at")
    )
    duration, duration_string = _duration(item)

    video_url = ""
    for key in ("video_url", "video_src", "content_url"):
        v = item.get(key)
        if isinstance(v, str) and v.startswith("http"):
            video_url = v.strip()
            break

    return {
        "id": rid,
        "title": _truncate_title(str(caption)),
        "url": url,
        "video_url": video_url,
        "uploader": _uploader(item),
        "like_count": likes,
        "comment_count": comments,
        "view_count": views,
        "upload_date": upload_date,
        "upload_date_display": (
            f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
            if len(upload_date) == 8
            else ""
        ),
        "thumbnail": _thumbnail(item),
        "duration": duration,
        "duration_string": duration_string,
        "below_threshold": False,
        "platform": "instagram",
    }


def _passes_thresholds(
    likes: Optional[int],
    comments: Optional[int],
    views: Optional[int],
    min_likes: int,
    min_comments: int,
    min_views: int,
    *,
    require_known_stats: bool,
) -> bool:
    checks = (
        (min_likes, likes),
        (min_comments, comments),
        (min_views, views),
    )
    for threshold, value in checks:
        if threshold <= 0:
            continue
        if value is None:
            if require_known_stats:
                return False
        elif value < threshold:
            return False
    return True


def _call_scrapecreators(
    query: str,
    *,
    date_posted: Optional[str] = None,
    page: int = 1,
) -> list[dict[str, Any]]:
    key = _api_key()
    if not key:
        raise ValueError(
            "未配置 SCRAPECREATORS_API_KEY。请在 backend/.env 中设置后重启后端。"
        )

    params: dict[str, Any] = {"query": query, "page": max(1, int(page or 1))}
    if date_posted:
        params["date_posted"] = date_posted

    url = f"{_API_URL}?{urlencode(params)}"
    timeout = httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=30.0)
    last_err: Optional[Exception] = None
    resp: Optional[httpx.Response] = None
    # 偶发 SSL EOF / 断连：轻量重试
    for attempt in range(3):
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(url, headers={"x-api-key": key})
            break
        except httpx.TimeoutException as e:
            raise ValueError("Instagram 搜索超时（ScrapeCreators 未在时限内返回）") from e
        except httpx.HTTPError as e:
            last_err = e
            if attempt >= 2:
                raise ValueError(f"Instagram 搜索请求失败: {e}") from e
    if resp is None:
        raise ValueError(f"Instagram 搜索请求失败: {last_err}")

    if resp.status_code == 401 or resp.status_code == 403:
        raise ValueError("SCRAPECREATORS_API_KEY 无效或已过期")
    if resp.status_code == 402:
        raise ValueError("ScrapeCreators 额度不足，请充值后重试")
    if resp.status_code >= 400:
        detail = (resp.text or "")[:400]
        raise ValueError(f"ScrapeCreators 失败 (HTTP {resp.status_code}): {detail}")

    try:
        data = resp.json()
    except Exception as e:
        raise ValueError("ScrapeCreators 返回了无法解析的响应") from e

    if isinstance(data, dict):
        if data.get("success") is False:
            err = data.get("error") or data.get("message") or "未知错误"
            raise ValueError(f"ScrapeCreators 错误: {err}")
        items = data.get("reels") or data.get("items") or data.get("data") or []
        if isinstance(items, list):
            return [x for x in items if isinstance(x, dict)]
        return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def _fetch_pool(
    query: str,
    *,
    date_posted: Optional[str],
    pool: int,
) -> list[dict[str, Any]]:
    """按页拉取，直到凑够 pool 或无更多结果。"""
    collected: list[dict[str, Any]] = []
    page = 1
    max_pages = 5
    while len(collected) < pool and page <= max_pages:
        batch = _call_scrapecreators(query, date_posted=date_posted, page=page)
        if not batch:
            break
        collected.extend(batch)
        if len(batch) < 5:
            # 本页很少，多半没有下一页
            break
        page += 1
    return collected[: max(pool, len(collected))]


def search_instagram(
    query: str,
    *,
    max_results: int = 20,
    min_likes: int = 0,
    min_comments: int = 0,
    min_views: int = 0,
    search_pool: Optional[int] = None,
    date_filter: str = "all",
    upload_date: Optional[str] = None,
) -> dict[str, Any]:
    """按关键词搜索 Instagram Reels，并按点赞/评论/播放/日期筛选。"""
    q = (query or "").strip()
    if not q:
        raise ValueError("请输入搜索关键词")

    max_results = max(1, min(int(max_results or _default_max_results()), 50))
    min_likes = max(0, int(min_likes or 0))
    min_comments = max(0, int(min_comments or 0))
    min_views = max(0, int(min_views or 0))

    mode = (date_filter or "all").strip().lower()
    if mode not in ("all", "today", "week", "month", "year", "date"):
        mode = "all"

    target_date: Optional[str] = None
    date_posted = map_date_posted(mode)
    if mode == "date":
        target_date = _parse_filter_date(upload_date)
        if not target_date:
            raise ValueError("请选择筛选日期")
        # 指定单日：不传 date_posted（或用 last-year 扩大召回），本地按 taken_at 滤
        date_posted = None
    elif mode == "today":
        target_date = _today_yyyymmdd()

    if search_pool is None:
        if min_likes > 0 or min_comments > 0 or min_views > 0 or target_date or mode in (
            "week",
            "month",
            "year",
        ):
            pool = max(max_results, min(_default_search_pool(), 50))
        else:
            pool = max_results
    else:
        pool = max(1, min(int(search_pool), 50))
    pool = max(pool, max_results)

    raw_items = _fetch_pool(q, date_posted=date_posted, pool=pool)

    matched: list[dict[str, Any]] = []
    below: list[dict[str, Any]] = []
    scanned = 0
    skipped_threshold = 0
    skipped_date = 0
    skipped_no_url = 0
    skipped_error = 0

    require_known = min_likes > 0 or min_comments > 0 or min_views > 0

    for raw in raw_items:
        item = normalize_reel(raw)
        if not item:
            skipped_no_url += 1
            continue

        scanned += 1

        if target_date:
            ud = item.get("upload_date") or ""
            if ud and ud != target_date:
                skipped_date += 1
                continue
            # today 档：API 已用 last-day；无日期的条目仍保留（索引结果可能缺 taken_at）
            if mode == "date" and not ud:
                skipped_date += 1
                continue

        likes = item.get("like_count")
        comments = item.get("comment_count")
        views = item.get("view_count")

        ok = _passes_thresholds(
            likes,
            comments,
            views,
            min_likes,
            min_comments,
            min_views,
            require_known_stats=require_known,
        )
        if ok:
            if len(matched) < max_results:
                matched.append(item)
        else:
            skipped_threshold += 1
            if target_date and len(below) < max_results:
                below.append({**item, "below_threshold": True})

        if len(matched) >= max_results and (not target_date or len(below) >= max_results):
            break

    if scanned == 0 and not matched and not below:
        raise ValueError(
            "Instagram 搜索无结果（索引召回弱于 YouTube，可放宽关键词/日期后重试）"
        )

    return {
        "query": q,
        "date_posted": date_posted or "",
        "platform": "instagram",
        "max_results": max_results,
        "min_likes": min_likes,
        "min_comments": min_comments,
        "min_views": min_views,
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
