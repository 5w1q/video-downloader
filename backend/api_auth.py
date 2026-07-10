from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from ab_client import build_ab_login_url, proxy_ab_logout
from auth import get_current_user, local_mode_enabled

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/login")
async def login_redirect(next: str = "/"):
    if local_mode_enabled():
        return RedirectResponse(url=next or "/", status_code=307)
    return RedirectResponse(url=build_ab_login_url(next_url=next, mode="login"), status_code=307)


@router.get("/register")
async def register_redirect(next: str = "/"):
    if local_mode_enabled():
        return RedirectResponse(url=next or "/", status_code=307)
    return RedirectResponse(url=build_ab_login_url(next_url=next, mode="register"), status_code=307)


@router.get("/me")
async def get_me(user: dict = Depends(get_current_user)):
    return {
        "success": True,
        "data": user,
    }


@router.post("/logout")
async def logout(request: Request):
    if not local_mode_enabled():
        await proxy_ab_logout(request)
    return {"success": True}
