from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.adapter.database import get_db_ctx
from app.domain.certification.src.index import (
    Course,
    CourseAuditLog,
    CourseChapter,
    CourseUpload,
)
from app.port.exceptions import BusinessException, ForbiddenException, NotFoundException, ValidationException
from app.schemas.course_upload import CourseUploadCreate
from app.services.course_storage import COURSE_DEFAULT_PART_SIZE, CourseStorage


PENDING_UPLOAD_RETENTION = timedelta(days=7)
COMPLETED_UPLOAD_RETENTION = timedelta(hours=24)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _response(upload: CourseUpload, *, upload_url: str | None = None, parts=None):
    from app.schemas.course_upload import CourseUploadResponse

    return CourseUploadResponse(
        id=upload.id,
        course_id=upload.course_id,
        kind=upload.kind,
        filename=upload.original_filename,
        content_type=upload.content_type,
        size_bytes=upload.size_bytes,
        part_size=upload.part_size,
        status=upload.status,
        title=upload.title,
        duration=upload.duration,
        sort_order=upload.sort_order,
        object_key=upload.object_key,
        oss_upload_id=upload.oss_upload_id,
        upload_url=upload_url,
        expires_at=upload.expires_at,
        completed_at=upload.completed_at,
        parts=parts or [],
    )


