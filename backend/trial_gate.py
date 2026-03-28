"""
试用期门禁（JWT + RSA 公钥校验）。

规则：
- 若存在公钥文件（默认 backend/keys/trial_public.pem，或由环境变量 TRIAL_PUBLIC_KEY_FILE 指定），
  则必须在环境变量 TRIAL_LICENSE 中提供由你方私钥签发的 JWT，且未过期，否则进程直接退出。
- 若公钥文件不存在，则不启用（公开仓库 / 自用部署不受影响）。

对方若拿到源码并删除公钥或改掉本模块，可绕过——这是交付类项目的常见边界，防君子不防刻意破解。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import jwt
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError


def _public_key_path() -> Path | None:
    override = os.getenv("TRIAL_PUBLIC_KEY_FILE", "").strip()
    if override:
        p = Path(override)
        return p if p.is_file() else None
    default = Path(__file__).resolve().parent / "keys" / "trial_public.pem"
    return default if default.is_file() else None


def enforce_trial_license_or_exit() -> None:
    key_path = _public_key_path()
    if key_path is None:
        return

    token = os.getenv("TRIAL_LICENSE", "").strip()
    if not token:
        print(
            "ERROR: 已启用试用期校验：检测到公钥文件 "
            f"({key_path})，但未设置环境变量 TRIAL_LICENSE。",
            file=sys.stderr,
        )
        sys.exit(1)

    pem = key_path.read_text(encoding="utf-8")
    try:
        payload = jwt.decode(
            token,
            pem,
            algorithms=["RS256"],
            options={"require": ["exp"]},
            leeway=60,
        )
    except ExpiredSignatureError:
        print("ERROR: 试用期已结束（TRIAL_LICENSE 已过期）。", file=sys.stderr)
        sys.exit(1)
    except InvalidTokenError as e:
        print(f"ERROR: TRIAL_LICENSE 无效或已损坏: {e}", file=sys.stderr)
        sys.exit(1)

    if os.getenv("TRIAL_GATE_DEBUG", "").lower() in ("1", "true", "yes"):
        print(
            "trial_gate: OK sub=%s exp=%s"
            % (payload.get("sub"), payload.get("exp"))
        )
