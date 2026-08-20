"""Private storage boundary for H3C verification materials and exports."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import urllib.parse
import uuid
from pathlib import Path

from app.port.config import settings
from app.port.exceptions import ThirdPartyException, ValidationException


H3C_PREFIX = "h3c"


def source_prefix(user_id: int) -> str:
    return f"{H3C_PREFIX}/materials/{user_id}/"


def assert_owned_source_key(user_id: int, storage_key: str) -> None:
    if not storage_key.startswith(source_prefix(user_id)):
        raise ValidationException("材料不属于当前用户")
    if storage_key.startswith("/") or ".." in storage_key:
        raise ValidationException("材料对象键无效")


class H3cObjectStorage:
    def __init__(self) -> None:
        self.storage_type = settings.RENSHE_STORAGE_TYPE

    async def save_source(
        self,
        *,
        user_id: int,
        filename: str,
        content_type: str | None,
        data: bytes,
        max_bytes: int,
    ) -> tuple[str, int, str]:
        normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
        extension = Path(filename).suffix.lower()
        if normalized_type not in {"image/jpeg", "image/jpg"} or extension not in {".jpg", ".jpeg"}:
            raise ValidationException("H3C 证明材料必须为 JPG 图片")
        if len(data) > max_bytes:
            raise ValidationException("H3C 证明图片超过大小限制")
        if len(data) < 4 or data[:3] != b"\xff\xd8\xff":
            raise ValidationException("H3C 证明图片格式无效")

        storage_key = f"{source_prefix(user_id)}{uuid.uuid4().hex}.jpg"
        await self._put(storage_key, data, "image/jpeg")
        return storage_key, len(data), hashlib.sha256(data).hexdigest()

    async def signed_get_url(self, storage_key: str) -> str:
        if self.storage_type == "local":
            raise ThirdPartyException("本地开发模式不生成 H3C 材料签名地址")
        if self.storage_type != "aliyun_oss":
            raise ThirdPartyException("H3C OSS 未配置")

        def _sign() -> str:
            return self._bucket().sign_url(
                "GET",
                storage_key,
                settings.ALIYUN_OSS_SIGNED_URL_TTL_SECONDS,
                params={
                    "response-content-disposition": (
                        "inline; filename=h3c-material.jpg"
                    )
                },
            )

        return await asyncio.to_thread(_sign)

    async def download_file(self, storage_key: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if self.storage_type == "local":
            source = self._local_path(storage_key)
            if not source.is_file():
                raise ValidationException("H3C 材料不存在")
            await asyncio.to_thread(shutil.copyfile, source, destination)
            return
        if self.storage_type != "aliyun_oss":
            raise ThirdPartyException("H3C OSS 未配置")

        def _download() -> None:
            self._bucket().get_object_to_file(storage_key, str(destination))

        await asyncio.to_thread(_download)

    async def upload_file(self, storage_key: str, source: Path, content_type: str) -> None:
        if self.storage_type == "local":
            target = self._local_path(storage_key)
            target.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(shutil.copyfile, source, target)
            return
        if self.storage_type != "aliyun_oss":
            raise ThirdPartyException("H3C OSS 未配置")

        def _upload() -> None:
            import oss2

            result = oss2.resumable_upload(
                self._bucket(),
                storage_key,
                str(source),
                multipart_threshold=100 * 1024 * 1024,
                part_size=16 * 1024 * 1024,
                num_threads=4,
                headers={"Content-Type": content_type},
            )
            if result is not None and getattr(result, "status", 200) // 100 != 2:
                raise ThirdPartyException("H3C 导出产物上传失败")

        await asyncio.to_thread(_upload)

    async def delete(self, storage_key: str) -> None:
        if self.storage_type == "local":
            path = self._local_path(storage_key)
            if path.is_file():
                await asyncio.to_thread(path.unlink)
            return
        if self.storage_type != "aliyun_oss":
            return
        await asyncio.to_thread(self._bucket().delete_object, storage_key)

    async def _put(self, storage_key: str, data: bytes, content_type: str) -> None:
        if self.storage_type == "local":
            path = self._local_path(storage_key)
            path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(path.write_bytes, data)
            return
        if self.storage_type != "aliyun_oss":
            raise ThirdPartyException("H3C OSS 未配置")

        def _upload() -> None:
            result = self._bucket().put_object(
                storage_key,
                data,
                headers={"Content-Type": content_type},
            )
            if result.status // 100 != 2:
                raise ThirdPartyException("H3C 材料上传失败")

        await asyncio.to_thread(_upload)

    @staticmethod
    def _local_path(storage_key: str) -> Path:
        root = (Path(settings.UPLOAD_DIR).resolve() / "private").resolve()
        target = (root / storage_key).resolve()
        if root not in target.parents:
            raise ValidationException("H3C 材料对象键无效")
        return target

    @staticmethod
    def _bucket() -> oss2.Bucket:
        import oss2

        if not all(
            (
                settings.ALIYUN_OSS_ENDPOINT,
                settings.ALIYUN_OSS_BUCKET,
                settings.ALIYUN_OSS_ACCESS_KEY_ID,
                settings.ALIYUN_OSS_ACCESS_KEY_SECRET,
            )
        ):
            raise ThirdPartyException("H3C OSS 配置不完整")
        auth = oss2.Auth(
            settings.ALIYUN_OSS_ACCESS_KEY_ID,
            settings.ALIYUN_OSS_ACCESS_KEY_SECRET,
        )
        return oss2.Bucket(
            auth,
            settings.ALIYUN_OSS_ENDPOINT,
            settings.ALIYUN_OSS_BUCKET,
        )
