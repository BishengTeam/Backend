"""B7 视频站站点接口适配层：让视频站前端(learn.ztyyx.cn)直接调 Backend。

字段按视频站前端 `frontend/src/types.ts` 的契约输出，与小程序 C 端接口解耦，
避免改动现有 `/api/courses`（小程序零影响）。
"""

from datetime import datetime, timezone

from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.adapter.security import create_access_token
from app.domain.certification.src.index import (
    Course,
    CourseChapter,
    CourseEnrollment,
    UserChapterNote,
    UserChapterProgress,
)
from app.domain.user.src.index import User
from app.domain.user.src.model.user_profile import UserProfile
from app.port.config import settings
from app.port.exceptions import ForbiddenException, NotFoundException
from app.schemas.course import ChapterProgressUpsert, CourseFilter
from app.services.course import CourseService
from app.services.videoweb import VideoWebService


class VideoWebSiteService:
    _course_service = CourseService()

    @staticmethod
    async def _auth_user(db, user: User) -> dict:
        nickname = await db.scalar(
            select(UserProfile.nickname).where(UserProfile.user_id == user.id)
        )
        return {
            "id": user.id,
            "phone": user.phone or "",
            "miniapp_user_id": user.id,
            "display_name": nickname or (user.phone or f"用户{user.id}"),
            "last_login_at": None,
            "access_disabled": user.videoweb_access_disabled_at is not None,
        }

    @classmethod
    async def login(cls, login_code: str) -> dict:
        user_id = await VideoWebService.exchange_login_code(login_code)
        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None:
                raise NotFoundException("小程序用户")
            token = create_access_token(user.id, user.openid)
            return {
                "token": token,
                "expires_in": settings.JWT_EXPIRE_MINUTES * 60,
                "user": await cls._auth_user(db, user),
            }

    @classmethod
    async def me(cls, user: User) -> dict:
        async with get_db_ctx() as db:
            return await cls._auth_user(db, user)

    @classmethod
    async def my_courses(cls, user_id: int) -> list[dict]:
        async with get_db_ctx() as db:
            rows = (
                await db.execute(
                    select(CourseEnrollment, Course)
                    .join(Course, Course.id == CourseEnrollment.course_id)
                    .where(
                        CourseEnrollment.user_id == user_id,
                        CourseEnrollment.learning_access.is_(True),
                        CourseEnrollment.status.in_(("enrolled", "completed")),
                        Course.deleted_at.is_(None),
                    )
                    .order_by(CourseEnrollment.id.desc())
                )
            ).all()
            items = []
            for enrollment, course in rows:
                chapter_count = int(
                    await db.scalar(
                        select(func.count()).select_from(CourseChapter).where(
                            CourseChapter.course_id == course.id,
                            CourseChapter.deleted_at.is_(None),
                        )
                    )
                    or 0
                )
                completed = int(
                    await db.scalar(
                        select(func.count()).select_from(UserChapterProgress).where(
                            UserChapterProgress.user_id == user_id,
                            UserChapterProgress.course_id == course.id,
                            UserChapterProgress.is_completed.is_(True),
                        )
                    )
                    or 0
                )
                progress = (
                    await db.execute(
                        select(UserChapterProgress, CourseChapter)
                        .join(CourseChapter, CourseChapter.id == UserChapterProgress.chapter_id)
                        .where(
                            UserChapterProgress.user_id == user_id,
                            UserChapterProgress.course_id == course.id,
                        )
                        .order_by(UserChapterProgress.updated_at.desc())
                        .limit(1)
                    )
                ).first()
                continue_point = None
                if progress:
                    row, chapter = progress
                    continue_point = {
                        "resource_type": chapter.resource_type,
                        "last_page": row.last_page or 1,
                        "chapter_id": chapter.id,
                        "title": chapter.title,
                        "position_seconds": row.last_position_seconds or 0,
                    }
                items.append(
                    {
                        "id": course.id,
                        "title": course.title,
                        "category": course.category,
                        "description": course.description,
                        "cover_url": await cls._course_service.storage.signed_url(course.cover_storage_key),
                        "teacher_name": course.teacher_name,
                        "entitlement_id": enrollment.id,
                        "granted_at": enrollment.access_granted_at,
                        "chapter_count": chapter_count,
                        "completed_count": completed,
                        "continue_point": continue_point,
                    }
                )
            return items

    @classmethod
    async def course_detail(cls, user_id: int, course_id: int) -> dict:
        courses = await cls.my_courses(user_id)
        for item in courses:
            if item["id"] == course_id:
                return item
        # 未授权时仍允许看基础信息（视频站课程页会提示未授权）
        async with get_db_ctx() as db:
            course = await db.get(Course, course_id)
            if course is None:
                raise NotFoundException("课程")
            return {
                "id": course.id,
                "title": course.title,
                "category": course.category,
                "description": course.description,
                "cover_url": "",
                "teacher_name": course.teacher_name,
                "entitlement_id": 0,
                "granted_at": None,
            }

    @classmethod
    async def chapters(cls, user_id: int | None, course_id: int) -> dict:
        data = await CourseService().get_chapters(course_id, user_id)
        chapters = [
            {
                "id": chapter.id,
                "title": chapter.title,
                "duration": chapter.duration,
                "resource_type": chapter.resource_type,
                "resource_version": chapter.resource_version,
                "can_play": chapter.can_play,
                "page_count": chapter.page_count,
            }
            for chapter in data.chapters
        ]
        continue_chapter_id = None
        if data.progress and data.progress.last_chapter_id:
            continue_chapter_id = data.progress.last_chapter_id
        return {"chapters": chapters, "continue_chapter_id": continue_chapter_id}

    @classmethod
    async def playback(
        cls, user_id: int, course_id: int, chapter_id: int, *, document: bool
    ) -> dict:
        service = CourseService()
        data = (
            await service.issue_resource(user_id, course_id, chapter_id)
            if document
            else await service.issue_playback(user_id, course_id, chapter_id)
        )
        return data.model_dump()

    @classmethod
    async def save_progress(
        cls,
        user_id: int,
        course_id: int,
        chapter_id: int,
        payload: dict,
    ) -> dict:
        data = ChapterProgressUpsert(
            chapter_id=chapter_id,
            last_position_seconds=int(payload.get("position_seconds") or 0),
            last_page=int(payload.get("last_page") or 1),
            resource_version=int(payload.get("resource_version") or 1),
            is_completed=bool(payload.get("is_completed", False)),
        )
        result = await CourseService().upsert_progress(user_id, course_id, data)
        # ChapterProgressResponse 是"课程级"进度快照（last_chapter_id/last_page/...），
        # 视频站前端按"章节级"契约消费，因此用入参回填 chapter_id / is_completed。
        return {
            "chapter_id": chapter_id,
            "resource_version": result.resource_version,
            "position_seconds": result.last_position_seconds,
            "last_page": result.last_page,
            "is_completed": data.is_completed,
        }

    @classmethod
    async def _note_chapter(cls, db, user_id: int, course_id: int, chapter_id: int):
        """笔记与学习进度同权限口径：课程已发布且该用户持有有效学习授权。"""
        course = await db.get(Course, course_id)
        chapter = await db.get(CourseChapter, chapter_id)
        if (
            course is None
            or chapter is None
            or chapter.course_id != course_id
            or chapter.deleted_at is not None
            or course.deleted_at is not None
        ):
            raise NotFoundException("课程章节")
        if not course.is_active or course.status != "published":
            raise ForbiddenException("课程或章节不可访问")
        if not await CourseService()._has_learning_access(db, user_id, course_id):
            raise ForbiddenException("无课程学习权限")
        return chapter

    @classmethod
    async def get_note(cls, user_id: int, course_id: int, chapter_id: int) -> dict:
        async with get_db_ctx() as db:
            chapter = await cls._note_chapter(db, user_id, course_id, chapter_id)
            row = (
                await db.execute(
                    select(UserChapterNote).where(
                        UserChapterNote.user_id == user_id,
                        UserChapterNote.course_id == course_id,
                        UserChapterNote.chapter_id == chapter_id,
                    )
                )
            ).scalar_one_or_none()
            return {
                "chapter_id": chapter_id,
                "content": row.content if row else "",
                "content_updated": bool(
                    row and row.resource_version != chapter.resource_version
                ),
                "updated_at": row.updated_at if row else datetime.now(timezone.utc),
            }

    @classmethod
    async def save_note(
        cls, user_id: int, course_id: int, chapter_id: int, content: str
    ) -> dict:
        async with get_db_ctx() as db:
            chapter = await cls._note_chapter(db, user_id, course_id, chapter_id)
            row = (
                await db.execute(
                    select(UserChapterNote)
                    .where(
                        UserChapterNote.user_id == user_id,
                        UserChapterNote.course_id == course_id,
                        UserChapterNote.chapter_id == chapter_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                row = UserChapterNote(
                    user_id=user_id,
                    course_id=course_id,
                    chapter_id=chapter_id,
                    content=content,
                )
                db.add(row)
            else:
                row.content = content
            row.resource_version = chapter.resource_version
            await db.commit()
            await db.refresh(row)
            return {
                "chapter_id": chapter_id,
                "content": row.content,
                "content_updated": False,
                "updated_at": row.updated_at,
            }
