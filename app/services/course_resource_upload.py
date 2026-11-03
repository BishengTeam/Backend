"""Atomic chapter binding and replacement for resumable mixed resource uploads."""
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.domain.certification.src.index import Course, CourseChapter, CourseUpload, CourseAuditLog
from app.port.exceptions import BusinessException, ConflictException, NotFoundException, ValidationException
from app.services.course_storage import COURSE_DEFAULT_PART_SIZE


class CourseResourceUploadMixin:
    async def create_resource_upload(self, data, *, admin_id):
        from app.services.course_upload import _now, _response
        if data.course_id is None:
            raise ValidationException("请选择所属课程")
        if bool(data.chapter_id) != bool(data.expected_resource_version):
            raise ValidationException("替换章节必须提供当前资源修订号")
        target = await self.storage.initiate(kind=data.kind, filename=data.filename, content_type=data.content_type, size_bytes=data.size_bytes)
        try:
            async with get_db_ctx() as db, db.begin():
                course = await db.scalar(select(Course).where(Course.id == data.course_id).with_for_update())
                if not course or course.status == "archived":
                    raise BusinessException("课程不存在或已归档")
                if data.chapter_id:
                    chapter = await db.get(CourseChapter, data.chapter_id)
                    if not chapter or chapter.course_id != course.id:
                        raise NotFoundException("课程章节")
                    if chapter.resource_version != data.expected_resource_version:
                        raise ConflictException("章节内容已变化，请刷新后替换")
                    order = chapter.sort_order
                else:
                    # Reserve order before uploading, not in completion order.
                    bound = await db.scalar(select(func.max(CourseChapter.sort_order)).where(CourseChapter.course_id == course.id)) or 0
                    pending = await db.scalar(select(func.max(CourseUpload.sort_order)).where(CourseUpload.course_id == course.id,
                        CourseUpload.status.in_(("pending", "validating", "completed", "failed")))) or 0
                    order = max(bound, pending) + 1
                row = CourseUpload(course_id=course.id, kind="chapter_file", object_key=target.object_key, oss_upload_id=target.upload_id,
                    original_filename=data.filename, content_type=data.content_type, size_bytes=data.size_bytes, part_size=COURSE_DEFAULT_PART_SIZE,
                    title=(data.title or Path(data.filename).stem).strip()[:256] or "未命名章节", sort_order=order,
                    chapter_id=data.chapter_id, expected_resource_version=data.expected_resource_version, status="pending", expires_at=_now() + timedelta(days=7))
                db.add(row)
                await db.flush()
                return _response(row)
        except Exception:
            await self.storage.abort(target.object_key, target.upload_id)
            raise

    async def complete_resource_upload(self, upload_id, *, admin_id):
        # The transaction lease is released on worker death, permitting safe
        # recovery of a persisted validating status after a service restart.
        async with get_db_ctx() as lease, lease.begin():
            recover = lease.get_bind().dialect.name == "postgresql"
            if recover and not await lease.scalar(select(func.pg_try_advisory_xact_lock(32601, upload_id))):
                raise ConflictException("文件正在验证，请刷新查看进度")
            return await self._complete_resource_upload(upload_id, admin_id=admin_id, recover=recover)

    async def _complete_resource_upload(self, upload_id, *, admin_id, recover=False):
        from app.services.course_upload import _now
        async with get_db_ctx() as db, db.begin():
            row = await db.scalar(select(CourseUpload).where(CourseUpload.id == upload_id).with_for_update())
            if not row:
                raise NotFoundException("上传任务")
            if row.status == "bound":
                return await self.detail(upload_id)
            if row.status not in ({"pending", "failed", "validating"} if recover else {"pending", "failed"}):
                raise ConflictException("文件正在验证或任务已失效，请刷新查看")
            row.status = "validating"
            row.validation_error = None
            key, multipart, size, filename = row.object_key, row.oss_upload_id, row.size_bytes, row.original_filename
        try:
            await self.storage.complete(key, multipart, size)
            # Mark multipart completion before parsing so validation retries do not re-complete OSS parts.
            async with get_db_ctx() as db, db.begin():
                row = await db.get(CourseUpload, upload_id)
                row.oss_upload_id = ""
            metadata = await self.storage.validate_resource(key, filename, size)
            async with get_db_ctx() as db, db.begin():
                course_id = await db.scalar(select(CourseUpload.course_id).where(CourseUpload.id == upload_id))
                course = await db.scalar(select(Course).where(Course.id == course_id).with_for_update())
                row = await db.scalar(select(CourseUpload).where(CourseUpload.id == upload_id).with_for_update())
                if not course or course.status == "archived" or row.status != "validating":
                    raise ConflictException("课程或上传任务状态已变化")
                if row.chapter_id:
                    chapter = await db.scalar(select(CourseChapter).where(CourseChapter.id == row.chapter_id).with_for_update())
                    if not chapter or chapter.course_id != course.id or chapter.resource_version != row.expected_resource_version:
                        raise ConflictException("章节已由其他管理员更新，请刷新后重新替换")
                    old_upload = await db.scalar(select(CourseUpload).where(CourseUpload.object_key == chapter.resource_storage_key).with_for_update())
                    if old_upload and old_upload.id != row.id:
                        old_upload.status = "retired"
                        old_upload.expires_at = _now() + timedelta(hours=24)
                    chapter.resource_version += 1
                else:
                    chapter = CourseChapter(course_id=course.id, title=row.title, sort_order=row.sort_order, resource_version=1)
                    db.add(chapter)
                for name, value in metadata.items():
                    setattr(chapter, name, value)
                    if hasattr(row, name):
                        setattr(row, name, value)
                chapter.resource_storage_key = row.object_key
                chapter.video_storage_key = row.object_key if metadata["resource_type"] == "video" else None
                chapter.original_filename, chapter.size_bytes = row.original_filename, row.size_bytes
                await db.flush()
                row.chapter_id, row.status, row.completed_at = chapter.id, "bound", _now()
                db.add(CourseAuditLog(actor_type="admin", actor_id=admin_id, action="course.chapter.resource_bound", object_type="course_chapter",
                    object_id=chapter.id, result="succeeded", summary={"resource_version": chapter.resource_version, "resource_type": chapter.resource_type}))
        except Exception as error:
            async with get_db_ctx() as db, db.begin():
                row = await db.scalar(select(CourseUpload).where(CourseUpload.id == upload_id).with_for_update())
                if row and row.status == "validating":
                    row.status, row.validation_error = "failed", str(error)[:1024]
            raise
        return await self.detail(upload_id)