class CourseUploadService:
    def __init__(self, storage: CourseStorage | None = None) -> None:
        self.storage = storage or CourseStorage()

    async def create(
        self,
        data: CourseUploadCreate,
        *,
        admin_id: int,
    ):
        if data.kind == "cover":
            if data.course_id is not None or any(
                (data.title, data.duration, data.sort_order)
            ):
                raise ValidationException("封面上传不能携带章节字段")
        else:
            if (
                data.course_id is None
                or data.title is None
                or data.duration is None
                or data.sort_order is None
            ):
                raise ValidationException("章节视频必须绑定课程并填写标题、时长和排序")

        if data.course_id is not None:
            async with get_db_ctx() as db:
                course = await db.get(Course, data.course_id)
                if course is None:
                    raise NotFoundException("课程")
                if course.status == "archived":
                    raise BusinessException("归档课程不能上传章节视频")

        target = await self.storage.initiate(
            kind=data.kind,
            filename=data.filename,
            content_type=data.content_type,
            size_bytes=data.size_bytes,
        )
        cover_upload_url = (
            await self.storage.put_url(target.object_key)
            if data.kind == "cover"
            else None
        )
        try:
            async with get_db_ctx() as db:
                async with db.begin():
                    row = CourseUpload(
                        course_id=data.course_id,
                        kind=data.kind,
                        object_key=target.object_key,
                        oss_upload_id=target.upload_id,
                        original_filename=data.filename,
                        content_type=data.content_type,
                        size_bytes=data.size_bytes,
                        part_size=COURSE_DEFAULT_PART_SIZE,
                        status="pending",
                        title=data.title,
                        duration=data.duration,
                        sort_order=data.sort_order,
                        expires_at=_now() + PENDING_UPLOAD_RETENTION,
                    )
                    db.add(row)
                    await db.flush()
                    db.add(
                        CourseAuditLog(
                            actor_type="admin",
                            actor_id=admin_id,
                            action="course.upload.created",
                            object_type="course_upload",
                            object_id=row.id,
                            result="succeeded",
                            summary={"kind": row.kind, "size": row.size_bytes},
                        )
                    )
                    result = _response(row, upload_url=cover_upload_url)
                    return result
        except Exception:
            try:
                await self.storage.abort(target.object_key, target.upload_id)
            except Exception:
                pass
            raise

    async def get(self, upload_id: int) -> CourseUpload:
        async with get_db_ctx() as db:
            row = await db.get(CourseUpload, upload_id)
            if row is None:
                raise NotFoundException("课程上传任务")
            if row.status == "pending" and row.expires_at < _now():
                row.status = "expired"
                await db.commit()
            return row

    async def detail(self, upload_id: int):
        row = await self.get(upload_id)
        parts = (
            await self.storage.list_parts(row.object_key, row.oss_upload_id)
            if row.status == "pending"
            else []
        )
        return _response(row, parts=parts)

    async def part_url(self, upload_id: int, part_number: int):
        from app.schemas.course_upload import CourseUploadPartUrlResponse

        row = await self.get(upload_id)
        if row.status != "pending":
            raise BusinessException("上传任务已完成或已失效")
        if row.expires_at < _now():
            raise BusinessException("上传任务已过期")
        max_part = math.ceil(row.size_bytes / row.part_size)
        if part_number > max_part:
            raise ValidationException("上传分片编号超出范围")
        url = await self.storage.part_url(
            row.object_key, row.oss_upload_id, part_number
        )
        return CourseUploadPartUrlResponse(
            upload_id=row.id,
            part_number=part_number,
            url=url,
            expires_at=int(_now().timestamp()) + 3600,
        )

    async def complete(self, upload_id: int, *, admin_id: int):
        row = await self.get(upload_id)
        if row.status != "pending":
            raise BusinessException("上传任务已完成或已失效")
        uploaded = await self.storage.complete(
            row.object_key, row.oss_upload_id, row.size_bytes
        )
        async with get_db_ctx() as db:
            async with db.begin():
                current = await db.get(CourseUpload, upload_id)
                if current is None or current.status != "pending":
                    raise BusinessException("上传任务状态已变化")
                current.status = "completed"
                current.completed_at = _now()
                current.expires_at = _now() + COMPLETED_UPLOAD_RETENTION
                if uploaded.content_type and uploaded.content_type not in {
                    "application/octet-stream"
                }:
                    current.content_type = uploaded.content_type
                db.add(
                    CourseAuditLog(
                        actor_type="admin",
                        actor_id=admin_id,
                        action="course.upload.completed",
                        object_type="course_upload",
                        object_id=current.id,
                        result="succeeded",
                        summary={"size": current.size_bytes},
                    )
                )
        return await self.detail(upload_id)

    async def abort(self, upload_id: int, *, admin_id: int) -> None:
        row = await self.get(upload_id)
        if row.status not in {"pending", "completed"}:
            return
        if row.status == "pending":
            await self.storage.abort(row.object_key, row.oss_upload_id)
        else:
            await self.storage.delete(row.object_key)
        async with get_db_ctx() as db:
            current = await db.get(CourseUpload, upload_id)
            if current is not None and current.status in {"pending", "completed"}:
                current.status = "aborted"
                db.add(
                    CourseAuditLog(
                        actor_type="admin",
                        actor_id=admin_id,
                        action="course.upload.aborted",
                        object_type="course_upload",
                        object_id=current.id,
                        result="succeeded",
                    )
                )
                await db.commit()

    async def list_course_uploads(self, course_id: int):
        async with get_db_ctx() as db:
            rows = (
                await db.execute(
                    select(CourseUpload)
                    .where(
                        CourseUpload.course_id == course_id,
                        CourseUpload.status.in_(("pending", "completed")),
                    )
                    .order_by(CourseUpload.sort_order.nulls_first(), CourseUpload.id)
                )
            ).scalars().all()
            return [_response(row) for row in rows]

    async def cleanup_expired(self, *, batch_size: int = 100) -> int:
        now = _now()
        async with get_db_ctx() as db:
            rows = (
                await db.execute(
                    select(CourseUpload)
                    .where(
                        CourseUpload.status.in_(("pending", "completed")),
                        CourseUpload.expires_at < now,
                    )
                    .order_by(CourseUpload.expires_at)
                    .limit(batch_size)
                )
            ).scalars().all()
        cleaned = 0
        for row in rows:
            try:
                if row.status == "pending":
                    await self.storage.abort(row.object_key, row.oss_upload_id)
                else:
                    await self.storage.delete(row.object_key)
            except Exception:
                continue
            async with get_db_ctx() as db:
                current = await db.get(CourseUpload, row.id)
                if current is not None and current.status in {"pending", "completed"}:
                    current.status = "expired"
                    await db.commit()
                    cleaned += 1
        return cleaned

    async def bind_cover(self, upload_id: int, course: Course) -> None:
        row = await self.get(upload_id)
        if row.kind != "cover" or row.status != "completed":
            raise ValidationException("课程封面尚未上传完成")
        async with get_db_ctx() as db:
            current = await db.get(CourseUpload, upload_id)
            target = await db.get(Course, course.id)
            if current is None or target is None:
                raise NotFoundException("课程封面上传")
            old_key = target.cover_storage_key
            target.cover_storage_key = current.object_key
            current.status = "bound"
            current.course_id = target.id
            current.expires_at = _now()
            await db.flush()
            db.add(
                CourseAuditLog(
                    actor_type="system",
                    action="course.cover.bound",
                    object_type="course",
                    object_id=target.id,
                    result="succeeded",
                    summary={"replaced": bool(old_key)},
                )
            )
        await db.commit()
        if old_key and old_key != row.object_key:
            await self.storage.delete(old_key)

    async def create_chapters(
        self,
        course_id: int,
        upload_ids: list[int],
        *,
        admin_id: int,
    ) -> list[CourseChapter]:
        async with get_db_ctx() as db:
            async with db.begin():
                course = await db.get(Course, course_id)
                if course is None:
                    raise NotFoundException("课程")
                if course.status == "archived":
                    raise BusinessException("归档课程不能新增章节")
                rows = (
                    (
                        await db.execute(
                            select(CourseUpload).where(
                                CourseUpload.id.in_(upload_ids),
                                CourseUpload.course_id == course_id,
                                CourseUpload.kind == "chapter_video",
                                CourseUpload.status == "completed",
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                if len(rows) != len(set(upload_ids)):
                    raise ValidationException("存在未完成或不可用的章节视频")
                existing_count = int(
                    await db.scalar(select(CourseChapter.id).where(CourseChapter.course_id == course_id).limit(1))
                    is not None
                )
                base_sort = 0 if not existing_count else int(
                    await db.scalar(
                        select(CourseChapter.sort_order)
                        .where(CourseChapter.course_id == course_id)
                        .order_by(CourseChapter.sort_order.desc())
                        .limit(1)
                    )
                )
                created = []
                for index, upload in enumerate(sorted(rows, key=lambda item: item.sort_order or 0), start=1):
                    chapter = CourseChapter(
                        course_id=course_id,
                        title=upload.title or upload.original_filename,
                        video_storage_key=upload.object_key,
                        original_filename=upload.original_filename,
                        content_type=upload.content_type,
                        size_bytes=upload.size_bytes,
                        duration=upload.duration or 1,
                        sort_order=base_sort + index,
                    )
                    db.add(chapter)
                    upload.status = "bound"
                    upload.expires_at = _now()
                    created.append(chapter)
                await db.flush()
                db.add(
                    CourseAuditLog(
                        actor_type="admin",
                        actor_id=admin_id,
                        action="course.chapters.batch_created",
                        object_type="course",
                        object_id=course_id,
                        result="succeeded",
                        summary={"count": len(created)},
                    )
                )
                return created

    async def replace_video(
        self, course_id: int, chapter_id: int, upload_id: int, *, admin_id: int
    ) -> CourseChapter:
        row = await self.get(upload_id)
        if row.kind != "chapter_video" or row.course_id != course_id or row.status != "completed":
            raise ValidationException("替换视频尚未上传完成或不属于该课程")
        async with get_db_ctx() as db:
            async with db.begin():
                chapter = await db.get(CourseChapter, chapter_id)
                upload = await db.get(CourseUpload, upload_id)
                if chapter is None or chapter.course_id != course_id:
                    raise NotFoundException("课程章节")
                if upload is None or upload.status != "completed":
                    raise ValidationException("替换视频尚未上传完成")
                old_key = chapter.video_storage_key
                chapter.video_storage_key = upload.object_key
                chapter.original_filename = upload.original_filename
                chapter.content_type = upload.content_type
                chapter.size_bytes = upload.size_bytes
                if upload.duration:
                    chapter.duration = upload.duration
                upload.status = "bound"
                upload.expires_at = _now()
                db.add(
                    CourseAuditLog(
                        actor_type="admin",
                        actor_id=admin_id,
                        action="course.chapter.video_replaced",
                        object_type="course_chapter",
                        object_id=chapter.id,
                        result="succeeded",
                        summary={"course_id": course_id},
                    )
                )
                if old_key != upload.object_key:
                    pass
            if old_key != upload.object_key:
                await self.storage.delete(old_key)
            return chapter
