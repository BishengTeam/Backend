"""Private storage boundary for NISP verification materials."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from app.port.config import settings
from app.port.exceptions import ThirdPartyException, ValidationException


NISP_PREFIX = "nisp"
NISP_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".pdf"}
NISP_ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/jpg", "application/pdf"}
NISP_MAX_BYTES = 10 * 1024 * 1024


def source_prefix(user_id: int) -> str:
    return f"{NISP_PREFIX}/materials/{user_id}/"


def assert_owned_source_key(user_id: int, storage_key: str) -> None:
    if not storage_key.startswith(source_prefix(user_id)):
        raise ValidationException("材料不属于当前用户")
    if storage_key.startswith("/") or ".." in storage_key:
        raise ValidationException("材料对象键无效")


class NispObjectStorage:

    def __init__(self) -> None:
        self.storage_type = settings.RENSHE_STORAGE_TYPE

    async def save_source(
        self,
        *,
        user_id: int,
        filename: str,
        content_type: str | None,
        data: bytes,
        max_bytes: int = NISP_MAX_BYTES,
    ) -> tuple[str, int, str]:
        normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
        extension = Path(filename).suffix.lower()
        if extension not in NISP_ALLOWED_EXTENSIONS:
            raise ValidationException("NISP 材料仅支持 JPG 或 PDF 格式")
        if len(data) > max_bytes:
            raise ValidationException(f"材料超过大小限制 ({max_bytes // 1024 // 1024}MB)")

        storage_key = f"{source_prefix(user_id)}{uuid.uuid4().hex}{extension}"
        await self._put(storage_key, data, normalized_type or "application/octet-stream")
        return storage_key, len(data), hashlib.sha256(data).hexdigest()

    async def signed_get_url(self, storage_key: str) -> str:
        if self.storage_type == "local":
            raise ThirdPartyException("本地开发模式不生成 NISP 材料签名地址")
        if self.storage_type != "aliyun_oss":
            raise ThirdPartyException("NISP OSS 未配置")
        return await self._sign(storage_key)

    async def _put(self, key: str, data: bytes, content_type: str) -> None:
        if self.storage_type == "local":
            target = Path(settings.RENSHE_STORAGE_LOCAL_ROOT) / key
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            return
        if self.storage_type != "aliyun_oss":
            raise ThirdPartyException("NISP OSS 未配置")
        await self._oss_put(key, data, content_type)

    async def _sign(self, key: str) -> str:
        import oss2

        if not settings.OSS_ACCESS_KEY_ID or not settings.OSS_ACCESS_KEY_SECRET:
            raise ThirdPartyException("NISP OSS 未配置")
        auth = oss2.Auth(settings.OSS_ACCESS_KEY_ID, settings.OSS_ACCESS_KEY_SECRET)
        bucket = oss2.Bucket(
            auth,
            settings.OSS_ENDPOINT,
            settings.OSS_BUCKET_NAME,
            connect_timeout=5,
        )
        return bucket.sign_url("GET", key, 3600)

    async def _oss_put(self, key: str, data: bytes, content_type: str) -> None:
        import asyncio
        import oss2

        if not settings.OSS_ACCESS_KEY_ID or not settings.OSS_ACCESS_KEY_SECRET:
            raise ThirdPartyException("NISP OSS 未配置")
        auth = oss2.Auth(settings.OSS_ACCESS_KEY_ID, settings.OSS_ACCESS_KEY_SECRET)
        bucket = oss2.Bucket(
            auth,
            settings.OSS_ENDPOINT,
            settings.OSS_BUCKET_NAME,
            connect_timeout=5,
        )
        await asyncio.to_thread(bucket.put_object, key, data, headers={"Content-Type": content_type})
