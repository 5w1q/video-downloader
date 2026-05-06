import os
import uuid
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

import httpx
from fastapi import HTTPException, Request


def _env_truthy(name: str, default: str = "0") -> bool:
    return (os.getenv(name, default) or "").strip().lower() in ("1", "true", "yes", "on")


def _billing_enabled() -> bool:
    """
    是否启用 Ab 主站计费检查。
    默认启用；本地独立运行可在 backend/.env 设 AB_BILLING_DISABLED=1 关闭。
    """
    return not _env_truthy("AB_BILLING_DISABLED", "0")


def _usage_reporting_enabled() -> bool:
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


def _action_cost(action: str) -> int | None:
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
        return max(0, int(raw))
    except ValueError:
        return None


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
        "credits": int(summary_data.get("credits") or 0),
    }


async def try_consume_from_request(
    request: Request,
    action: str,
    idempotency_key: str | None = None,
    credits_cost: int | None = None,
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
    cost = _action_cost(action) if credits_cost is None else credits_cost

    payload = {
        "app_id": app_id,
        "idempotency_key": idem,
    }
    if cost is not None:
        payload["credits_cost"] = int(cost)

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
    return data if isinstance(data, dict) else {}


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
