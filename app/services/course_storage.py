from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from sqlalchemy import select

from app.adapter.database import get_db_ctx
from app.models.deployment_acceptance import DeploymentAcceptance
from app.port.config import settings
from app.port.exceptions import ThirdPartyException, ValidationException


CourseUploadKind = Literal["cover", "chapter_video"]

COURSE_COVER_MAX_BYTES = 10 * 1024 * 1024
COURSE_VIDEO_MAX_BYTES = 5 * 1024 * 1024 * 1024
COURSE_UPLOAD_SIGN_TTL_SECONDS = 3600
COURSE_PLAYBACK_TTL_SECONDS = 300
COURSE_DEFAULT_PART_SIZE = 16 * 1024 * 1024

_COVER_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv"}
_SAFE_INSTALLATION_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True, slots=True)
class CourseUploadObject:
    object_key: str
    upload_id: str


@dataclass(frozen=True, slots=True)
class CourseUploadedObject:
    object_key: str
    size_bytes: int
    content_type: str | None


def validate_upload(
    *,
    kind: CourseUploadKind,
    filename: str,
    content_type: str,
    size_bytes: int,
) -> str:
    extension = Path(filename or "").suffix.lower()
    normalized_type = content_type.split(";", 1)[0].strip().lower()
    if kind == "cover":
        if size_bytes <= 0 or size_bytes > COURSE_COVER_MAX_BYTES:
            raise ValidationException("课程封面大小必须在 10MB 以内")
        if extension not in _COVER_EXTENSIONS:
            raise ValidationException("课程封面仅支持 JPG、PNG 或 WebP")
        allowed_types = {"", "application/octet-stream"} | {
            "image/jpeg",
            "image/png",
            "image/webp",
        }
        if normalized_type not in allowed_types:
            raise ValidationException("课程封面文件类型无效")
        return extension

    if size_bytes <= 0 or size_bytes > COURSE_VIDEO_MAX_BYTES:
        raise ValidationException("章节视频大小必须在 5GB 以内")
    if extension not in _VIDEO_EXTENSIONS:
        raise ValidationException("章节视频仅支持 MP4、MOV 或 MKV")
    return extension


