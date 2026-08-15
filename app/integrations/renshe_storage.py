"""Private storage boundary for human-resources verification materials."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.domain.renshe.src.index import validate_material
from app.port.config import settings
from app.port.exceptions import ThirdPartyException, ValidationException


@dataclass(frozen=True, slots=True)
class StoredMaterial:
    kind: str
    source_storage_key: str | None
    storage_key: str
    original_filename: str
    extension: str
    content_type: str
    size_bytes: int
    sha256: str


def _safe_prefix() -> str:
    return settings.ALIYUN_OSS_PREFIX.strip("/") or "renshe"


def source_prefix(user_id: int) -> str:
    return f"{_safe_prefix()}/source/{user_id}/"


def assert_owned_source_key(user_id: int, storage_key: str) -> None:
    if not storage_key.startswith(source_prefix(user_id)):
        raise ValidationException("材料不属于当前用户或不是人社私有材料")
    if ".." in storage_key or storage_key.startswith("/"):
        raise ValidationException("材料对象键无效")


class RensheObjectStorage:
    """Storage facade with a local development backend and private OSS production backend."""

    def __init__(self) -> None:
        self.storage_type = settings.RENSHE_STORAGE_TYPE

    async def save_source(
        self,
        *,
        user_id: int,
        kind: str,
        filename: str,
        content_type: str | None,
        data: bytes,
    ) -> StoredMaterial:
        try:
            extension = validate_material(
                kind=kind,
                filename=filename,
                content_type=content_type,
                size_bytes=len(data),
                header=data[:16],
            )
        except ValueError as exc:
            raise ValidationException(str(exc)) from exc

        normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
        storage_key = f"{source_prefix(user_id)}{uuid.uuid4().hex}{extension}"
        await self._put(storage_key, data, normalized_type)
        return StoredMaterial(
            kind=kind,
            source_storage_key=None,
            storage_key=storage_key,
            original_filename=Path(filename).name,
            extension=extension,
            content_type=normalized_type,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )

    async def copy_version_material(
        self,
        *,
        user_id: int,
        plan_id: int,
        application_id: int,
        version_no: int,
        kind: str,
        source_key: str,
    ) -> StoredMaterial:
        assert_owned_source_key(user_id, source_key)
        data, content_type = await self._get(source_key)
        source_extension = Path(source_key).suffix.lower()
        filename = f"source{source_extension}"
        try:
            extension = validate_material(
                kind=kind,
                filename=filename,
                content_type=content_type,
                size_bytes=len(data),
                header=data[:16],
            )
        except ValueError as exc:
            raise ValidationException(f"{kind}: {exc}") from exc

        destination = (
            f"{_safe_prefix()}/versions/{plan_id}/{application_id}/"
            f"v{version_no}/{uuid.uuid4().hex}{extension}"
        )
        await self._put(destination, data, content_type)
        return StoredMaterial(
            kind=kind,
            source_storage_key=source_key,
            storage_key=destination,
            original_filename=Path(source_key).name,
            extension=extension,
            content_type=content_type,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )

    async def signed_get_url(
        self, storage_key: str, *, download_filename: str | None = None
    ) -> str:
        if self.storage_type == "disabled":
            raise ThirdPartyException("人社 OSS 未配置，材料功能不可用")
        if self.storage_type != "aliyun_oss":
            raise ThirdPartyException("本地开发存储不生成公网访问地址")

        def _sign() -> str:
            bucket = self._oss_bucket()
            params = None
            if download_filename:
                encoded = urllib.parse.quote(download_filename, safe="")
                params = {
                    "response-content-disposition": (
                        f"attachment; filename*=UTF-8''{encoded}"
                    )
                }
            return bucket.sign_url(
                "GET",
                storage_key,
                settings.ALIYUN_OSS_SIGNED_URL_TTL_SECONDS,
                params=params,
            )

        return await asyncio.to_thread(_sign)

    async def delete_many(self, storage_keys: list[str]) -> None:
        for key in storage_keys:
            if key:
                await self._delete(key)

    async def upload_file(
        self, storage_key: str, source_path: Path, content_type: str
    ) -> None:
        """Upload a potentially multi-gigabyte generated file without buffering it."""

        if self.storage_type == "local":
            destination = self._local_path(storage_key)

            def _copy() -> None:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source_path, destination)

            await asyncio.to_thread(_copy)
            return
        if self.storage_type != "aliyun_oss":
            if self.storage_type == "disabled":
                raise ThirdPartyException("人社 OSS 未配置，材料功能不可用")
            raise ThirdPartyException("未知的人社材料存储类型")

        def _upload() -> None:
            try:
                import oss2

                result = oss2.resumable_upload(
                    self._oss_bucket(),
                    storage_key,
                    str(source_path),
                    multipart_threshold=100 * 1024 * 1024,
                    part_size=16 * 1024 * 1024,
                    num_threads=4,
                    headers={"Content-Type": content_type},
                )
            except Exception as exc:
                raise ThirdPartyException("阿里云 OSS 上传生成文件失败") from exc
            if result is not None and getattr(result, "status", 200) // 100 != 2:
                raise ThirdPartyException("阿里云 OSS 上传生成文件失败")

        await asyncio.to_thread(_upload)

    async def download_file(self, storage_key: str, destination: Path) -> None:
        """Download an object to a temporary file without loading it all into memory."""

        destination.parent.mkdir(parents=True, exist_ok=True)
        if self.storage_type == "local":
            source = self._local_path(storage_key)
            if not source.is_file():
                raise ValidationException("材料对象不存在")
            await asyncio.to_thread(shutil.copyfile, source, destination)
            return
        if self.storage_type != "aliyun_oss":
            if self.storage_type == "disabled":
                raise ThirdPartyException("人社 OSS 未配置，材料功能不可用")
            raise ThirdPartyException("未知的人社材料存储类型")

        def _download() -> None:
            try:
                self._oss_bucket().get_object_to_file(storage_key, str(destination))
            except Exception as exc:
                raise ThirdPartyException("阿里云 OSS 下载材料失败") from exc

        await asyncio.to_thread(_download)

    async def _put(self, storage_key: str, data: bytes, content_type: str) -> None:
        if self.storage_type == "local":
            path = self._local_path(storage_key)

            def _write() -> None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)

            await asyncio.to_thread(_write)
            return
        if self.storage_type != "aliyun_oss":
            if self.storage_type == "disabled":
                raise ThirdPartyException("人社 OSS 未配置，材料功能不可用")
            raise ThirdPartyException("未知的人社材料存储类型")

        def _upload() -> None:
            bucket = self._oss_bucket()
            result = bucket.put_object(
                storage_key,
                data,
                headers={"Content-Type": content_type},
            )
            if result.status // 100 != 2:
                raise ThirdPartyException("阿里云 OSS 上传失败")

        await asyncio.to_thread(_upload)

    async def _get(self, storage_key: str) -> tuple[bytes, str]:
        if self.storage_type == "local":
            path = self._local_path(storage_key)
            if not path.is_file():
                raise ValidationException("材料对象不存在")
            data = await asyncio.to_thread(path.read_bytes)
            content_type = (
                "application/pdf" if path.suffix.lower() == ".pdf" else "image/jpeg"
            )
            return data, content_type
        if self.storage_type != "aliyun_oss":
            if self.storage_type == "disabled":
                raise ThirdPartyException("人社 OSS 未配置，材料功能不可用")
            raise ThirdPartyException("未知的人社材料存储类型")

        def _download() -> tuple[bytes, str]:
            try:
                result = self._oss_bucket().get_object(storage_key)
                data = result.read()
                content_type = (result.headers.get("Content-Type") or "").lower()
                return data, content_type
            except Exception as exc:
                raise ThirdPartyException("阿里云 OSS 读取材料失败") from exc

        return await asyncio.to_thread(_download)

    async def _delete(self, storage_key: str) -> None:
        if self.storage_type == "local":
            path = self._local_path(storage_key)
            if path.is_file():
                await asyncio.to_thread(path.unlink)
            return
        if self.storage_type != "aliyun_oss":
            if self.storage_type == "disabled":
                raise ThirdPartyException("人社 OSS 未配置，材料功能不可用")
            raise ThirdPartyException("未知的人社材料存储类型")

        def _remove() -> None:
            try:
                self._oss_bucket().delete_object(storage_key)
            except Exception as exc:
                raise ThirdPartyException("阿里云 OSS 删除材料失败") from exc

        await asyncio.to_thread(_remove)

    @staticmethod
    def _local_path(storage_key: str) -> Path:
        root = (Path(settings.UPLOAD_DIR).resolve() / "private").resolve()
        target = (root / storage_key).resolve()
        if root not in target.parents:
            raise ValidationException("材料对象键无效")
        return target

    @staticmethod
    def _oss_bucket():
        if not all(
            (
                settings.ALIYUN_OSS_ENDPOINT,
                settings.ALIYUN_OSS_BUCKET,
                settings.ALIYUN_OSS_ACCESS_KEY_ID,
                settings.ALIYUN_OSS_ACCESS_KEY_SECRET,
            )
        ):
            raise ThirdPartyException("阿里云 OSS 配置不完整")
        try:
            import oss2
        except ImportError as exc:
            raise ThirdPartyException("阿里云 OSS SDK 未安装") from exc
        auth = oss2.Auth(
            settings.ALIYUN_OSS_ACCESS_KEY_ID,
            settings.ALIYUN_OSS_ACCESS_KEY_SECRET,
        )
        return oss2.Bucket(
            auth,
            settings.ALIYUN_OSS_ENDPOINT,
            settings.ALIYUN_OSS_BUCKET,
        )
