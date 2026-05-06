#!/usr/bin/env python3
"""生成 RSA 密钥对，用于签发 / 校验试用期 JWT。私钥切勿提交或发给对方。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def main() -> int:
    p = argparse.ArgumentParser(description="生成 trial_public.pem + trial_private.pem")
    p.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "backend" / "keys",
        help="输出目录（默认 backend/keys）",
    )
    args = p.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    priv_path = args.out_dir / "trial_private.pem"
    pub_path = args.out_dir / "trial_public.pem"
    priv_path.write_bytes(priv_pem)
    pub_path.write_bytes(pub_pem)
    try:
        priv_path.chmod(0o600)
    except OSError:
        pass

    print(f"已写入:\n  {pub_path}\n  {priv_path}")
    print("\n下一步: 将 trial_public.pem 打入给对方构建的 Docker 镜像（或挂载到容器内同路径）。")
    print("切勿泄露 trial_private.pem。使用 scripts/issue_trial_license.py 签发 TRIAL_LICENSE。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
