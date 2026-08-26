"""Direct-to-OSS uploads for quiz stem and option images."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from app.port.config import settings
from app.port.exceptions import ThirdPartyException, ValidationException
from app.schemas.admin_quiz_contract import (
    AdminQuizImageUploadCreate,
    AdminQuizImageUploadResponse,
)
from app.services.course_storage import CourseStorage


QUIZ_IMAGE_MAX_BYTES = 10 * 1024 * 1024
QUIZ_IMAGE_SIGN_TTL_SECONDS = 3600
QUIZ_IMAGE_READ_TTL_SECONDS = 7 * 24 * 3600
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_IMAGE_CONTENT_TYPES = {
    "",
    "application/octet-stream",
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
}


@dataclass(frozen=True, slots=True)
class QuizImageTarget:
    object_key: str
    upload_url: str
    public_url: str
    expires_at: datetime


def _public_base_url() -> str:
    endpoint = settings.ALIYUN_OSS_ENDPOINT.strip()
    bucket = settings.ALIYUN_OSS_BUCKET.strip()
    if not endpoint or not bucket:
        raise ThirdPartyException("题库图片 OSS 配置不完整")
    host = urlparse(endpoint if "://" in endpoint else f"https://{endpoint}").netloc
    if not host:
        raise ThirdPartyException("题库图片 OSS Endpoint 无效")
    return f"https://{bucket}.{host}"


def _quiz_base_or_none() -> str | None:
    try:
        return _public_base_url()
    except ThirdPartyException:
        return None


def extract_quiz_image_key(value: str) -> str | None:
    """Extract the quiz-images object key from a clean/signed URL or bare key."""

    if not value:
        return None
    if value.startswith("quiz-images/"):
        return value.split("?", 1)[0]
    base = _quiz_base_or_none()
    if base and value.startswith(f"{base}/"):
        key = value[len(base) + 1 :].split("?", 1)[0]
        if key.startswith("quiz-images/"):
            return key
    return None


def sign_quiz_media_urls(values: list[str]) -> list[str]:
    """Return display URLs, re-signing quiz OSS links like course covers."""

    if not values or _quiz_base_or_none() is None:
        return list(values)
    keys = [extract_quiz_image_key(value) for value in values]
    if not any(keys):
        return list(values)

    def _sign_all() -> list[str]:
        bucket = CourseStorage._bucket()
        return [
            (
                bucket.sign_url(
                    "GET", key, QUIZ_IMAGE_READ_TTL_SECONDS, slash_safe=True
                )
                if key
                else value
            )
            for key, value in zip(keys, values)
        ]

    return _sign_all()


def sign_quiz_media_map(mapping: dict[str, str] | None) -> dict[str, str]:
    if not mapping:
        return {}
    keys = list(mapping.keys())
    values = sign_quiz_media_urls([mapping[key] for key in keys])
    return dict(zip(keys, values))


class QuizImageUploadService:
    """Issues presigned PUT URLs for immutable public quiz images."""

    @staticmethod
    def validate(data: AdminQuizImageUploadCreate) -> str:
        extension = Path(data.filename or "").suffix.lower()
        if extension not in _IMAGE_EXTENSIONS:
            raise ValidationException("题库图片仅支持 JPG、PNG、WebP 或 GIF")
        if data.size_bytes <= 0 or data.size_bytes > QUIZ_IMAGE_MAX_BYTES:
            raise ValidationException("题库图片大小必须在 10MB 以内")
        normalized_type = (data.content_type or "").split(";", 1)[0].strip().lower()
        if normalized_type not in _IMAGE_CONTENT_TYPES:
            raise ValidationException("题库图片类型必须是常见图片 MIME")
        return extension

    @classmethod
    async def create(
        cls, data: AdminQuizImageUploadCreate
    ) -> AdminQuizImageUploadResponse:
        extension = cls.validate(data)
        installation = await CourseStorage.installation_id()
        object_key = f"quiz-images/{installation}/{uuid.uuid4().hex}{extension}"

        def _sign() -> str:
            return CourseStorage._bucket().sign_url(
                "PUT", object_key, QUIZ_IMAGE_SIGN_TTL_SECONDS
            )

        upload_url = await asyncio.to_thread(_sign)
        public_url = sign_quiz_media_urls([f"{_public_base_url()}/{object_key}"])[0]
        now = datetime.now(timezone.utc)
        return AdminQuizImageUploadResponse(
            object_key=object_key,
            upload_url=upload_url,
            public_url=public_url,
            expires_at=now + timedelta(seconds=QUIZ_IMAGE_SIGN_TTL_SECONDS),
        )
