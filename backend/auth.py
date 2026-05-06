from fastapi import HTTPException, Request

from ab_client import get_ab_user_from_request
from database import sync_user_from_ab


async def get_current_user(request: Request) -> dict:
    """以 Ab 主站会话为准：必须已登录。"""
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
        "credits": int(ab_user.get("credits") or 0),
    }


async def get_optional_user(request: Request) -> dict | None:
    """以 Ab 主站会话为准：未登录返回 None。"""
    try:
        return await get_current_user(request)
    except HTTPException as e:
        if e.status_code == 401:
            return None
        raise
