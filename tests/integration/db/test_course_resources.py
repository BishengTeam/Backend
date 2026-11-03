import asyncio
import importlib
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.domain.certification.src.index import Course, CourseChapter, CourseUpload, CourseEnrollment, CourseAuditLog, UserChapterProgress
from app.domain.user.src.index import User
from app.port.exceptions import BusinessException, ConflictException, ForbiddenException, ValidationException
from app.schemas.course import ChapterProgressUpsert
from app.schemas.course_upload import CourseUploadCreate
from app.services.admin_course import AdminCourseService
from app.services.course import CourseService
from app.services.course_upload import CourseUploadService

pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


@pytest.fixture
async def context(monkeypatch):
    engine = create_async_engine(os.environ['TEST_DATABASE_URL'], poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    @asynccontextmanager
    async def ctx():
        async with factory() as db:
            yield db
    for name in ('course', 'course_upload', 'course_resource_upload', 'admin_course'):
        monkeypatch.setattr(importlib.import_module(f'app.services.{name}'), 'get_db_ctx', ctx)
    stored = {}
    async def initiate(**data):
        key = uuid4().hex
        stored[key] = data['filename']
        return SimpleNamespace(object_key=key, upload_id=key)
    async def validate(key, filename, size):
        if filename.startswith('broken'):
            raise ValidationException('PDF 损坏')
        return dict(resource_type='document', page_count=3, duration=None, content_type='application/pdf') if filename.endswith('.pdf') else dict(resource_type='video', page_count=None, duration=60, content_type='video/mp4')
    storage = SimpleNamespace(initiate=initiate, complete=AsyncMock(), validate_resource=validate, list_parts=AsyncMock(return_value=[]),
        delete=AsyncMock(), abort=AsyncMock(), object_exists=AsyncMock(return_value=True), signed_url=AsyncMock(return_value='https://signed.invalid/resource'))
    prefix = uuid4().hex
    async with factory() as db, db.begin():
        course = Course(title=prefix, category='test', cover_storage_key=prefix, price=0, status='draft', is_active=False)
        user = User(openid=prefix)
        db.add_all([course, user]); await db.flush()
        cid, uid = course.id, user.id
    uploads, courses = CourseUploadService(storage), CourseService(storage)
    async def create(filename='document.pdf', size_bytes=200, **kwargs):
        return await uploads.create(CourseUploadCreate(kind='chapter_file', filename=filename, content_type='application/octet-stream', size_bytes=size_bytes, course_id=cid, **kwargs), admin_id=1)
    async def activate():
        await AdminCourseService(storage).change_lifecycle(cid, 'publish', admin_id=1)
    yield SimpleNamespace(db=factory, uploads=uploads, courses=courses, cid=cid, uid=uid, create=create, activate=activate, storage=storage)
    async with factory() as db, db.begin():
        chapters = select(CourseChapter.id).where(CourseChapter.course_id == cid)
        tasks = select(CourseUpload.id).where(CourseUpload.course_id == cid)
        await db.execute(delete(CourseAuditLog).where(((CourseAuditLog.object_type == 'course_chapter') & CourseAuditLog.object_id.in_(chapters)) |
            ((CourseAuditLog.object_type == 'course_upload') & CourseAuditLog.object_id.in_(tasks)) |
            ((CourseAuditLog.object_type == 'course') & (CourseAuditLog.object_id == cid))))
        await db.execute(delete(UserChapterProgress).where(UserChapterProgress.course_id == cid))
        await db.execute(delete(CourseEnrollment).where(CourseEnrollment.course_id == cid))
        await db.execute(delete(CourseUpload).where(CourseUpload.course_id == cid))
        await db.execute(delete(CourseChapter).where(CourseChapter.course_id == cid))
        await db.execute(delete(Course).where(Course.id == cid))
        await db.execute(delete(User).where(User.id == uid))
    await engine.dispose()


async def test_out_of_order_completion_keeps_selection_order_and_is_idempotent(context):
    first = await context.create('first.pdf')
    second = await context.create('second.mp4')
    await context.uploads.complete(second.id, admin_id=1)
    result = await context.uploads.complete(first.id, admin_id=1)
    again = await context.uploads.complete(first.id, admin_id=1)
    assert result.chapter_id == again.chapter_id
    async with context.db() as db:
        rows = list(await db.scalars(select(CourseChapter).where(CourseChapter.course_id == context.cid).order_by(CourseChapter.sort_order)))
        assert [row.title for row in rows] == ['first', 'second']
        assert rows[0].duration is None and rows[0].page_count == 3 and rows[0].video_storage_key is None
        assert rows[1].duration == 60 and rows[1].resource_type == 'video'
    await context.activate()


async def test_invalid_file_is_not_bound_and_blocks_publish_until_abandoned(context):
    valid = await context.create()
    await context.uploads.complete(valid.id, admin_id=1)
    broken = await context.create('broken.pdf')
    with pytest.raises(ValidationException):
        await context.uploads.complete(broken.id, admin_id=1)
    assert (await context.uploads.detail(broken.id)).status == 'failed'
    with pytest.raises(BusinessException, match='未完成'):
        await context.activate()
    await context.uploads.abort(broken.id, admin_id=1)
    await context.activate()


async def test_concurrent_replacements_switch_once_and_preserve_chapter_identity(context):
    original = await context.create('first.mp4')
    bound = await context.uploads.complete(original.id, admin_id=1)
    candidates = [await context.create(f'new{i}.pdf', chapter_id=bound.chapter_id, expected_resource_version=1) for i in range(2)]
    results = await asyncio.gather(*(context.uploads.complete(task.id, admin_id=i + 1) for i, task in enumerate(candidates)), return_exceptions=True)
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, ConflictException) for result in results) == 1
    async with context.db() as db:
        chapter = await db.get(CourseChapter, bound.chapter_id)
        assert chapter.title == 'first' and chapter.sort_order == 1
        assert chapter.resource_version == 2 and chapter.resource_type == 'document' and chapter.duration is None
        old = await db.get(CourseUpload, original.id)
        assert old.status == 'retired' and old.expires_at > datetime.now(timezone.utc)
    context.storage.delete.assert_not_called()


