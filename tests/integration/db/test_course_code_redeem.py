"""B2 兑换码：本人兑换、幂等、状态矩阵、我的码直读本表。"""

import importlib
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.domain.certification.src.index import (
    Course,
    CourseAuditLog,
    CourseChapter,
    CourseCode,
    CourseEnrollment,
)
from app.domain.user.src.index import User
from app.port.config import settings
from app.port.exceptions import BusinessException
from app.services import course_code_crypto
from app.services.course_code import CourseCodeService

pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


@pytest.fixture
async def ctx(monkeypatch):
    monkeypatch.setattr(settings, "CODE_HASH_PEPPER", "test-pepper")
    monkeypatch.setattr(
        settings, "CODE_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"], poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def ctx_fn():
        async with factory() as db:
            yield db

    monkeypatch.setattr(
        importlib.import_module("app.services.course_code"), "get_db_ctx", ctx_fn
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
            preview_chapter_count=0,
        )
        owner = User(openid=f"own-{prefix}")
        stranger = User(openid=f"str-{prefix}")
        db.add_all([course, owner, stranger])
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
        cid, owner_id, stranger_id, chid = course.id, owner.id, stranger.id, chapter.id

    created_codes: list[int] = []

    async def add_code(*, owner: int | None, status: str = "issued") -> tuple[int, str]:
        raw = course_code_crypto.generate_code()
        async with factory() as db, db.begin():
            row = CourseCode(
                code_hash=course_code_crypto.hash_code(raw),
                code_cipher=course_code_crypto.encrypt_code(raw),
                course_id=cid,
                owner_user_id=owner,
                status=status,
                source="admin",
            )
            db.add(row)
            await db.flush()
            created_codes.append(row.id)
            return row.id, raw

    async def set_course_status(status: str):
        async with factory() as db, db.begin():
            row = await db.get(Course, cid)
            row.status = status
            row.is_active = status == "published"

    async def grant_existing_access(user_id: int):
        async with factory() as db, db.begin():
            db.add(
                CourseEnrollment(
                    user_id=user_id,
                    course_id=cid,
                    status="enrolled",
                    learning_access=True,
                    access_granted_at=datetime.now(timezone.utc),
                )
            )

    yield SimpleNamespace(
        factory=factory,
        cid=cid,
        owner_id=owner_id,
        stranger_id=stranger_id,
        add_code=add_code,
        set_course_status=set_course_status,
        grant_existing_access=grant_existing_access,
        created_codes=created_codes,
    )

    async with factory() as db, db.begin():
        await db.execute(
            delete(CourseAuditLog).where(
                CourseAuditLog.object_type == "course_code",
                CourseAuditLog.object_id.in_(created_codes or [0]),
            )
        )
        await db.execute(delete(CourseEnrollment).where(CourseEnrollment.course_id == cid))
        await db.execute(delete(CourseCode).where(CourseCode.course_id == cid))
        await db.execute(delete(CourseChapter).where(CourseChapter.course_id == cid))
        await db.execute(delete(Course).where(Course.id == cid))
        await db.execute(delete(User).where(User.id.in_([owner_id, stranger_id])))
    await engine.dispose()


async def test_redeem_own_code_grants_course_access(ctx):
    code_id, raw = await ctx.add_code(owner=ctx.owner_id)
    result = await CourseCodeService.redeem(ctx.owner_id, raw)
    assert result["learning_access"] is True
    assert result["already_redeemed"] is False
    async with ctx.factory() as db:
        code = await db.get(CourseCode, code_id)
        assert code.status == "redeemed" and code.redeemed_at is not None
        enrollment = await db.scalar(
            select(CourseEnrollment).where(
                CourseEnrollment.course_id == ctx.cid,
                CourseEnrollment.user_id == ctx.owner_id,
            )
        )
        assert enrollment.learning_access is True
        assert enrollment.status == "enrolled"
        assert enrollment.source == "redeem"
        assert enrollment.course_code_id == code_id


async def test_redeem_is_idempotent_and_keeps_existing_grant_time(ctx):
    code_id, raw = await ctx.add_code(owner=ctx.owner_id)
    await ctx.grant_existing_access(ctx.owner_id)
    async with ctx.factory() as db:
        before = await db.scalar(
            select(CourseEnrollment).where(CourseEnrollment.course_id == ctx.cid)
        )
        granted_before = before.access_granted_at

    result = await CourseCodeService.redeem(ctx.owner_id, raw)
    assert result["already_redeemed"] is False
    async with ctx.factory() as db:
        enrollment = await db.scalar(
            select(CourseEnrollment).where(CourseEnrollment.course_id == ctx.cid)
        )
        assert enrollment.access_granted_at == granted_before
        assert enrollment.course_code_id == code_id

    again = await CourseCodeService.redeem(ctx.owner_id, raw)
    assert again["already_redeemed"] is True


async def test_redeem_other_users_code_is_rejected(ctx):
    _, raw = await ctx.add_code(owner=ctx.owner_id)
    with pytest.raises(BusinessException, match="不属于当前账号"):
        await CourseCodeService.redeem(ctx.stranger_id, raw)


async def test_revoked_and_refunded_codes_are_rejected(ctx):
    _, revoked_raw = await ctx.add_code(owner=ctx.owner_id, status="revoked")
    _, refunded_raw = await ctx.add_code(owner=ctx.owner_id, status="refunded")
    with pytest.raises(BusinessException, match="已作废"):
        await CourseCodeService.redeem(ctx.owner_id, revoked_raw)
    with pytest.raises(BusinessException, match="已退款回收"):
        await CourseCodeService.redeem(ctx.owner_id, refunded_raw)


async def test_unknown_code_is_rejected(ctx):
    with pytest.raises(BusinessException, match="兑换码不存在"):
        await CourseCodeService.redeem(ctx.owner_id, "VW-AAAA-BBBB-CCCC-DDDD")


async def test_offline_or_archived_course_cannot_be_redeemed(ctx):
    _, raw = await ctx.add_code(owner=ctx.owner_id)
    await ctx.set_course_status("offline")
    with pytest.raises(BusinessException, match="已下线"):
        await CourseCodeService.redeem(ctx.owner_id, raw)
    await ctx.set_course_status("archived")
    with pytest.raises(BusinessException, match="已归档"):
        await CourseCodeService.redeem(ctx.owner_id, raw)


async def test_list_for_user_only_returns_own_codes(ctx):
    mine_id, _ = await ctx.add_code(owner=ctx.owner_id)
    await ctx.add_code(owner=ctx.stranger_id)
    codes = await CourseCodeService.list_for_user(ctx.owner_id)
    assert [item["id"] for item in codes] == [mine_id]
    assert codes[0]["owner_miniapp_user_id"] == ctx.owner_id
