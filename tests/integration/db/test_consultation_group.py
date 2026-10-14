import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.plan.src.index import Plan
from app.port.exceptions import ConflictException
from app.schemas.consultation_group import ConsultationGroupUpdate
from app.services.consultation_group import ConsultationGroupService

pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


@pytest.fixture
async def context(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(url, pool_size=2, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def db_ctx():
        async with factory() as session:
            yield session

    monkeypatch.setattr("app.services.consultation_group.get_db_ctx", db_ctx)
    async with factory() as db:
        plan = Plan(
            product_type="RS-ZY",
            name=f"consult_{uuid4().hex}",
            status="published",
            capacity=0,
            price_cents=50000,
            contact_name="测试联系人",
            contact_phone="13800138000",
        )
        db.add(plan)
        await db.commit()
        try:
            yield plan, db_ctx
        finally:
            await db.delete(plan)
            await db.commit()
    await engine.dispose()


async def test_consultation_group_crud_visibility_and_version(context):
    plan, db_ctx = context
    service = ConsultationGroupService()

    empty = await service.get_admin(plan.id)
    assert empty.status == "not_configured"
    assert empty.version == 0

    created = await service.update(
        plan_id=plan.id,
        admin_id=9,
        data=ConsultationGroupUpdate(
            qrcode_url="/api/media/consultation.png",
            join_instructions="请备注姓名",
            expires_at=None,
            version=0,
        ),
    )
    assert created.version == 1
    assert created.status == "configured"

    async with db_ctx() as db:
        visible = await service.get_user_group(
            db,
            plan=plan,
            registration_status="pending_initial_review",
        )
        hidden_before_payment = await service.get_user_group(
            db,
            plan=plan,
            registration_status="pending_payment",
        )
    assert visible is not None
    assert visible.qrcode_url == "/api/media/consultation.png"
    assert visible.contact_name == "测试联系人"
    assert hidden_before_payment is None

    with pytest.raises(ConflictException):
        await service.update(
            plan_id=plan.id,
            admin_id=9,
            data=ConsultationGroupUpdate(
                qrcode_url="/api/media/new.png",
                version=0,
            ),
        )

    expired = await service.update(
        plan_id=plan.id,
        admin_id=9,
        data=ConsultationGroupUpdate(
            qrcode_url="/api/media/consultation.png",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            version=1,
        ),
    )
    assert expired.status == "expired"

    async with db_ctx() as db:
        assert await service.get_user_group(
            db,
            plan=plan,
            registration_status="pending_initial_review",
        ) is None

    removed = await service.update(
        plan_id=plan.id,
        admin_id=9,
        data=ConsultationGroupUpdate(qrcode_url=None, version=2),
    )
    assert removed.status == "not_configured"