async def test_progress_requires_access_version_and_manual_read_completion(context):
    upload = await context.create()
    bound = await context.uploads.complete(upload.id, admin_id=1)
    await context.activate()
    with pytest.raises(ForbiddenException):
        await context.courses.issue_resource(context.uid, context.cid, bound.chapter_id)
    async with context.db() as db, db.begin():
        db.add(CourseEnrollment(user_id=context.uid, course_id=context.cid, status='enrolled', learning_access=True))
    with pytest.raises(ValidationException, match='文档'):
        await context.courses.issue_playback(context.uid, context.cid, bound.chapter_id)
    await context.courses.issue_resource(context.uid, context.cid, bound.chapter_id)
    progress = await context.courses.upsert_progress(context.uid, context.cid, ChapterProgressUpsert(chapter_id=bound.chapter_id, last_page=3))
    assert progress.last_page == 3 and progress.completed_chapter_ids == []
    await context.courses.upsert_progress(context.uid, context.cid, ChapterProgressUpsert(chapter_id=bound.chapter_id, last_page=3, is_completed=True))
    replacement = await context.create('new.mp4', chapter_id=bound.chapter_id, expected_resource_version=1)
    await context.uploads.complete(replacement.id, admin_id=1)
    assert (await context.courses.get_progress(context.uid, context.cid)).completed_chapter_ids == []
    with pytest.raises(ConflictException):
        await context.courses.upsert_progress(context.uid, context.cid, ChapterProgressUpsert(chapter_id=bound.chapter_id, last_page=3, is_completed=True))
    current = await context.courses.upsert_progress(context.uid, context.cid, ChapterProgressUpsert(chapter_id=bound.chapter_id, resource_version=2, last_position_seconds=5))
    assert current.resource_version == 2 and current.completed_chapter_ids == [] and current.last_position_seconds == 5


async def test_cleanup_does_not_delete_validating_or_bound_resources(context):
    task = await context.create()
    async with context.db() as db, db.begin():
        row = await db.get(CourseUpload, task.id)
        row.status, row.expires_at = 'validating', datetime.now(timezone.utc) - timedelta(days=1)
    await context.uploads.cleanup_expired()
    context.storage.delete.assert_not_called()


async def test_preview_uses_current_chapter_rank_after_abandoned_upload(context):
    abandoned = await context.create('abandoned.pdf')
    await context.uploads.abort(abandoned.id, admin_id=1)
    first = await context.create('first.pdf')
    bound = await context.uploads.complete(first.id, admin_id=1)
    async with context.db() as db, db.begin():
        chapter = await db.get(CourseChapter, bound.chapter_id)
        chapter.sort_order = 3  # A pending/abandoned task can leave a gap.
        course = await db.get(Course, context.cid)
        course.preview_chapter_count = 1
    await context.activate()
    public = await context.courses.get_chapters(context.cid, None)
    assert public.chapters[0].can_play
    await context.courses.issue_resource(context.uid, context.cid, bound.chapter_id)


async def test_failed_replacement_keeps_original_resource(context):
    first = await context.create('original.mp4')
    original = await context.uploads.complete(first.id, admin_id=1)
    broken = await context.create('broken.pdf', chapter_id=original.chapter_id, expected_resource_version=1)
    with pytest.raises(ValidationException):
        await context.uploads.complete(broken.id, admin_id=1)
    async with context.db() as db:
        chapter = await db.get(CourseChapter, original.chapter_id)
        assert chapter.resource_storage_key == original.object_key and chapter.resource_version == 1
        assert chapter.duration == 60 and chapter.page_count is None


async def test_publish_during_verification_is_blocked_and_can_retry_after_binding(context):
    first = await context.create()
    await context.uploads.complete(first.id, admin_id=1)
    other = await context.create('other.pdf')
    entered, release = asyncio.Event(), asyncio.Event()
    validate = context.storage.validate_resource
    async def slow(*args):
        entered.set()
        await release.wait()
        return await validate(*args)
    context.storage.validate_resource = slow
    complete = asyncio.create_task(context.uploads.complete(other.id, admin_id=1))
    await entered.wait()
    try:
        with pytest.raises(BusinessException, match='未完成'):
            await context.activate()
    finally:
        release.set()
        await complete
    await context.activate()


async def test_existing_video_size_limit_fits_database(context):
    upload = await context.create('large.mp4', size_bytes=5 * 1024 * 1024 * 1024)
    assert (await context.uploads.detail(upload.id)).size_bytes == 5 * 1024 * 1024 * 1024
    await context.uploads.abort(upload.id, admin_id=1)


async def test_verification_lease_prevents_reentry_and_recovers_after_worker_exit(context):
    task = await context.create()
    async with context.db() as db, db.begin():
        row = await db.get(CourseUpload, task.id)
        row.status, row.oss_upload_id = 'validating', ''
        from sqlalchemy import func
        await db.scalar(select(func.pg_advisory_xact_lock(32601, task.id)))
        with pytest.raises(ConflictException, match='正在验证'):
            await context.uploads.complete(task.id, admin_id=1)
    result = await context.uploads.complete(task.id, admin_id=1)
    assert result.status == 'bound'
