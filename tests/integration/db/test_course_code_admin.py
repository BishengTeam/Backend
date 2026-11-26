"""B5 管理端兑换码：必选归属人、兑换人回退、撤销/退款留痕、用户搜索。"""

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
from app.domain.user.src.model.admin_user import AdminUser
from app.domain.user.src.model.user_realname import UserRealname
from app.domain.user.src.model.user_profile import UserProfile
from app.port.config import settings
from app.services import course_code_crypto
from app.services.course_code_admin import CourseCodeAdminService

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

    monkeypatch.setattr(
        importlib.import_module("app.services.course_code_admin"), "get_db_ctx", ctx_fn
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
        user_a = User(openid=f"a-{prefix}", phone="13800001111")
        user_b = User(openid=f"b-{prefix}", phone="13900002222")
        user_c = User(openid=f"c-{prefix}")
        admin = AdminUser(
            username=f"adm-{prefix}"[:32],
            display_name="测试管理员",
            password_hash="x",
            role="super_admin",
        )
        db.add_all([course, user_a, user_b, user_c, admin])
        await db.flush()
        db.add(UserProfile(user_id=user_a.id, nickname="小花"))
        db.add(UserRealname(user_id=user_b.id, user_type="student", real_name="张三", id_card_number="510100199001010011", status="verified"))
        await db.flush()
        cid, a_id, b_id, c_id, admin_id = (
            course.id, user_a.id, user_b.id, user_c.id, admin.id,
        )

    created_codes: list[int] = []
    yield SimpleNamespace(
        factory=factory,
        cid=cid,
        a_id=a_id,
        b_id=b_id,
        c_id=c_id,
        admin_id=admin_id,
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
        await db.execute(delete(UserProfile).where(UserProfile.user_id.in_([a_id, b_id, c_id])))
        await db.execute(delete(UserRealname).where(UserRealname.user_id.in_([a_id, b_id, c_id])))
        await db.execute(delete(Course).where(Course.id == cid))
        await db.execute(delete(User).where(User.id.in_([a_id, b_id, c_id])))
        await db.execute(delete(AdminUser).where(AdminUser.id == admin_id))
    await engine.dispose()


async def test_issue_requires_owner_and_returns_plaintext_codes(ctx):
    codes = await CourseCodeAdminService.issue(
        course_id=ctx.cid,
        quantity=2,
        owner_user_id=ctx.a_id,
        note="渠道补发",
        admin_id=ctx.admin_id,
    )
    assert len(codes) == 2
    async with ctx.factory() as db:
        for item in codes:
            ctx.created_codes.append(item["id"])
            row = await db.get(CourseCode, item["id"])
            assert row.owner_user_id == ctx.a_id
            assert row.status == "issued" and row.source == "admin"
            assert row.code_hash == course_code_crypto.hash_code(item["code"])
            assert course_code_crypto.decrypt_code(row.code_cipher) == item["code"]


async def test_owner_display_name_falls_back_in_order(ctx):
    await CourseCodeAdminService.issue(
        course_id=ctx.cid, quantity=1, owner_user_id=ctx.a_id, note=None, admin_id=ctx.admin_id
    )
    await CourseCodeAdminService.issue(
        course_id=ctx.cid, quantity=1, owner_user_id=ctx.b_id, note=None, admin_id=ctx.admin_id
    )
    await CourseCodeAdminService.issue(
        course_id=ctx.cid, quantity=1, owner_user_id=ctx.c_id, note=None, admin_id=ctx.admin_id
    )
    listing = await CourseCodeAdminService.list_codes(
        course_id=ctx.cid, status="issued", page=1, page_size=50
    )
    ctx.created_codes.extend(item["id"] for item in listing["items"])
    owners = {item["owner_user_id"]: item["owner"]["display_name"] for item in listing["items"]}
    assert owners[ctx.a_id] == "小花"          # 昵称
    assert owners[ctx.b_id] == "张三"          # 实名（verified）
    assert owners[ctx.c_id] == f"小程序用户 #{ctx.c_id}"  # 无任何资料


async def test_revoke_redeemed_code_also_reclaims_learning_access(ctx):
    [issued] = await CourseCodeAdminService.issue(
        course_id=ctx.cid, quantity=1, owner_user_id=ctx.a_id, note=None, admin_id=ctx.admin_id
    )
    ctx.created_codes.append(issued["id"])
    async with ctx.factory() as db, db.begin():
        row = await db.get(CourseCode, issued["id"])
        row.status = "redeemed"
        row.redeemed_at = datetime.now(timezone.utc)
        db.add(
            CourseEnrollment(
                user_id=ctx.a_id,
                course_id=ctx.cid,
                status="enrolled",
                learning_access=True,
                access_granted_at=datetime.now(timezone.utc),
                source="redeem",
                course_code_id=row.id,
            )
        )

    result = await CourseCodeAdminService.change_status(
        issued["id"], "revoke", "渠道回收", ctx.admin_id
    )
    assert result["status"] == "revoked"
    assert result["revoke_reason"] == "渠道回收"
    assert result["revoked_by"] == ctx.admin_id
    async with ctx.factory() as db:
        enrollment = await db.scalar(
            select(CourseEnrollment).where(CourseEnrollment.course_id == ctx.cid)
        )
        assert enrollment.status == "refunded"
        assert enrollment.learning_access is False
        assert enrollment.access_revoked_at is not None


async def test_search_users_matches_phone_prefix_nickname_and_real_name(ctx):
    # 测试库里可能有历史用户命中同一前缀，断言"包含且字段正确"而非排他。
    by_phone = await CourseCodeAdminService.search_users(
        keyword="13800001111", page=1, page_size=10
    )
    phone_hit = next(
        item for item in by_phone["items"] if item["user_id"] == ctx.a_id
    )
    assert phone_hit["phone"] == "138****1111"

    by_nick = await CourseCodeAdminService.search_users(
        keyword="小花", page=1, page_size=10
    )
    assert ctx.a_id in [item["user_id"] for item in by_nick["items"]]

    by_real = await CourseCodeAdminService.search_users(
        keyword="张三", page=1, page_size=10
    )
    assert ctx.b_id in [item["user_id"] for item in by_real["items"]]
