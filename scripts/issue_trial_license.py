#!/usr/bin/env python3
"""
使用 trial_private.pem 签发试用期 JWT（RS256）。

示例（3 天，从「当前时刻」起算）:
  python scripts/issue_trial_license.py --days 3

将输出的整行 token 写入对方 .env 或 docker-compose:
  TRIAL_LICENSE=eyJhbGciOiJSUzI1NiIs...
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt


def main() -> int:
    ap = argparse.ArgumentParser(description="签发 TRIAL_LICENSE JWT")
    ap.add_argument(
        "--private-key",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "backend" / "keys" / "trial_private.pem",
        help="私钥 PEM 路径",
    )
    ap.add_argument("--days", type=float, default=3.0, help="有效天数（可小数，如 0.5）")
    ap.add_argument(
        "--sub",
        default="trial",
        help="JWT sub 字段（标识用途）",
    )
    args = ap.parse_args()

    if not args.private_key.is_file():
        print(f"找不到私钥: {args.private_key}", file=sys.stderr)
        print("请先运行: python scripts/generate_trial_keys.py", file=sys.stderr)
        return 1

    pem = args.private_key.read_text(encoding="utf-8")
    now = datetime.now(timezone.utc)
    exp = now + timedelta(days=args.days)
    payload = {
        "sub": args.sub,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    token = jwt.encode(payload, pem, algorithm="RS256")
    if isinstance(token, bytes):
        token = token.decode("utf-8")

    print("=== 将下列一行加入对方环境变量 ===\n")
    print(f"TRIAL_LICENSE={token}\n")
    print(f"=== 到期时间 (UTC): {exp.isoformat()} ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
