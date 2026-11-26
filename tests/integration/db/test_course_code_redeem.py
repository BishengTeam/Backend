"""匿名兑换码：兑换时绑定归属人、已有权益不消耗、手机号门槛。"""

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
        phone_user = User(openid=f"ph-{prefix}", phone="13800001111")
        no_phone_user = User(openid=f"np-{prefix}")
        db.add_all([course, phone_user, no_phone_user])
        await db.flush()
        cid, uid, np_uid = course.id, phone_user.id, no_phone_user.id

    created_codes: list[int] = []

    async def add_code(*, owner: int | None = None, status: str = "issued") -> tuple[int, str]:
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
        uid=uid,
        np_uid=np_uid,
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
        await db.execute(delete(Course).where(Course.id == cid))
        await db.execute(delete(User).where(User.id.in_([uid, np_uid])))
    await engine.dispose()


async def test_anonymous_code_grants_access_and_binds_owner(ctx):
    code_id, raw = await ctx.add_code()
    result = await CourseCodeService.redeem(ctx.uid, raw)
    assert result["learning_access"] is True
    assert result["already_redeemed"] is False
    async with ctx.factory() as db:
        code = await db.get(CourseCode, code_id)
        assert code.owner_user_id == ctx.uid
        assert code.status == "redeemed" and code.redeemed_at is not None
        enrollment = await db.scalar(
            select(CourseEnrollment).where(CourseEnrollment.course_id == ctx.cid)
        )
        assert enrollment.learning_access is True
        assert enrollment.source == "redeem"
        assert enrollment.course_code_id == code_id


async def test_second_redeem_of_same_code_is_idempotent(ctx):
    _, raw = await ctx.add_code()
    first = await CourseCodeService.redeem(ctx.uid, raw)
    assert first["already_redeemed"] is False
    again = await CourseCodeService.redeem(ctx.uid, raw)
    assert again["already_redeemed"] is True


async def test_existing_access_rejects_without_consuming_code(ctx):
    code_id, raw = await ctx.add_code()
    await ctx.grant_existing_access(ctx.uid)
    with pytest.raises(BusinessException, match="已拥有该课程学习权限"):
        await CourseCodeService.redeem(ctx.uid, raw)
    async with ctx.factory() as db:
        code = await db.get(CourseCode, code_id)
        assert code.status == "issued"
        assert code.owner_user_id is None


async def test_code_bound_to_someone_else_is_rejected(ctx):
    _, raw = await ctx.add_code(owner=ctx.np_uid)
    with pytest.raises(BusinessException, match="不属于当前账号"):
        await CourseCodeService.redeem(ctx.uid, raw)


async def test_user_without_phone_cannot_redeem(ctx):
    _, raw = await ctx.add_code()
    with pytest.raises(BusinessException, match="授权手机号"):
        await CourseCodeService.redeem(ctx.np_uid, raw)


async def test_revoked_and_refunded_codes_are_rejected(ctx):
    _, revoked_raw = await ctx.add_code(status="revoked")
    _, refunded_raw = await ctx.add_code(status="refunded")
    with pytest.raises(BusinessException, match="已作废"):
        await CourseCodeService.redeem(ctx.uid, revoked_raw)
    with pytest.raises(BusinessException, match="已退款回收"):
        await CourseCodeService.redeem(ctx.uid, refunded_raw)


async def test_unknown_code_is_rejected(ctx):
    with pytest.raises(BusinessException, match="兑换码不存在"):
        await CourseCodeService.redeem(ctx.uid, "VW-AAAA-BBBB-CCCC-DDDD")


async def test_offline_or_archived_course_cannot_be_redeemed(ctx):
    _, raw = await ctx.add_code()
    await ctx.set_course_status("offline")
    with pytest.raises(BusinessException, match="已下线"):
        await CourseCodeService.redeem(ctx.uid, raw)
    await ctx.set_course_status("archived")
    with pytest.raises(BusinessException, match="已归档"):
        await CourseCodeService.redeem(ctx.uid, raw)
