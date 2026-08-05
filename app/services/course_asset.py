import hashlib
import hmac
import mimetypes
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from app.adapter.database import get_db_ctx
from app.domain.certification.src.index import Course, CourseAsset, CourseEnrollment
from app.domain.user.src.index import User
from app.port.config import settings
from app.port.exceptions import ForbiddenException, NotFoundException


PRIVATE_COURSE_ASSET_ROOT = Path(settings.UPLOAD_DIR) / "private" / "course-assets"
PLAYBACK_URL_TTL_SECONDS = 2 * 60 * 60


@dataclass(slots=True)
class CourseAssetFile:
    path: Path
    media_type: str | None
    filename: str
    inline: bool


class CourseAssetStorage:
    @staticmethod
    def resolve(storage_key: str) -> Path:
        root = PRIVATE_COURSE_ASSET_ROOT.resolve()
        key = Path(storage_key)
        if key.is_absolute() or ".." in key.parts:
            raise NotFoundException("课程资源")
        path = (root / key).resolve()
        if path == root or root not in path.parents:
            raise NotFoundException("课程资源")
        return path

    @classmethod
    def save(cls, course_id: int, filename: str, content: bytes) -> tuple[str, int]:
        _, extension = os.path.splitext(filename or "asset")
        extension = extension.lower()
        if len(extension) > 16 or not re.fullmatch(r"\.[a-z0-9]+", extension):
            extension = ""
        storage_key = f"{course_id}/{uuid.uuid4().hex}{extension.lower()}"
        path = cls.resolve(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return storage_key, len(content)

    @classmethod
    def delete(cls, storage_key: str) -> None:
        path = cls.resolve(storage_key)
        path.unlink(missing_ok=True)


class CourseAssetService:
    @staticmethod
    def _signature_key() -> bytes:
        return hmac.new(
            settings.JWT_SECRET.encode("utf-8"),
            b"course-asset-playback-v1",
            hashlib.sha256,
        ).digest()

    @classmethod
    def create_playback_signature(
        cls,
        user_id: int,
        asset_id: int,
        expires_at: int,
    ) -> str:
        payload = f"{user_id}:{asset_id}:{expires_at}".encode("ascii")
        return hmac.new(cls._signature_key(), payload, hashlib.sha256).hexdigest()

    @classmethod
    def verify_playback_signature(
        cls,
        user_id: int,
        asset_id: int,
        expires_at: int,
        signature: str,
        *,
        now: int | None = None,
    ) -> None:
        current_time = int(time.time()) if now is None else now
        if expires_at <= current_time:
            raise ForbiddenException("播放地址已过期")
        expected = cls.create_playback_signature(user_id, asset_id, expires_at)
        if not hmac.compare_digest(signature, expected):
            raise ForbiddenException("播放地址签名无效")

    async def issue_playback_url(
        self,
        user_id: int,
        asset_id: int,
    ) -> tuple[str, int]:
        # 签发前先执行与真实资源访问相同的权限和文件检查。
        await self.get_content(user_id, asset_id)
        expires_at = int(time.time()) + PLAYBACK_URL_TTL_SECONDS
        signature = self.create_playback_signature(user_id, asset_id, expires_at)
        url = (
            f"/api/course-assets/{asset_id}/content"
            f"?user_id={user_id}&expires={expires_at}&signature={signature}"
        )
        return url, expires_at

    async def get_content(self, user_id: int, asset_id: int) -> CourseAssetFile:
        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None or not user.is_active:
                raise ForbiddenException("用户账号不可用")
            asset = await db.get(CourseAsset, asset_id)
            if asset is None:
                raise NotFoundException("课程资源")
            course = await db.get(Course, asset.course_id)
            if course is None:
                raise NotFoundException("课程")

            has_access = (
                await db.execute(
                    select(CourseEnrollment.id).where(
                        CourseEnrollment.user_id == user_id,
                        CourseEnrollment.course_id == asset.course_id,
                        CourseEnrollment.learning_access.is_(True),
                        CourseEnrollment.status.in_(("enrolled", "completed")),
                    )
                )
            ).scalar_one_or_none() is not None
            preview_allowed = asset.is_preview and course.is_active
            if not has_access and not preview_allowed:
                raise ForbiddenException("无课程学习权限")

            path = CourseAssetStorage.resolve(asset.storage_key)
            if not path.is_file():
                raise NotFoundException("课程资源文件")
            media_type = mimetypes.guess_type(path.name)[0]
            inline = bool(
                media_type == "application/pdf"
                or media_type and media_type.startswith(("video/", "audio/"))
                or media_type
                and media_type.startswith("image/")
                and media_type != "image/svg+xml"
            )
            filename = re.sub(r'[\\/\r\n"]', "_", asset.title).strip() or path.name
            if not Path(filename).suffix and path.suffix:
                filename = f"{filename}{path.suffix}"
            return CourseAssetFile(
                path=path,
                media_type=media_type if inline else "application/octet-stream",
                filename=filename,
                inline=inline,
            )
