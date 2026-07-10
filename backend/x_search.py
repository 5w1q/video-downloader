"""X（Twitter）关键词搜索 + 互动/日期筛选（基于 Apify Advanced Search）。"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import quote

import httpx

_TITLE_MAX = 120
_DEFAULT_ACTOR = "api-ninja/x-twitter-advanced-search"
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
    return _env_int("X_SEARCH_MAX_RESULTS", 20, 1, 50)


def _default_search_pool() -> int:
    return _env_int("X_SEARCH_POOL", 40, 1, 50)


def _apify_token() -> str:
    return (os.getenv("APIFY_TOKEN") or "").strip()


def _actor_id() -> str:
    raw = (os.getenv("X_SEARCH_ACTOR_ID") or _DEFAULT_ACTOR).strip()
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


def _yyyymmdd_to_iso(d: str) -> str:
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}"


def _add_days_yyyymmdd(d: str, days: int) -> str:
    dt = datetime.strptime(d, "%Y%m%d") + timedelta(days=days)
    return dt.strftime("%Y%m%d")


def _normalize_upload_date(raw: Any) -> str:
    """统一为 YYYYMMDD；无法解析则返回空串。"""
    if raw is None:
        return ""
    if isinstance(raw, (int, float)):
        # unix 秒 / 毫秒
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
    if len(digits) >= 8 and re.fullmatch(r"\d{8,}", digits):
        # 纯数字且像 YYYYMMDD（避免把 tweet id 当日期）
        if len(digits) == 8:
            try:
                datetime.strptime(digits, "%Y%m%d")
                return digits
            except ValueError:
                pass
    # Twitter 风格：Tue Apr 10 07:00:30 +0000 2024
    for fmt in (
        "%a %b %d %H:%M:%S %z %Y",
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
    # ISO 宽松
    try:
        iso = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y%m%d")
    except ValueError:
        return ""


def build_x_query(
    keyword: str,
    *,
    min_likes: int = 0,
    min_retweets: int = 0,
    min_comments: int = 0,
    since: Optional[str] = None,
    until: Optional[str] = None,
    videos_only: bool = True,
) -> str:
    """拼装 X Advanced Search 查询串。since/until 为 YYYY-MM-DD。"""
    parts = [keyword.strip()]
    if videos_only:
        parts.append("filter:videos")
    if min_likes > 0:
        parts.append(f"min_faves:{min_likes}")
    if min_retweets > 0:
        parts.append(f"min_retweets:{min_retweets}")
    if min_comments > 0:
        parts.append(f"min_replies:{min_comments}")
    if since:
        parts.append(f"since:{since}")
    if until:
        parts.append(f"until:{until}")
    return " ".join(p for p in parts if p)


def _resolve_date_bounds(
    date_filter: str,
    upload_date: Optional[str],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """返回 (since_iso, until_iso, target_yyyymmdd_for_local)。

    until 对 X 为 exclusive；指定单日时 until = date+1。
    """
    mode = (date_filter or "all").strip().lower()
    if mode not in ("all", "today", "week", "month", "date"):
        mode = "all"

    today = _today_yyyymmdd()
    if mode == "all":
        return None, None, None
    if mode == "today":
        since = _yyyymmdd_to_iso(today)
        until = _yyyymmdd_to_iso(_add_days_yyyymmdd(today, 1))
        return since, until, today
    if mode == "week":
        start = _add_days_yyyymmdd(today, -7)
        since = _yyyymmdd_to_iso(start)
        until = _yyyymmdd_to_iso(_add_days_yyyymmdd(today, 1))
        return since, until, None
    if mode == "month":
        start = _add_days_yyyymmdd(today, -30)
        since = _yyyymmdd_to_iso(start)
        until = _yyyymmdd_to_iso(_add_days_yyyymmdd(today, 1))
        return since, until, None
    # date
    target = _parse_filter_date(upload_date)
    if not target:
        raise ValueError("请选择筛选日期")
    since = _yyyymmdd_to_iso(target)
    until = _yyyymmdd_to_iso(_add_days_yyyymmdd(target, 1))
    return since, until, target


def _truncate_title(text: str) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    if not t:
        return "无标题"
    if len(t) <= _TITLE_MAX:
        return t
    return t[: _TITLE_MAX - 1] + "…"


def _tweet_id(item: dict[str, Any]) -> str:
    for key in ("id", "id_str", "tweetId", "tweet_id"):
        v = item.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    url = (item.get("url") or item.get("tweetUrl") or "").strip()
    m = re.search(r"/status(?:es)?/(\d+)", url)
    return m.group(1) if m else ""


def _tweet_url(item: dict[str, Any], tid: str) -> str:
    url = (item.get("url") or item.get("tweetUrl") or item.get("twitterUrl") or "").strip()
    if url.startswith("http"):
        # 统一成 x.com，便于 yt-dlp
        url = re.sub(r"https?://(www\.)?(twitter|x)\.com", "https://x.com", url, count=1)
        return url
    if tid:
        return f"https://x.com/i/status/{tid}"
    return ""


def _author_name(item: dict[str, Any]) -> str:
    # api-ninja Actor 常用顶层 screen_name / user_info
    top = (item.get("screen_name") or item.get("username") or "").strip()
    if top:
        return top
    author = item.get("author") or item.get("user") or item.get("user_info") or {}
    if isinstance(author, dict):
        return (
            (
                author.get("userName")
                or author.get("username")
                or author.get("screen_name")
                or ""
            )
            or (author.get("name") or "")
            or ""
        )
    return str(author or "")


def _thumbnail(item: dict[str, Any]) -> str:
    for key in ("thumbnail", "thumbnailUrl", "thumbnail_url"):
        v = item.get(key)
        if isinstance(v, str) and v.startswith("http"):
            return v
    media = item.get("media") or item.get("extendedEntities") or item.get("extended_entities")
    if isinstance(media, list) and media:
        first = media[0]
        if isinstance(first, dict):
            for k in ("thumbnail", "preview_image_url", "media_url_https", "url"):
                v = first.get(k)
                if isinstance(v, str) and v.startswith("http"):
                    return v
        elif isinstance(first, str) and first.startswith("http"):
            return first
    entities = item.get("entities") or {}
    if isinstance(entities, dict):
        media_list = entities.get("media") or []
        if media_list and isinstance(media_list[0], dict):
            v = media_list[0].get("media_url_https") or media_list[0].get("media_url")
            if isinstance(v, str) and v.startswith("http"):
                return v
    return ""


def _duration(item: dict[str, Any]) -> tuple[Any, str]:
    dur = item.get("duration") or item.get("video_duration") or item.get("durationMs")
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


def normalize_tweet(item: dict[str, Any]) -> Optional[dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    # 跳过非推文条目
    t = (item.get("type") or "").lower()
    if t and t not in ("tweet", "status", ""):
        return None

    tid = _tweet_id(item)
    url = _tweet_url(item, tid)
    if not url:
        return None

    text = (
        item.get("text")
        or item.get("full_text")
        or item.get("fullText")
        or item.get("content")
        or ""
    )
    likes = _as_int(
        item.get("likeCount")
        or item.get("like_count")
        or item.get("favorite_count")
        or item.get("favorites")
        or item.get("likes")
    )
    retweets = _as_int(
        item.get("retweetCount")
        or item.get("retweet_count")
        or item.get("retweets")
        or item.get("reprint_count")
    )
    comments = _as_int(
        item.get("replyCount")
        or item.get("reply_count")
        or item.get("comment_count")
        or item.get("replies")
    )
    views = _as_int(
        item.get("viewCount")
        or item.get("view_count")
        or item.get("views")
    )
    upload_date = _normalize_upload_date(
        item.get("createdAt")
        or item.get("created_at")
        or item.get("date")
        or item.get("timestamp")
    )
    duration, duration_string = _duration(item)

    return {
        "id": tid,
        "title": _truncate_title(str(text)),
        "url": url,
        "uploader": _author_name(item),
        "like_count": likes,
        "retweet_count": retweets,
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
        "platform": "x",
    }


def _passes_thresholds(
    likes: Optional[int],
    retweets: Optional[int],
    comments: Optional[int],
    min_likes: int,
    min_retweets: int,
    min_comments: int,
    *,
    require_known_stats: bool,
) -> bool:
    checks = (
        (min_likes, likes),
        (min_retweets, retweets),
        (min_comments, comments),
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


def _call_apify(query: str, number_of_tweets: int) -> list[dict[str, Any]]:
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
    # 该 Actor 校验 numberOfTweets >= 20
    number_of_tweets = max(20, int(number_of_tweets or 20))
    payload = {
        "query": query,
        "search_type": "Latest",
        "numberOfTweets": number_of_tweets,
    }
    timeout = httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0)
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload)
    except httpx.TimeoutException as e:
        raise ValueError("X 搜索超时（Apify Actor 未在时限内返回）") from e
    except httpx.HTTPError as e:
        raise ValueError(f"X 搜索请求失败: {e}") from e

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
        # 偶发错误包装
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


def search_x(
    query: str,
    *,
    max_results: int = 20,
    min_likes: int = 0,
    min_retweets: int = 0,
    min_comments: int = 0,
    min_views: int = 0,  # 保留字段；X 无稳定服务端运算符，仅本地弱过滤
    search_pool: Optional[int] = None,
    date_filter: str = "all",
    upload_date: Optional[str] = None,
    videos_only: bool = True,
) -> dict[str, Any]:
    """按关键词搜索 X，并按点赞/转发/评论/日期筛选。"""
    q = (query or "").strip()
    if not q:
        raise ValueError("请输入搜索关键词")

    max_results = max(1, min(int(max_results or _default_max_results()), 50))
    min_likes = max(0, int(min_likes or 0))
    min_retweets = max(0, int(min_retweets or 0))
    min_comments = max(0, int(min_comments or 0))
    min_views = max(0, int(min_views or 0))

    mode = (date_filter or "all").strip().lower()
    if mode not in ("all", "today", "week", "month", "date"):
        mode = "all"

    since_iso, until_iso, target_date = _resolve_date_bounds(mode, upload_date)

    if search_pool is None:
        if min_likes > 0 or min_retweets > 0 or min_comments > 0 or target_date or mode in (
            "week",
            "month",
        ):
            pool = max(max_results, min(_default_search_pool(), 50))
        else:
            pool = max_results
    else:
        pool = max(1, min(int(search_pool), 50))
    pool = max(pool, max_results)

    search_query = build_x_query(
        q,
        min_likes=min_likes,
        min_retweets=min_retweets,
        min_comments=min_comments,
        since=since_iso,
        until=until_iso,
        videos_only=videos_only,
    )

    raw_items = _call_apify(search_query, pool)

    matched: list[dict[str, Any]] = []
    below: list[dict[str, Any]] = []
    scanned = 0
    skipped_threshold = 0
    skipped_date = 0
    skipped_no_url = 0
    skipped_error = 0

    require_known = min_likes > 0 or min_retweets > 0 or min_comments > 0

    for raw in raw_items:
        item = normalize_tweet(raw)
        if not item:
            skipped_no_url += 1
            continue

        scanned += 1

        if target_date and item.get("upload_date") and item["upload_date"] != target_date:
            skipped_date += 1
            continue

        likes = item.get("like_count")
        retweets = item.get("retweet_count")
        comments = item.get("comment_count")
        views = item.get("view_count")

        # min_views 仅本地弱过滤（有值才比）
        if min_views > 0 and views is not None and views < min_views:
            skipped_threshold += 1
            if target_date and len(below) < max_results:
                below.append({**item, "below_threshold": True})
            continue

        ok = _passes_thresholds(
            likes,
            retweets,
            comments,
            min_likes,
            min_retweets,
            min_comments,
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
        raise ValueError("X 搜索无结果（可放宽关键词/阈值后重试）")

    return {
        "query": q,
        "search_query": search_query,
        "platform": "x",
        "max_results": max_results,
        "min_likes": min_likes,
        "min_retweets": min_retweets,
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
