import mimetypes
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from app.adapter.database import get_db_ctx
from app.domain.certification.src.index import Course, CourseAsset, CourseEnrollment
from app.port.config import settings
from app.port.exceptions import ForbiddenException, NotFoundException


PRIVATE_COURSE_ASSET_ROOT = Path(settings.UPLOAD_DIR) / "private" / "course-assets"


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
    async def get_content(self, user_id: int, asset_id: int) -> CourseAssetFile:
        async with get_db_ctx() as db:
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
