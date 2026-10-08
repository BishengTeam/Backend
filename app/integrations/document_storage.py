"""Private OSS boundary for admin-managed operational PDF documents."""

from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
from pathlib import Path
from urllib.parse import quote

from app.port.config import settings
from app.port.exceptions import ThirdPartyException, ValidationException


DOCUMENT_MAX_BYTES = 20 * 1024 * 1024
DOCUMENT_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){1,7}$")


def validate_document_key(document_key: str) -> str:
    normalized = document_key.strip().lower()
    if len(normalized) > 128 or not DOCUMENT_KEY_PATTERN.fullmatch(normalized):
        raise ValidationException(
            "文档键名仅支持小写字母、数字、下划线和点，且以字母开头"
        )
    return normalized


class DocumentObjectStorage:
    """Validate and persist immutable PDF objects; DB points at the current one."""

    def __init__(self) -> None:
        self.storage_type = settings.RENSHE_STORAGE_TYPE

    async def save(
        self,
        *,
        document_key: str,
        filename: str,
        content_type: str | None,
        data: bytes,
        max_bytes: int = DOCUMENT_MAX_BYTES,
    ) -> tuple[str, int, str]:
        key = validate_document_key(document_key)
        normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
        extension = Path(filename or "").suffix.lower()
        if extension != ".pdf" or normalized_type != "application/pdf":
            raise ValidationException("运营文档仅支持 PDF 文件")
        if not data or len(data) > max_bytes:
            raise ValidationException(f"PDF 文件必须为 1 字节至 {max_bytes // 1024 // 1024}MB")
        if data[:5] != b"%PDF-":
            raise ValidationException("PDF 文件内容无效")

        digest = hashlib.sha256(data).hexdigest()
        storage_key = f"documents/{key.replace('.', '/')}/{digest}.pdf"
        await self._put(storage_key, data, "application/pdf")
        return storage_key, len(data), digest

    async def signed_get_url(
        self, storage_key: str, *, filename: str = "document.pdf"
    ) -> str:
        if self.storage_type != "aliyun_oss":
            raise ThirdPartyException("运营文档 OSS 未配置")

        disposition = (
            f"inline; filename=document.pdf; filename*=UTF-8''{quote(filename)}"
        )

        def _sign() -> str:
            return self._bucket().sign_url(
                "GET",
                storage_key,
                settings.ALIYUN_OSS_SIGNED_URL_TTL_SECONDS,
                params={"response-content-disposition": disposition},
            )

        return await asyncio.to_thread(_sign)

    async def _put(self, storage_key: str, data: bytes, content_type: str) -> None:
        if self.storage_type == "local":
            path = self._local_path(storage_key)
            path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(path.write_bytes, data)
            return
        if self.storage_type != "aliyun_oss":
            raise ThirdPartyException("运营文档 OSS 未配置")

        def _upload() -> None:
            import oss2

            result = self._bucket().put_object(
                storage_key,
                data,
                headers={"Content-Type": content_type},
            )
            if result.status // 100 != 2:
                raise ThirdPartyException("运营文档上传失败")

        await asyncio.to_thread(_upload)

    async def download_file(self, storage_key: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if self.storage_type == "local":
            source = self._local_path(storage_key)
            if not source.is_file():
                raise ValidationException("运营文档不存在")
            await asyncio.to_thread(shutil.copyfile, source, destination)
            return
        if self.storage_type != "aliyun_oss":
            raise ThirdPartyException("运营文档 OSS 未配置")

        def _download() -> None:
            self._bucket().get_object_to_file(storage_key, str(destination))

        await asyncio.to_thread(_download)

    @staticmethod
    def _local_path(storage_key: str) -> Path:
        root = (Path(settings.UPLOAD_DIR).resolve() / "private").resolve()
        target = (root / storage_key).resolve()
        if root not in target.parents:
            raise ValidationException("运营文档对象键无效")
        return target

    @staticmethod
    def _bucket():
        import oss2

        if not all(
            (
                settings.ALIYUN_OSS_ENDPOINT,
                settings.ALIYUN_OSS_BUCKET,
                settings.ALIYUN_OSS_ACCESS_KEY_ID,
                settings.ALIYUN_OSS_ACCESS_KEY_SECRET,
            )
        ):
            raise ThirdPartyException("运营文档 OSS 配置不完整")
        auth = oss2.Auth(
            settings.ALIYUN_OSS_ACCESS_KEY_ID,
            settings.ALIYUN_OSS_ACCESS_KEY_SECRET,
        )
        return oss2.Bucket(
            auth,
            settings.ALIYUN_OSS_ENDPOINT,
            settings.ALIYUN_OSS_BUCKET,
        )
