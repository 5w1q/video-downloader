#!/usr/bin/env python3
"""
将指定邮箱用户设为 VIP，到期时间设为远期（用于本地/测试「永久」额度）。

用法:
  python scripts/grant_vip_permanent.py user@example.com

依赖: 在仓库根目录执行，会操作 backend/data/app.db。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from database import get_db  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="将用户设为长期有效 VIP（AI 总结不限次）")
    ap.add_argument("email", help="用户邮箱（与注册时一致）")
    args = ap.parse_args()
    email = (args.email or "").strip()
    if not email:
        print("email 不能为空", file=sys.stderr)
        return 1

    expire = "2099-12-31T23:59:59+00:00"
    with get_db() as conn:
        cur = conn.execute(
            """
            UPDATE users
            SET is_vip = 1,
                vip_expire_at = ?,
                daily_summary_count = 0,
                updated_at = datetime('now')
            WHERE email = ?
            """,
            (expire, email),
        )
        if cur.rowcount == 0:
            print(f"未找到邮箱: {email}", file=sys.stderr)
            return 1
    print(f"已设置 VIP（至 {expire}）: {email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
