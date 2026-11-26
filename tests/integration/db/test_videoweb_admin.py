"""B6 学习授权页：学习者列表、授权清单、禁用后判权（目录/播放/兑换）。"""

import importlib
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.domain.certification.src.index import (
    Course,
    CourseAuditLog,
    CourseChapter,
    CourseCode,
    CourseEnrollment,
    UserChapterProgress,
)
from app.domain.user.src.index import User
from app.domain.user.src.model.admin_user import AdminUser
from app.port.config import settings
from app.port.exceptions import BusinessException, ForbiddenException, NotFoundException
from app.services import course_code_crypto
from app.services.course import CourseService
from app.services.course_code import CourseCodeService
from app.services.videoweb_admin import VideoWebAdminService

pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


@pytest.fixture
async def ctx(monkeypatch):
    monkeypatch.setattr(settings, "CODE_HASH_PEPPER", "test-pepper")
    monkeypatch.setattr(settings, "CODE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"], poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def ctx_fn():
        async with factory() as db:
            yield db

    for module in (
        "app.services.course",
        "app.services.course_code",
        "app.services.videoweb_admin",
        "app.services.course_code_admin",
    ):
        monkeypatch.setattr(importlib.import_module(module), "get_db_ctx", ctx_fn)

    storage = SimpleNamespace(
        object_exists=AsyncMock(return_value=True),
        signed_url=AsyncMock(return_value="https://signed.invalid/x"),
    )
    prefix = uuid4().hex
    async with factory() as db, db.begin():
        course = Course(
            title=f"课程-{prefix[:6]}",
            category="test",
            cover_storage_key=f"cover-{prefix}",
            price=0,
            status="published",
            is_active=True,
            preview_chapter_count=0,
        )
        user = User(openid=prefix, phone="13700009999")
        admin = AdminUser(
            username=f"adm-{prefix}"[:32],
            display_name="管理员",
            password_hash="x",
            role="super_admin",
        )
        db.add_all([course, user, admin])
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
        db.add(
            CourseEnrollment(
                user_id=user.id,
                course_id=course.id,
                status="enrolled",
                learning_access=True,
                access_granted_at=datetime.now(timezone.utc),
                source="redeem",
            )
        )
        db.add(
            UserChapterProgress(
                user_id=user.id,
                course_id=course.id,
                chapter_id=chapter.id,
                last_position_seconds=30,
                is_completed=False,
            )
        )
        cid, uid, aid, chid = course.id, user.id, admin.id, chapter.id

    raw_code = course_code_crypto.generate_code()
    async with factory() as db, db.begin():
        db.add(
            CourseCode(
                code_hash=course_code_crypto.hash_code(raw_code),
                code_cipher=course_code_crypto.encrypt_code(raw_code),
                course_id=cid,
                owner_user_id=uid,
                status="issued",
                source="admin",
            )
        )

    yield SimpleNamespace(
        factory=factory,
        course_service=CourseService(storage),
        cid=cid,
        uid=uid,
        aid=aid,
        chid=chid,
        raw_code=raw_code,
    )

    async with factory() as db, db.begin():
        await db.execute(
            delete(CourseAuditLog).where(
                CourseAuditLog.object_id.in_([cid, uid])
            )
        )
        await db.execute(delete(CourseEnrollment).where(CourseEnrollment.course_id == cid))
        await db.execute(delete(CourseCode).where(CourseCode.course_id == cid))
        await db.execute(delete(UserChapterProgress).where(UserChapterProgress.course_id == cid))
        await db.execute(delete(CourseChapter).where(CourseChapter.course_id == cid))
        await db.execute(delete(Course).where(Course.id == cid))
        await db.execute(delete(User).where(User.id == uid))
        await db.execute(delete(AdminUser).where(AdminUser.id == aid))
    await engine.dispose()


async def test_learners_list_shows_counts_and_last_learning(ctx):
    page = await VideoWebAdminService.list_learners(keyword="1370000", page=1, page_size=20)
    assert page["total"] >= 1
    row = next(item for item in page["items"] if item["user_id"] == ctx.uid)
    assert row["entitlement_count"] == 1
    assert row["last_learned_at"] is not None
    assert row["access_disabled_at"] is None
    assert row["phone"] == "137****9999"


async def test_entitlements_list_includes_course_title(ctx):
    rows = await VideoWebAdminService.list_entitlements(ctx.uid)
    assert len(rows) == 1
    assert rows[0]["course_id"] == ctx.cid
    assert rows[0]["course_title"].startswith("课程-")
    assert rows[0]["learning_access"] is True


async def test_disable_blocks_catalog_playback_and_redemption(ctx):
    await VideoWebAdminService.set_access(ctx.uid, enabled=False, admin_id=ctx.aid)

    with pytest.raises(NotFoundException):
        await ctx.course_service.get_chapters(ctx.cid, ctx.uid)
    with pytest.raises(ForbiddenException, match="视频站访问已禁用"):
        await ctx.course_service.issue_resource(ctx.uid, ctx.cid, ctx.chid)
    with pytest.raises(BusinessException, match="视频站访问已禁用"):
        await CourseCodeService.redeem(ctx.uid, ctx.raw_code)


async def test_enable_restores_access_immediately(ctx):
    await VideoWebAdminService.set_access(ctx.uid, enabled=False, admin_id=ctx.aid)
    result = await VideoWebAdminService.set_access(ctx.uid, enabled=True, admin_id=ctx.aid)
    assert result["access_disabled_at"] is None
    chapters = await ctx.course_service.get_chapters(ctx.cid, ctx.uid)
    assert chapters.chapters[0].can_play is True
