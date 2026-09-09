"""Integration flow: template versioning, acceptance snapshot, realname gate."""

import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


@pytest.fixture
async def context(monkeypatch):
    if not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("TEST_DATABASE_URL not configured")
    url = os.environ["TEST_DATABASE_URL"]
    engine = create_async_engine(url, pool_size=4, max_overflow=4)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"agrflow_{uuid4().hex[:12]}"

    @asynccontextmanager
    async def db_ctx():
        async with factory() as session:
            yield session

    import app.services.agreement_template as user_module
    import app.services.admin_agreement_template as admin_module

    monkeypatch.setattr(user_module, "get_db_ctx", db_ctx)
    monkeypatch.setattr(admin_module, "get_db_ctx", db_ctx)
    yield SimpleNamespace(factory=factory, db_ctx=db_ctx, prefix=prefix)

    from app.domain.content.src.index import AgreementAcceptance, AgreementTemplate
    from app.domain.user.src.index import User

    async with factory() as db:
        template_ids = select(AgreementTemplate.id).where(
            AgreementTemplate.title.like(f"{prefix}%")
        )
        user_ids = select(User.id).where(User.openid.like(f"{prefix}%"))
        await db.execute(
            delete(AgreementAcceptance).where(
                AgreementAcceptance.template_id.in_(template_ids)
            )
        )
        await db.execute(
            delete(AgreementTemplate).where(
                AgreementTemplate.title.like(f"{prefix}%")
            )
        )
        await db.execute(delete(User).where(User.openid.like(f"{prefix}%")))
        await db.commit()
    await engine.dispose()


async def _create_user(factory, prefix: str) -> int:
    from app.domain.user.src.index import User

    async with factory() as db:
        user = User(openid=f"{prefix}_openid")
        db.add(user)
        await db.commit()
        return user.id


async def test_template_versioning_and_acceptance_flow(context):
    from app.domain.content.src.index import AgreementAcceptance, AgreementTemplate
    from app.port.exceptions import BusinessException, NotFoundException
    from app.schemas.admin_agreement_template import (
        AdminAgreementTemplateCreate,
        AdminAgreementTemplateUpdate,
    )
    from app.services.admin_agreement_template import AdminAgreementTemplateService
    from app.services.agreement_template import (
        AgreementTemplateService,
        ensure_accepted,
    )

    admin = AdminAgreementTemplateService()
    user_service = AgreementTemplateService()
    title_v1 = f"{context.prefix} 用户服务协议"

    created = await admin.create(
        AdminAgreementTemplateCreate(
            type="user_terms", title=title_v1, content=f"{context.prefix} v1 正文"
        )
    )
    assert created.version == 1
    assert created.status == "active"

    active = await user_service.get_active("user_terms")
    assert active.version == 1
    assert active.content == f"{context.prefix} v1 正文"

    user_id = await _create_user(context.factory, context.prefix)

    # Accept v1 -> snapshot recorded
    accepted = await user_service.accept(
        user_id,
        __import__(
            "app.schemas.agreement_template", fromlist=["AgreementAcceptRequest"]
        ).AgreementAcceptRequest(
            items=[
                __import__(
                    "app.schemas.agreement_template", fromlist=["AgreementAcceptItem"]
                ).AgreementAcceptItem(type="user_terms", version=1)
            ]
        ),
    )
    assert accepted[0].type == "user_terms"

    async with context.db_ctx() as db:
        row = await db.scalar(select(AgreementAcceptance))
        assert row is not None
        assert row.content_snapshot == f"{context.prefix} v1 正文"
        assert row.version == 1

    # Edit -> v2 active, v1 archived; get_active returns v2
    updated = await admin.update(
        created.id,
        AdminAgreementTemplateUpdate(
            title=f"{context.prefix} 用户服务协议 v2",
            content=f"{context.prefix} v2 正文",
        ),
    )
    assert updated.version == 2
    assert updated.status == "active"
    active2 = await user_service.get_active("user_terms")
    assert active2.version == 2
    async with context.db_ctx() as db:
        old = await db.get(AgreementTemplate, created.id)
        assert old.status == "archived"

    # Old acceptance keeps its snapshot (immutable evidence)
    async with context.db_ctx() as db:
        row = await db.scalar(select(AgreementAcceptance))
        assert row.content_snapshot == f"{context.prefix} v1 正文"

    # Gate: user accepted v1 but active is v2 -> must raise
    with pytest.raises(BusinessException):
        async with context.db_ctx() as db:
            await ensure_accepted(db, user_id, "user_terms", message="请先阅读并同意最新协议")

    # Version mismatch on accept -> business error
    from app.schemas.agreement_template import AgreementAcceptItem, AgreementAcceptRequest

    with pytest.raises(BusinessException):
        await user_service.accept(
            user_id,
            AgreementAcceptRequest(
                items=[AgreementAcceptItem(type="user_terms", version=1)]
            ),
        )


async def test_identity_auth_gate_requires_acceptance(context):
    from app.port.exceptions import BusinessException
    from app.schemas.admin_agreement_template import AdminAgreementTemplateCreate
    from app.services.admin_agreement_template import AdminAgreementTemplateService
    from app.services.agreement_template import (
        AgreementTemplateService,
        ensure_accepted,
    )
    from app.schemas.agreement_template import AgreementAcceptItem, AgreementAcceptRequest

    admin = AdminAgreementTemplateService()
    user_service = AgreementTemplateService()
    title = f"{context.prefix} 实名授权协议"

    # No template configured -> gate open (data-driven rollout)
    user_id = await _create_user(context.factory, context.prefix)
    async with context.db_ctx() as db:
        await ensure_accepted(db, user_id, "identity_auth", message="blocked")

    await admin.create(
        AdminAgreementTemplateCreate(
            type="identity_auth", title=title, content=f"{context.prefix} 授权正文"
        )
    )

    # Template active but not accepted -> blocked
    with pytest.raises(BusinessException):
        async with context.db_ctx() as db:
            await ensure_accepted(
                db, user_id, "identity_auth", message="请先阅读并同意实名信息处理授权协议"
            )

    # Accept -> gate passes
    await user_service.accept(
        user_id,
        AgreementAcceptRequest(
            items=[AgreementAcceptItem(type="identity_auth", version=1)]
        ),
    )
    async with context.db_ctx() as db:
        await ensure_accepted(db, user_id, "identity_auth", message="blocked")
