"""批量 ZIP 上传阿里云 OSS，下载走 CDN/OSS 出口以规避 ECS 公网带宽瓶颈。"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from urllib.parse import quote

import oss2


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip()


def _bucket_name() -> str:
    return _env("OSS_BUCKET") or "video-prod-files"


def oss_bulk_zip_enabled() -> bool:
    return bool(
        _env("OSS_ACCESS_KEY_ID")
        and _env("OSS_ACCESS_KEY_SECRET")
        and _env("OSS_ENDPOINT")
        and _bucket_name()
    )


def _normalize_prefix(raw: str) -> str:
    s = (raw or "bulk-zip").strip().strip("/")
    return f"{s}/" if s else ""


def upload_bulk_zip_get_download_url(local_zip: str) -> str:
    """
    上传本地 ZIP，返回浏览器可用的下载 URL。

    - 若设置 OSS_CDN_BASE_URL：对象设为 public-read，返回 CDN 拼接路径（需控制台/CDN 源站指向该 Bucket，
      且建议 Bucket 策略仅允许匿名 GetObject 作用于此前缀）。
    - 否则返回 OSS 预签名 URL（仍走 OSS 出口，不经 ECS）。
    """
    if not oss_bulk_zip_enabled():
        raise RuntimeError("OSS 未配置完整（OSS_ACCESS_KEY_ID / SECRET / ENDPOINT / BUCKET）")

    endpoint = _env("OSS_ENDPOINT")
    bucket_name = _bucket_name()
    cdn_base = _env("OSS_CDN_BASE_URL").rstrip("/")
    expire_sec = int(_env("OSS_URL_EXPIRE_SECONDS", "3600") or "3600")
    expire_sec = max(60, min(expire_sec, 86400))

    prefix = _normalize_prefix(_env("OSS_BULK_PREFIX", "bulk-zip"))
    object_key = f"{prefix}{uuid.uuid4().hex}.zip"

    auth = oss2.Auth(_env("OSS_ACCESS_KEY_ID"), _env("OSS_ACCESS_KEY_SECRET"))
    bucket = oss2.Bucket(auth, endpoint, bucket_name)

    headers: dict[str, str] = {
        "Content-Type": "application/zip",
        "Content-Disposition": "attachment; filename=batch-download.zip",
    }
    if cdn_base:
        headers["x-oss-object-acl"] = "public-read"

    bucket.put_object_from_file(object_key, local_zip, headers=headers)

    try:
        Path(local_zip).unlink(missing_ok=True)
    except OSError:
        pass

    if cdn_base:
        return f"{cdn_base}/{quote(object_key, safe='/')}"

    return bucket.sign_url("GET", object_key, expire_sec, slash_safe=True)
