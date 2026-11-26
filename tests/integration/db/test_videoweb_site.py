"""B7 视频站站点接口适配层：字段按视频站前端契约组装。"""

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

from app.domain.certification.src.index import (
    Course,
    CourseChapter,
    CourseEnrollment,
    UserChapterProgress,
)
from app.domain.user.src.index import User
from app.services.videoweb_site import VideoWebSiteService

pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


@pytest.fixture
async def ctx(monkeypatch):
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"], poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def ctx_fn():
        async with factory() as db:
            yield db

    for module in ("app.services.videoweb_site", "app.services.course"):
        monkeypatch.setattr(importlib.import_module(module), "get_db_ctx", ctx_fn)
    monkeypatch.setattr(
        VideoWebSiteService,
        "_course_service",
        SimpleNamespace(
            storage=SimpleNamespace(
                signed_url=AsyncMock(return_value="https://signed.invalid/cover.jpg"),
                object_exists=AsyncMock(return_value=True),
            )
        ),
    )

    prefix = uuid4().hex
    async with factory() as db, db.begin():
        course = Course(
            title=f"课程-{prefix[:6]}",
            category="网络",
            cover_storage_key=f"cover-{prefix}",
            price=0,
            status="published",
            is_active=True,
            preview_chapter_count=0,
            teacher_name="李老师",
        )
        user = User(openid=prefix, phone="13600001111")
        db.add_all([course, user])
        await db.flush()
        chapter = CourseChapter(
            course_id=course.id,
            title="第一章",
            sort_order=1,
            resource_type="video",
            video_storage_key=f"video-{prefix}",
            resource_storage_key=f"video-{prefix}",
            original_filename="a.mp4",
            content_type="video/mp4",
            size_bytes=100,
            duration=120,
        )
        db.add(chapter)
        await db.flush()
        enrollment = CourseEnrollment(
            user_id=user.id,
            course_id=course.id,
            status="enrolled",
            learning_access=True,
            access_granted_at=datetime.now(timezone.utc),
            source="redeem",
        )
        db.add(enrollment)
        db.add(
            UserChapterProgress(
                user_id=user.id,
                course_id=course.id,
                chapter_id=chapter.id,
                last_position_seconds=45,
                last_page=2,
                is_completed=True,
            )
        )
        cid, uid, chid = course.id, user.id, chapter.id

    yield SimpleNamespace(factory=factory, cid=cid, uid=uid, chid=chid)

    async with factory() as db, db.begin():
        await db.execute(delete(CourseEnrollment).where(CourseEnrollment.course_id == cid))
        await db.execute(delete(UserChapterProgress).where(UserChapterProgress.course_id == cid))
        await db.execute(delete(CourseChapter).where(CourseChapter.course_id == cid))
        await db.execute(delete(Course).where(Course.id == cid))
        await db.execute(delete(User).where(User.id == uid))
    await engine.dispose()


async def test_my_courses_returns_frontend_contract_fields(ctx):
    items = await VideoWebSiteService.my_courses(ctx.uid)
    assert len(items) == 1
    item = items[0]
    assert item["id"] == ctx.cid
    assert item["cover_url"] == "https://signed.invalid/cover.jpg"
    assert item["entitlement_id"] > 0
    assert item["chapter_count"] == 1
    assert item["completed_count"] == 1
    assert item["continue_point"]["chapter_id"] == ctx.chid
    assert item["continue_point"]["position_seconds"] == 45


async def test_chapters_returns_continue_chapter_id(ctx):
    data = await VideoWebSiteService.chapters(ctx.uid, ctx.cid)
    assert [chapter["id"] for chapter in data["chapters"]] == [ctx.chid]
    assert data["chapters"][0]["can_play"] is True
    assert data["continue_chapter_id"] == ctx.chid


async def test_save_progress_maps_position_seconds_field(ctx):
    result = await VideoWebSiteService.save_progress(
        ctx.uid,
        ctx.cid,
        ctx.chid,
        {"position_seconds": 77, "is_completed": False, "resource_version": 1, "last_page": 1},
    )
    assert result["position_seconds"] == 77
    assert result["last_page"] == 1
    assert result["is_completed"] is False
