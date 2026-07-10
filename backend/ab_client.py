import logging
import os
import uuid
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

import httpx
from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)


def _env_truthy(name: str, default: str = "0") -> bool:
    return (os.getenv(name, default) or "").strip().lower() in ("1", "true", "yes", "on")


def _billing_enabled() -> bool:
    """
    是否启用 Ab 主站计费检查。
    默认启用；本地独立运行可设 AB_BILLING_DISABLED=1，或 LOCAL_MODE=1（自动关闭计费）。
    """
    if _env_truthy("LOCAL_MODE", "0"):
        return False
    return not _env_truthy("AB_BILLING_DISABLED", "0")


def _billing_refund_enabled() -> bool:
    """任务失败是否调用主站 refund-consume。默认开启；设 AB_BILLING_REFUND_DISABLED=1 关闭。"""
    return not _env_truthy("AB_BILLING_REFUND_DISABLED", "0")


def _billing_refund_secret() -> str:
    return (os.getenv("AB_BILLING_REFUND_SECRET") or "").strip()


def _usage_reporting_enabled() -> bool:
    if _env_truthy("LOCAL_MODE", "0"):
        return False
    return _env_truthy("AB_USAGE_REPORT_ENABLED", "1")


def _usage_report_path() -> str:
    return (os.getenv("AB_USAGE_REPORT_PATH", "/api/account/app-usage") or "").strip()


def _ab_base_url() -> str:
    return os.getenv("AB_BASE_URL", "https://sayhi-ab.asia").rstrip("/")


def _ab_login_path() -> str:
    return os.getenv("AB_LOGIN_PATH", "/login.html")


def _ab_app_id() -> str:
    return os.getenv("AB_APP_ID", "video-downloader").strip() or "video-downloader"


def _ab_timeout_sec() -> float:
    try:
        return max(3.0, float(os.getenv("AB_TIMEOUT_SEC", "20")))
    except ValueError:
        return 20.0


def _parse_summary_credits(raw) -> float:
    """主站 /api/account/summary 的 credits；兼容数字或字符串。"""
    if raw is None or raw == "":
        return 0.0
    try:
        return round(float(raw), 4)
    except (TypeError, ValueError):
        return 0.0


def _cookie_header_from_request(request: Request) -> str:
    return request.headers.get("cookie", "")


def _local_next_path(next_url: str) -> str:
    """
    Ab 目前只接受站内相对路径 next。
    如为跨域 URL，降级为首页，登录后用户回到主站再返回子应用。
    """
    try:
        parsed = urlparse(next_url or "")
        if parsed.scheme or parsed.netloc:
            return "/"
        path = parsed.path or "/"
        if not path.startswith("/") or path.startswith("//"):
            return "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        return path
    except Exception:
        return "/"


def build_ab_login_url(next_url: str = "/", mode: str = "login") -> str:
    base = _ab_base_url()
    login_path = _ab_login_path()
    safe_next = _local_next_path(next_url)
    mode_q = "&mode=register" if mode == "register" else ""
    return f"{base}{login_path}?next={quote(safe_next, safe='/%?=&')}{mode_q}"


def _action_cost(action: str) -> float | None:
    """
    从环境变量读取本次动作的积分扣费（元口径，可为小数如 0.29）；未设置时返回 None。
    try-consume 实际传的单价由 try_consume_from_request 收敛（总结有兜底，其余默认为 0）。
    """
    key_map = {
        "download": "AB_CREDITS_COST_DOWNLOAD",
        "summarize": "AB_CREDITS_COST_SUMMARIZE",
        "chat": "AB_CREDITS_COST_CHAT",
        "bulk_download": "AB_CREDITS_COST_BULK_DOWNLOAD",
    }
    key = key_map.get(action)
    if not key:
        return None
    raw = os.getenv(key, "").strip()
    if raw == "":
        return None
    try:
        v = float(raw)
    except ValueError:
        return None
    if v < 0:
        return None
    return round(v, 4)


