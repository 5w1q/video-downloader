import os

from fastapi import HTTPException, Request

from ab_client import get_ab_user_from_request
from database import sync_user_from_ab


def local_mode_enabled() -> bool:
    """完全本地运行：不依赖 Ab 主站 / 公网服务器。设 LOCAL_MODE=1 开启。"""
    return (os.getenv("LOCAL_MODE") or "").strip().lower() in ("1", "true", "yes", "on")


def _local_stub_ab_user() -> dict:
    return {
        "id": 1,
        "email": "local@localhost",
        "username": "local",
        "display_name": "本地用户",
        "is_vip": True,
        "membership_until": None,
        "credits": 9999,
    }


def _parse_ab_credits(raw) -> float:
    """主站 summary 余额；兼容 JSON 数字或字符串。"""
    if raw is None or raw == "":
        return 0.0
    try:
        return round(float(raw), 4)
    except (TypeError, ValueError):
        return 0.0


async def get_current_user(request: Request) -> dict:
    """以 Ab 主站会话为准：必须已登录。LOCAL_MODE 下返回本地虚拟用户。"""
    if local_mode_enabled():
        ab_user = _local_stub_ab_user()
    else:
        ab_user = await get_ab_user_from_request(request)
    local_user = sync_user_from_ab(ab_user)
    return {
        "id": local_user.get("id"),  # 本地 user id，供本项目数据库关联使用
        "ab_user_id": ab_user.get("id"),
        "email": local_user.get("email") or ab_user.get("email", ""),
        "username": ab_user.get("username", ""),
        "display_name": ab_user.get("display_name", ""),
        "is_vip": bool(ab_user.get("is_vip")),
        "membership_until": ab_user.get("membership_until"),
        "credits": _parse_ab_credits(ab_user.get("credits")),
    }


async def get_optional_user(request: Request) -> dict | None:
    """以 Ab 主站会话为准：未登录返回 None。"""
    try:
        return await get_current_user(request)
    except HTTPException as e:
        if e.status_code == 401:
            return None
        raise
