"""Instagram Reels 关键词搜索 + 互动/日期筛选（基于 Apify）。"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import quote

import httpx

_TITLE_MAX = 120
_DEFAULT_ACTOR = "data-slayer/instagram-search-reels"
_APIFY_BASE = "https://api.apify.com/v2"


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


def _apify_token() -> str:
    return (os.getenv("APIFY_TOKEN") or "").strip()


def _actor_id() -> str:
    raw = (os.getenv("INSTAGRAM_SEARCH_ACTOR_ID") or _DEFAULT_ACTOR).strip()
    return raw or _DEFAULT_ACTOR


def _actor_path_id(actor_id: str) -> str:
    """Apify URL 中 username/name 用 ~ 连接。"""
    s = (actor_id or "").strip()
    if "/" in s and "~" not in s:
        return s.replace("/", "~", 1)
    return s


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


def _add_days_yyyymmdd(d: str, days: int) -> str:
    dt = datetime.strptime(d, "%Y%m%d") + timedelta(days=days)
    return dt.strftime("%Y%m%d")


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
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
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


def _resolve_date_bounds(
    date_filter: str,
    upload_date: Optional[str],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """返回 (since_yyyymmdd, until_yyyymmdd_exclusive, exact_yyyymmdd)。

    until 为 exclusive；指定单日时 since=until-1day 的 exact。
    """
    mode = (date_filter or "all").strip().lower()
    if mode not in ("all", "today", "week", "month", "year", "date"):
        mode = "all"

    today = _today_yyyymmdd()
    if mode == "all":
        return None, None, None
    if mode == "today":
        return today, _add_days_yyyymmdd(today, 1), today
    if mode == "week":
        return _add_days_yyyymmdd(today, -7), _add_days_yyyymmdd(today, 1), None
    if mode == "month":
        return _add_days_yyyymmdd(today, -30), _add_days_yyyymmdd(today, 1), None
    if mode == "year":
        return _add_days_yyyymmdd(today, -365), _add_days_yyyymmdd(today, 1), None
    # date
    target = _parse_filter_date(upload_date)
    if not target:
        raise ValueError("请选择筛选日期")
    return target, _add_days_yyyymmdd(target, 1), target


def _in_date_range(
    upload_date: str,
    *,
    since: Optional[str],
    until_excl: Optional[str],
    exact: Optional[str],
    mode: str,
) -> bool:
    """本地日期过滤。缺日期：today/date 严格丢弃；相对档保留（与旧行为接近）。"""
    if exact:
        if not upload_date:
            return False
        return upload_date == exact
    if since is None and until_excl is None:
        return True
    if not upload_date:
        # 相对档无 taken_at 时保留，避免索引缺字段导致全空
        return mode in ("week", "month", "year")
    if since and upload_date < since:
        return False
    if until_excl and upload_date >= until_excl:
        return False
    return True


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
    sc = (item.get("code") or item.get("shortcode") or "").strip()
    return sc


def _reel_url(item: dict[str, Any]) -> str:
    url = (item.get("url") or item.get("permalink") or "").strip()
    if url.startswith("http"):
        return url
    sc = (item.get("code") or item.get("shortcode") or "").strip()
    if sc:
        return f"https://www.instagram.com/reel/{sc}/"
    return ""


def _uploader(item: dict[str, Any]) -> str:
    owner = item.get("user") or item.get("owner") or {}
    if isinstance(owner, dict):
        return (
            owner.get("username")
            or owner.get("user_name")
            or owner.get("full_name")
            or ""
        )
    return str(owner or "")


def _thumbnail(item: dict[str, Any]) -> str:
    for key in ("thumbnail_url", "thumbnail_src", "display_url", "thumbnail"):
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


def _caption_text(item: dict[str, Any]) -> str:
    caption = item.get("caption") or item.get("title") or ""
    if isinstance(caption, dict):
        return str(caption.get("text") or "")
    return str(caption or "")


def normalize_reel(item: dict[str, Any]) -> Optional[dict[str, Any]]:
    if not isinstance(item, dict):
        return None

    url = _reel_url(item)
    if not url:
        return None

    rid = _reel_id(item)
    caption = _caption_text(item)

    likes = _as_int(item.get("like_count") or item.get("likes"))
    comments = _as_int(item.get("comment_count") or item.get("comments_count"))
    views = _as_int(
        item.get("ig_play_count")
        if item.get("ig_play_count") is not None
        else item.get("play_count")
        if item.get("play_count") is not None
        else item.get("video_play_count")
        if item.get("video_play_count") is not None
        else item.get("video_view_count") or item.get("view_count")
    )
    upload_date = _normalize_upload_date(
        item.get("taken_at_date")
        or item.get("taken_at")
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
        "title": _truncate_title(caption),
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


def _pool_to_max_pages(pool: int) -> int:
    """Actor 按页翻；约估每页十余条，上限 5 页控制费用。"""
    p = max(1, int(pool or 1))
    return max(1, min(5, (p + 11) // 12))


def _call_apify(query: str, max_pages: int) -> list[dict[str, Any]]:
    token = _apify_token()
    if not token:
        raise ValueError(
            "未配置 APIFY_TOKEN。请在 backend/.env 中设置后重启后端。"
        )

    actor = _actor_path_id(_actor_id())
    url = (
        f"{_APIFY_BASE}/acts/{quote(actor, safe='~')}"
        f"/run-sync-get-dataset-items"
        f"?token={quote(token)}"
    )
    payload = {
        "query": query,
        "maxPages": max(1, min(100, int(max_pages or 1))),
    }
    timeout = httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0)
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload)
    except httpx.TimeoutException as e:
        raise ValueError("Instagram 搜索超时（Apify Actor 未在时限内返回）") from e
    except httpx.HTTPError as e:
        from downloader import friendly_download_error

        raise ValueError(
            f"Instagram 搜索请求失败: {friendly_download_error(e)}"
        ) from e

    if resp.status_code == 401:
        raise ValueError("APIFY_TOKEN 无效或已过期")
    if resp.status_code == 402:
        raise ValueError("Apify 额度不足，请充值后重试")
    if resp.status_code >= 400:
        detail = (resp.text or "")[:400]
        raise ValueError(f"Apify Actor 失败 (HTTP {resp.status_code}): {detail}")

    try:
        data = resp.json()
    except Exception as e:
        raise ValueError("Apify 返回了无法解析的响应") from e

    if isinstance(data, dict):
        err = data.get("error") or data.get("message")
        if err and "items" not in data:
            raise ValueError(f"Apify 错误: {err}")
        items = data.get("items") or data.get("data") or []
        if isinstance(items, list):
            return [x for x in items if isinstance(x, dict)]
        return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


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

    since_d, until_d, exact_date = _resolve_date_bounds(mode, upload_date)
    target_date = exact_date

    if search_pool is None:
        if min_likes > 0 or min_comments > 0 or min_views > 0 or since_d or mode in (
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

    max_pages = _pool_to_max_pages(pool)
    raw_items = _call_apify(q, max_pages)
    if len(raw_items) > pool:
        raw_items = raw_items[:pool]

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

        ud = item.get("upload_date") or ""
        if not _in_date_range(
            ud, since=since_d, until_excl=until_d, exact=exact_date, mode=mode
        ):
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
            "Instagram 搜索无结果（可放宽关键词/日期后重试）"
        )

    result = {
        "query": q,
        "date_posted": "",
        "platform": "instagram",
        "provider": "apify",
        "actor_id": _actor_id(),
        "max_pages": max_pages,
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

    from title_translate import annotate_result_titles

    return annotate_result_titles(result)