# AI 总结：env 未配置 AB_CREDITS_COST_SUMMARIZE 时的兜底单价（元）。批量/普通下载不走 try-consume。
_TRY_CONSUME_SUMMARIZE_FALLBACK = 0.29


def make_action_idempotency_key(action: str) -> str:
    """生成 try-consume / refund-consume 共用的幂等键。"""
    return f"{_ab_app_id()}:{action}:{uuid.uuid4().hex}"


def consume_refundable(consume: dict | None) -> bool:
    """扣费已成功且主站侧可返还（积分或免费试次）。"""
    if not consume or not consume.get("allowed"):
        return False
    mode = consume.get("mode") or ""
    return mode in ("credits", "free_trial")


async def _ab_get(path: str, cookie_header: str) -> dict:
    async with httpx.AsyncClient(timeout=_ab_timeout_sec()) as client:
        res = await client.get(
            f"{_ab_base_url()}{path}",
            headers={"Cookie": cookie_header, "Accept": "application/json"},
        )
    try:
        data = res.json()
    except Exception:
        data = {}
    if res.status_code == 401:
        raise HTTPException(status_code=401, detail="请先登录 Ab 主站")
    if not res.is_success:
        msg = data.get("error") if isinstance(data, dict) else None
        raise HTTPException(status_code=502, detail=msg or f"主站接口异常: {res.status_code}")
    return data if isinstance(data, dict) else {}


async def get_ab_user_from_request(request: Request) -> dict:
    cookie_header = _cookie_header_from_request(request)
    if not cookie_header:
        raise HTTPException(status_code=401, detail="请先登录 Ab 主站")

    me_data = await _ab_get("/api/auth/me", cookie_header)
    summary_data = await _ab_get("/api/account/summary", cookie_header)

    if not me_data.get("ok") or not summary_data.get("ok"):
        raise HTTPException(status_code=502, detail="主站账户状态读取失败")

    user = me_data.get("user") or {}
    return {
        "id": user.get("id"),
        "email": user.get("email") or user.get("username") or "",
        "username": user.get("username") or "",
        "display_name": user.get("display_name") or "",
        "is_vip": bool(summary_data.get("is_member")),
        "membership_until": summary_data.get("membership_until"),
        "credits": _parse_summary_credits(summary_data.get("credits")),
    }


async def try_consume_from_request(
    request: Request,
    action: str,
    idempotency_key: str | None = None,
    credits_cost: float | None = None,
) -> dict:
    if not _billing_enabled():
        return {
            "ok": True,
            "allowed": True,
            "message": "billing disabled",
            "reason": "billing_disabled",
        }

    cookie_header = _cookie_header_from_request(request)
    if not cookie_header:
        raise HTTPException(status_code=401, detail="请先登录 Ab 主站")

    app_id = _ab_app_id()
    idem = idempotency_key or f"{app_id}:{action}:{uuid.uuid4().hex}"
    cost = credits_cost if credits_cost is not None else _action_cost(action)
    if action == "summarize":
        if cost is None:
            cost = _TRY_CONSUME_SUMMARIZE_FALLBACK
    elif cost is None:
        cost = 0.0

    payload = {
        "app_id": app_id,
        "idempotency_key": idem,
        "credits_cost": float(cost),
    }

    async with httpx.AsyncClient(timeout=_ab_timeout_sec()) as client:
        res = await client.post(
            f"{_ab_base_url()}/api/account/try-consume",
            json=payload,
            headers={"Cookie": cookie_header, "Content-Type": "application/json"},
        )

    try:
        data = res.json()
    except Exception:
        data = {}

    if res.status_code == 401:
        raise HTTPException(status_code=401, detail="请先登录 Ab 主站")
    if not res.is_success:
        msg = data.get("error") if isinstance(data, dict) else None
        raise HTTPException(status_code=502, detail=msg or "主站扣费服务不可用")
    out = data if isinstance(data, dict) else {}
    out["idempotency_key"] = idem
    return out


