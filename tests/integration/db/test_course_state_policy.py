"""课程状态口径（vweb001 收编）：offline 授权可看可播，archived 一律停。

对应 7.1 矩阵：
- published：可看可播（无授权走预览）
- offline：已授权可看可播；未授权拒绝
- archived：任何角色都拒绝
"""

import importlib
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.domain.certification.src.index import Course, CourseAuditLog, CourseChapter, CourseEnrollment
from app.domain.user.src.index import User
from app.port.exceptions import ForbiddenException, NotFoundException
from app.services.course import CourseService

pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


@pytest.fixture
async def ctx(monkeypatch):
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"], poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def ctx_fn():
        async with factory() as db:
            yield db

    monkeypatch.setattr(
        importlib.import_module("app.services.course"), "get_db_ctx", ctx_fn
    )
    storage = SimpleNamespace(
        object_exists=AsyncMock(return_value=True),
        signed_url=AsyncMock(return_value="https://signed.invalid/resource"),
    )
    prefix = uuid4().hex
    async with factory() as db, db.begin():
        course = Course(
            title=prefix,
            category="test",
            cover_storage_key=f"cover-{prefix}",
            price=0,
            status="published",
            is_active=True,
            preview_chapter_count=1,
        )
        user = User(openid=prefix)
        db.add_all([course, user])
        await db.flush()
        chapter = CourseChapter(
            course_id=course.id,
            title="ch1",
            sort_order=1,
            resource_type="video",
            video_storage_key=f"video-{prefix}",
            resource_storage_key=f"video-{prefix}",
            original_filename="a.mp4",
            content_type="video/mp4",
            size_bytes=100,
            duration=60,
        )
        db.add(chapter)
        await db.flush()
        cid, uid, chid = course.id, user.id, chapter.id

    service = CourseService(storage)

    async def set_state(status: str):
        async with factory() as db, db.begin():
            row = await db.get(Course, cid)
            row.status = status
            row.is_active = status == "published"

    async def grant_access():
        async with factory() as db, db.begin():
            db.add(
                CourseEnrollment(
                    user_id=uid,
                    course_id=cid,
                    status="enrolled",
                    learning_access=True,
                    access_granted_at=datetime.now(timezone.utc),
                )
            )

    yield SimpleNamespace(
        factory=factory,
        service=service,
        cid=cid,
        uid=uid,
        chid=chid,
        set_state=set_state,
        grant_access=grant_access,
    )

    async with factory() as db, db.begin():
        await db.execute(
            delete(CourseAuditLog).where(
                CourseAuditLog.object_type == "course_chapter",
                CourseAuditLog.object_id == chid,
            )
        )
        await db.execute(
            delete(CourseEnrollment).where(CourseEnrollment.course_id == cid)
        )
        await db.execute(delete(CourseChapter).where(CourseChapter.course_id == cid))
        await db.execute(delete(Course).where(Course.id == cid))
        await db.execute(delete(User).where(User.id == uid))
    await engine.dispose()


async def test_published_without_access_can_preview_first_chapter(ctx):
    chapters = await ctx.service.get_chapters(ctx.cid, ctx.uid)
    assert chapters.chapters[0].can_play is True
    await ctx.service.issue_resource(ctx.uid, ctx.cid, ctx.chid)


async def test_offline_keeps_access_and_playback_for_granted_users(ctx):
    await ctx.grant_access()
    await ctx.set_state("offline")
    chapters = await ctx.service.get_chapters(ctx.cid, ctx.uid)
    assert chapters.chapters[0].can_play is True
    playback = await ctx.service.issue_resource(ctx.uid, ctx.cid, ctx.chid)
    assert playback.url


async def test_offline_rejects_users_without_access(ctx):
    await ctx.set_state("offline")
    with pytest.raises(NotFoundException):
        await ctx.service.get_chapters(ctx.cid, ctx.uid)
    with pytest.raises(ForbiddenException, match="课程已下线"):
        await ctx.service.issue_resource(ctx.uid, ctx.cid, ctx.chid)


async def test_archived_blocks_even_granted_users(ctx):
    await ctx.grant_access()
    await ctx.set_state("archived")
    with pytest.raises(NotFoundException):
        await ctx.service.get_chapters(ctx.cid, ctx.uid)
    with pytest.raises(ForbiddenException, match="课程已归档"):
        await ctx.service.issue_resource(ctx.uid, ctx.cid, ctx.chid)
