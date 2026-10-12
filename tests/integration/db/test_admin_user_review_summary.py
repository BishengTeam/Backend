from datetime import datetime, timezone
from contextlib import asynccontextmanager
from uuid import uuid4
import os

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domain.user.src.index import User, UserRealname, UserStudent
from app.schemas.admin import AdminUserFilter
from app.services.admin_user import AdminUserService


pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


@pytest.fixture
async def db_session():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with factory() as session:
            yield session
        await transaction.rollback()
    await engine.dispose()


async def test_review_summary_and_combined_filters(db_session, monkeypatch):
    @asynccontextmanager
    async def db_ctx():
        yield db_session

    monkeypatch.setattr("app.services.admin_user.get_db_ctx", db_ctx)
    now = datetime.now(timezone.utc)
    openid_suffix = uuid4().hex[:10]

    both_pending = User(openid=f"review-both-{openid_suffix}", phone="13800000001")
    identity_pending = User(openid=f"review-identity-{openid_suffix}", phone="13800000002")
    approved = User(openid=f"review-approved-{openid_suffix}", phone="13800000003")
    rejected = User(openid=f"review-rejected-{openid_suffix}", phone="13800000004")
    db_session.add_all([both_pending, identity_pending, approved, rejected])
    await db_session.flush()

    def realname(user_id: int, status: str, updated_at: datetime) -> UserRealname:
        return UserRealname(
            user_id=user_id,
            user_type="student",
            real_name="测试用户",
            id_card_number=f"110101199001{user_id:06d}"[:18],
            status=status,
            updated_at=updated_at,
        )

    def student(user_id: int, status: str, updated_at: datetime) -> UserStudent:
        return UserStudent(
            user_id=user_id,
            education="本科",
            school="测试大学",
            major="计算机",
            student_card_oss=f"test/{user_id}/student.jpg",
            status=status,
            updated_at=updated_at,
        )

    db_session.add_all([
        realname(both_pending.id, "pending", now.replace(minute=1)),
        student(both_pending.id, "pending", now.replace(minute=1)),
        realname(identity_pending.id, "pending", now.replace(minute=2)),
        student(identity_pending.id, "verified", now.replace(minute=2)),
        realname(approved.id, "verified", now.replace(minute=3)),
        student(approved.id, "verified", now.replace(minute=3)),
        realname(rejected.id, "verified", now.replace(minute=4)),
        student(rejected.id, "rejected", now.replace(minute=4)),
    ])
    await db_session.flush()

    service = AdminUserService()
    summary = await service.get_review_summary(latest_limit=10)
    assert summary.identity_pending_count >= 2
    assert summary.student_pending_count >= 1
    latest_ids = [item.user_id for item in summary.latest_pending]
    assert identity_pending.id in latest_ids
    assert both_pending.id in latest_ids

    pending = await service.list_users(
        AdminUserFilter(review_status="pending"), 1, 100
    )
    pending_ids = {item.id for item in pending.items}
    assert {both_pending.id, identity_pending.id}.issubset(pending_ids)
    assert pending.items[0].id == identity_pending.id

    identity = await service.list_users(
        AdminUserFilter(identity_status="pending"), 1, 100
    )
    assert {both_pending.id, identity_pending.id}.issubset(
        item.id for item in identity.items
    )

    student_pending = await service.list_users(
        AdminUserFilter(student_status="pending"), 1, 100
    )
    assert both_pending.id in {item.id for item in student_pending.items}
    assert identity_pending.id not in {item.id for item in student_pending.items}

    approved_rows = await service.list_users(
        AdminUserFilter(review_status="approved"), 1, 100
    )
    assert approved.id in {item.id for item in approved_rows.items}
    assert both_pending.id not in {item.id for item in approved_rows.items}

    rejected_rows = await service.list_users(
        AdminUserFilter(review_status="rejected"), 1, 100
    )
    assert rejected.id in {item.id for item in rejected_rows.items}