async def refund_consume_from_request(
    request: Request,
    idempotency_key: str,
    *,
    reason: str = "summarize_failed",
    user_id: int | None = None,
) -> dict:
    """
    任务失败时返还积分或免费试次（幂等）。
    默认用用户 Cookie 调主站；若配置 AB_BILLING_REFUND_SECRET 且传入 user_id 则走内网密钥。
    """
    if not _billing_enabled() or not _billing_refund_enabled():
        return {"ok": True, "refunded": False, "mode": "billing_disabled"}

    app_id = _ab_app_id()
    refund_reason = (reason or "summarize_failed").strip()[:120] or "summarize_failed"
    payload = {
        "app_id": app_id,
        "idempotency_key": idempotency_key,
        "reason": refund_reason,
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json"}

    secret = _billing_refund_secret()
    if secret and user_id is not None and int(user_id) > 0:
        headers["X-Ab-Billing-Refund-Secret"] = secret
        payload["user_id"] = int(user_id)
    else:
        cookie_header = _cookie_header_from_request(request)
        if not cookie_header:
            logger.warning("refund-consume skipped: no cookie and no refund secret")
            return {"ok": False, "refunded": False, "mode": "no_auth"}
        headers["Cookie"] = cookie_header

    url = f"{_ab_base_url()}/api/account/refund-consume"
    try:
        async with httpx.AsyncClient(timeout=_ab_timeout_sec()) as client:
            res = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as e:
        logger.warning("refund-consume request failed: %s", e)
        return {"ok": False, "refunded": False, "mode": "request_failed"}

    try:
        data = res.json()
    except Exception:
        data = {}

    if not res.is_success:
        msg = data.get("error") if isinstance(data, dict) else None
        logger.warning(
            "refund-consume HTTP %s idem=%s body=%s",
            res.status_code,
            idempotency_key,
            (res.text or "")[:300],
        )
        return {
            "ok": False,
            "refunded": False,
            "mode": "http_error",
            "error": msg or f"HTTP {res.status_code}",
        }
    return data if isinstance(data, dict) else {"ok": True, "refunded": False}


async def proxy_ab_logout(request: Request) -> None:
    cookie_header = _cookie_header_from_request(request)
    if not cookie_header:
        return
    async with httpx.AsyncClient(timeout=_ab_timeout_sec()) as client:
        await client.post(
            f"{_ab_base_url()}/api/auth/logout",
            headers={"Cookie": cookie_header, "Accept": "application/json"},
        )


async def report_usage_from_request(
    request: Request,
    action: str,
    status: str = "ok",
    message: str = "",
    extra: dict | None = None,
    request_id: str | None = None,
    duration_ms: int | None = None,
    code: str | None = None,
) -> None:
    """
    向 Ab 主站上报应用侧流水（仅统计用途，失败不影响主流程）。
    默认上报到 /api/account/app-usage，可通过 AB_USAGE_REPORT_PATH 覆盖。
    """
    if not _usage_reporting_enabled():
        return
    path = _usage_report_path()
    if not path:
        return
    cookie_header = _cookie_header_from_request(request)
    if not cookie_header:
        return

    payload: dict = {
        "app_id": _ab_app_id(),
        "action": action,
        "status": status,
        "message": message[:500],
        "request_id": request_id or request.headers.get("x-request-id") or uuid.uuid4().hex,
        "method": request.method,
        "path": request.url.path,
        "host": request.url.hostname or "",
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    if duration_ms is not None:
        payload["duration_ms"] = max(0, int(duration_ms))
    if code:
        payload["code"] = str(code)[:100]
    if extra:
        payload["extra"] = extra
    try:
        async with httpx.AsyncClient(timeout=_ab_timeout_sec()) as client:
            await client.post(
                f"{_ab_base_url()}{path}",
                json=payload,
                headers={
                    "Cookie": cookie_header,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
    except Exception:
        # 统计上报是旁路，不影响主功能
        return
