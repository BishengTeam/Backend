"""Private storage boundary for NISP verification materials."""

from __future__ import annotations

import hashlib
import shutil
import uuid
import asyncio
from pathlib import Path

from app.port.config import settings
from app.port.exceptions import ThirdPartyException, ValidationException


NISP_PREFIX = "nisp"
NISP_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".pdf"}
NISP_ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/jpg", "application/pdf"}
NISP_MAX_BYTES = 10 * 1024 * 1024
NISP_MATERIAL_RULES = {
    "id_card_both_sides": (".pdf", "application/pdf", "PDF"),
    "portrait_photo": (".jpg", "image/jpeg", "JPG"),
    "xuexin_report": (".pdf", "application/pdf", "PDF"),
    "application_form": (".pdf", "application/pdf", "PDF"),
}


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
        material_type: str,
        filename: str,
        content_type: str | None,
        data: bytes,
        max_bytes: int = NISP_MAX_BYTES,
    ) -> tuple[str, int, str]:
        normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
        extension = Path(filename).suffix.lower()
        rule = NISP_MATERIAL_RULES.get(material_type)
        if rule is None:
            raise ValidationException(f"不支持的材料类型: {material_type}")
        expected_extension, expected_content_type, display_name = rule
        extension_allowed = extension in (
            {".jpg", ".jpeg"} if expected_extension == ".jpg" else {expected_extension}
        )
        content_type_allowed = normalized_type in (
            {"image/jpeg", "image/jpg"}
            if expected_content_type == "image/jpeg"
            else {expected_content_type}
        )
        if not extension_allowed and not content_type_allowed:
            raise ValidationException(f"{material_type} 材料仅支持 {display_name} 格式")
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

    async def download_file(self, storage_key: str, destination: Path) -> None:
        """Download a private NISP object to a local export staging file."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        if self.storage_type == "local":
            source = self._local_path(storage_key)
            if not source.is_file():
                raise ValidationException("NISP 材料不存在")
            await asyncio.to_thread(shutil.copyfile, source, destination)
            return
        if self.storage_type != "aliyun_oss":
            raise ThirdPartyException("NISP OSS 未配置")

        def _download() -> None:
            import oss2

            auth = oss2.Auth(
                settings.ALIYUN_OSS_ACCESS_KEY_ID,
                settings.ALIYUN_OSS_ACCESS_KEY_SECRET,
            )
            bucket = oss2.Bucket(
                auth,
                settings.ALIYUN_OSS_ENDPOINT,
                settings.ALIYUN_OSS_BUCKET,
                connect_timeout=5,
            )
            bucket.get_object_to_file(storage_key, str(destination))

        await asyncio.to_thread(_download)

    async def _sign(self, key: str) -> str:
        import oss2

        if not settings.ALIYUN_OSS_ACCESS_KEY_ID or not settings.ALIYUN_OSS_ACCESS_KEY_SECRET:
            raise ThirdPartyException("NISP OSS 未配置")
        auth = oss2.Auth(settings.ALIYUN_OSS_ACCESS_KEY_ID, settings.ALIYUN_OSS_ACCESS_KEY_SECRET)
        bucket = oss2.Bucket(
            auth,
            settings.ALIYUN_OSS_ENDPOINT,
            settings.ALIYUN_OSS_BUCKET,
            connect_timeout=5,
        )
        return bucket.sign_url("GET", key, 3600)

    async def _oss_put(self, key: str, data: bytes, content_type: str) -> None:
        import asyncio
        import oss2

        if not settings.ALIYUN_OSS_ACCESS_KEY_ID or not settings.ALIYUN_OSS_ACCESS_KEY_SECRET:
            raise ThirdPartyException("NISP OSS 未配置")
        auth = oss2.Auth(settings.ALIYUN_OSS_ACCESS_KEY_ID, settings.ALIYUN_OSS_ACCESS_KEY_SECRET)
        bucket = oss2.Bucket(
            auth,
            settings.ALIYUN_OSS_ENDPOINT,
            settings.ALIYUN_OSS_BUCKET,
            connect_timeout=5,
        )
        await asyncio.to_thread(bucket.put_object, key, data, headers={"Content-Type": content_type})

    async def save_export(
        self,
        *,
        storage_key: str,
        data: bytes,
        content_type: str = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ) -> None:
        await self._put(storage_key, data, content_type)

    async def upload_export_file(
        self, storage_key: str, source: Path, content_type: str
    ) -> None:
        """Upload a generated export without holding the whole archive in memory."""
        if self.storage_type == "local":
            target = self._local_path(storage_key)
            target.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(shutil.copyfile, source, target)
            return
        if self.storage_type != "aliyun_oss":
            raise ThirdPartyException("NISP OSS 未配置")

        def _upload() -> None:
            import oss2

            auth = oss2.Auth(
                settings.ALIYUN_OSS_ACCESS_KEY_ID,
                settings.ALIYUN_OSS_ACCESS_KEY_SECRET,
            )
            bucket = oss2.Bucket(
                auth,
                settings.ALIYUN_OSS_ENDPOINT,
                settings.ALIYUN_OSS_BUCKET,
                connect_timeout=5,
            )
            oss2.resumable_upload(
                bucket,
                storage_key,
                str(source),
                multipart_threshold=20 * 1024 * 1024,
                part_size=8 * 1024 * 1024,
                headers={"Content-Type": content_type},
            )

        await asyncio.to_thread(_upload)

    async def delete_object(self, storage_key: str) -> None:
        if self.storage_type == "local":
            from pathlib import Path
            target = Path(settings.RENSHE_STORAGE_LOCAL_ROOT) / storage_key
            target.unlink(missing_ok=True)
            return
        if self.storage_type != "aliyun_oss":
            raise ThirdPartyException("NISP OSS 未配置")
        import asyncio
        import oss2
        auth = oss2.Auth(settings.ALIYUN_OSS_ACCESS_KEY_ID, settings.ALIYUN_OSS_ACCESS_KEY_SECRET)
        bucket = oss2.Bucket(auth, settings.ALIYUN_OSS_ENDPOINT, settings.ALIYUN_OSS_BUCKET, connect_timeout=5)
        await asyncio.to_thread(bucket.delete_object, storage_key)

    @staticmethod
    def _local_path(storage_key: str) -> Path:
        root = Path(settings.RENSHE_STORAGE_LOCAL_ROOT).resolve()
        target = (root / storage_key).resolve()
        if root != target and root not in target.parents:
            raise ValidationException("NISP 材料对象键无效")
        return target