class CourseStorage:
    """Private OSS facade for course covers and chapter videos."""

    @staticmethod
    async def installation_id() -> str:
        configured = settings.COURSE_OSS_INSTALLATION_ID.strip()
        if configured:
            if not _SAFE_INSTALLATION_ID.fullmatch(configured):
                raise ValidationException("课程 OSS 安装标识无效")
            return configured
        async with get_db_ctx() as db:
            value = await db.scalar(
                select(DeploymentAcceptance.installation_id)
                .order_by(DeploymentAcceptance.installation_id.desc())
                .limit(1)
            )
        return value or "default"

    @staticmethod
    def _bucket():
        import oss2

        if settings.RENSHE_STORAGE_TYPE != "aliyun_oss":
            raise ThirdPartyException("课程 OSS 未配置")
        missing = [
            name
            for name in (
                settings.ALIYUN_OSS_ENDPOINT,
                settings.ALIYUN_OSS_BUCKET,
                settings.ALIYUN_OSS_ACCESS_KEY_ID,
                settings.ALIYUN_OSS_ACCESS_KEY_SECRET,
            )
            if not name
        ]
        if missing:
            raise ThirdPartyException("课程 OSS 配置不完整")
        return oss2.Bucket(
            oss2.Auth(
                settings.ALIYUN_OSS_ACCESS_KEY_ID,
                settings.ALIYUN_OSS_ACCESS_KEY_SECRET,
            ),
            settings.ALIYUN_OSS_ENDPOINT,
            settings.ALIYUN_OSS_BUCKET,
        )

    @classmethod
    async def initiate(
        cls,
        *,
        kind: CourseUploadKind,
        filename: str,
        content_type: str,
        size_bytes: int,
    ) -> CourseUploadObject:
        extension = validate_upload(
            kind=kind,
            filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
        )
        import uuid

        installation = await cls.installation_id()
        folder = "covers" if kind == "cover" else "chapters"
        object_key = (
            f"course/{installation}/{folder}/{uuid.uuid4().hex}{extension}"
        )

        def _init() -> str:
            bucket = cls._bucket()
            if kind == "cover":
                return ""
            return bucket.init_multipart_upload(object_key).upload_id

        upload_id = await asyncio.to_thread(_init)
        return CourseUploadObject(object_key=object_key, upload_id=upload_id)

    @classmethod
    async def part_url(
        cls, object_key: str, oss_upload_id: str, part_number: int
    ) -> str:
        def _sign() -> str:
            return cls._bucket().sign_url(
                "PUT",
                object_key,
                COURSE_UPLOAD_SIGN_TTL_SECONDS,
                params={
                    "partNumber": str(part_number),
                    "uploadId": str(oss_upload_id),
                },
            )

        return await asyncio.to_thread(_sign)

    @classmethod
    async def put_url(cls, object_key: str) -> str:
        def _sign() -> str:
            return cls._bucket().sign_url(
                "PUT", object_key, COURSE_UPLOAD_SIGN_TTL_SECONDS
            )

        return await asyncio.to_thread(_sign)

    @classmethod
    async def list_parts(cls, object_key: str, oss_upload_id: str) -> list[dict]:
        if not oss_upload_id:
            return []
        def _list() -> list[dict]:
            parts = []
            for part in cls._bucket().list_parts(object_key, oss_upload_id):
                parts.append(
                    {
                        "part_number": int(part.part_number),
                        "size_bytes": int(part.size),
                        "etag": str(part.etag).strip('"'),
                    }
                )
            return parts

        return await asyncio.to_thread(_list)

    @classmethod
    async def complete(
        cls, object_key: str, oss_upload_id: str, expected_size: int
    ) -> CourseUploadedObject:
        from oss2.models import PartInfo

        def _complete() -> CourseUploadedObject:
            bucket = cls._bucket()
            if not oss_upload_id:
                head = bucket.head_object(object_key)
                if int(head.content_length) != expected_size:
                    raise ValidationException("OSS 封面对象校验失败")
                return CourseUploadedObject(
                    object_key=object_key,
                    size_bytes=int(head.content_length),
                    content_type=head.headers.get("Content-Type"),
                )
            raw_parts = list(bucket.list_parts(object_key, oss_upload_id))
            uploaded = sum(int(part.size) for part in raw_parts)
            if uploaded != expected_size:
                raise ValidationException("视频分片大小与声明不一致")
            parts = [
                PartInfo(int(part.part_number), str(part.etag))
                for part in raw_parts
            ]
            bucket.complete_multipart_upload(object_key, oss_upload_id, parts)
            head = bucket.head_object(object_key)
            if int(head.content_length) != expected_size:
                raise ValidationException("OSS 视频对象校验失败")
            return CourseUploadedObject(
                object_key=object_key,
                size_bytes=int(head.content_length),
                content_type=head.headers.get("Content-Type"),
            )

        return await asyncio.to_thread(_complete)

    @classmethod
    async def abort(cls, object_key: str, oss_upload_id: str) -> None:
        def _abort() -> None:
            bucket = cls._bucket()
            if oss_upload_id:
                bucket.abort_multipart_upload(object_key, oss_upload_id)
            else:
                bucket.delete_object(object_key)

        await asyncio.to_thread(_abort)

    @classmethod
    async def delete(cls, object_key: str) -> None:
        def _delete() -> None:
            cls._bucket().delete_object(object_key)

        await asyncio.to_thread(_delete)

    @classmethod
    async def signed_url(
        cls,
        object_key: str,
        *,
        download_filename: str | None = None,
        ttl_seconds: int = COURSE_PLAYBACK_TTL_SECONDS,
    ) -> str:
        def _sign() -> str:
            params = None
            if download_filename:
                encoded = quote(download_filename, safe="")
                params = {
                    "response-content-disposition": (
                        f"inline; filename*=UTF-8''{encoded}"
                    )
                }
            return cls._bucket().sign_url(
                "GET", object_key, ttl_seconds, params=params
            )

        return await asyncio.to_thread(_sign)
