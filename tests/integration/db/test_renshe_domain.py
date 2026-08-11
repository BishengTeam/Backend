"""Real PostgreSQL evidence for the human-resources domain (BE-01/BE-17).

The suite is deliberately skipped unless both explicit test URLs are supplied
by the operator.  It must never connect to the developer database by accident.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domain.order.src.index import Order
from app.domain.plan.src.index import Plan
from app.domain.renshe.src.index import (
    RensheApplication,
    RensheAuditLog,
    RensheCleanupRun,
    RensheRefundRequest,
)
from app.domain.user.src.index import User
from app.services.renshe_application import RensheApplicationService
from app.services.renshe_batch import RensheBatchService
from app.integrations.wechat_pay import WechatPayRefund
from app.port.config import settings
from app.services.renshe_audit import RensheAuditQueryService
from app.services.renshe_refund import RensheRefundService


pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


def _url() -> str:
    value = os.getenv("TEST_DATABASE_URL")
    if not value:
        pytest.skip("TEST_DATABASE_URL is not set")
    if not value.startswith("postgresql+asyncpg://"):
        raise AssertionError("TEST_DATABASE_URL must use the asyncpg driver")
    return value


@pytest.fixture
async def db_session() -> AsyncSession:
    engine = create_async_engine(_url(), pool_size=2, max_overflow=0)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection, class_=AsyncSession, expire_on_commit=False
        )
        async with factory() as session:
            try:
                yield session
            finally:
                await session.rollback()
        await transaction.rollback()
    await engine.dispose()


async def test_renshe_tables_indexes_and_constraints_exist(db_session: AsyncSession) -> None:
    def inspect_schema(sync_connection):
        inspector = inspect(sync_connection)
        tables = set(inspector.get_table_names())
        return {
            "tables": tables,
            "application_checks": {
                item["name"] for item in inspector.get_check_constraints("renshe_application")
            },
            "material_indexes": {
                item["name"] for item in inspector.get_indexes("renshe_material")
            },
            "volume_checks": {
                item["name"] for item in inspector.get_check_constraints("renshe_export_volume")
            },
            "refund_indexes": {
                item["name"] for item in inspector.get_indexes("renshe_refund_request")
            },
            "audit_indexes": {
                item["name"] for item in inspector.get_indexes("renshe_audit_log")
            },
        }

    schema = await db_session.connection()
    observed = await schema.run_sync(inspect_schema)
    assert {
        "renshe_application",
        "renshe_application_version",
        "renshe_material",
        "renshe_review",
        "renshe_review_correction",
        "renshe_refund_request",
        "renshe_export_job",
        "renshe_export_volume",
        "renshe_export_item",
        "renshe_cleanup_run",
        "renshe_audit_log",
    } <= observed["tables"]
    assert "ck_renshe_application_status" in observed["application_checks"]
    assert "uq_renshe_material_version_kind" in observed["material_indexes"]
    assert "ck_renshe_export_volume_max_size" in observed["volume_checks"]
    assert "ix_renshe_refund_status_updated" in observed["refund_indexes"]
    assert "ix_renshe_audit_application_created" in observed["audit_indexes"]
    assert "ix_renshe_audit_result_created" in observed["audit_indexes"]


async def test_audit_persistence_redacts_pii_at_sqlalchemy_boundary(
    db_session: AsyncSession,
) -> None:
    # Use a unique user/plan so this test can run against a shared disposable
    # database while the surrounding transaction remains fully rolled back.
    suffix = uuid4().hex[:12]
    user = User(openid=f"rsh_test_{suffix}")
    plan = Plan(
        product_type="RS-ZY",
        name=f"rsh_test_{suffix}",
        status="draft",
        capacity=0,
        price_cents=50000,
    )
    db_session.add_all([user, plan])
    await db_session.flush()
    application = RensheApplication(user_id=user.id, plan_id=plan.id, status="draft")
    db_session.add(application)
    await db_session.flush()
    audit = RensheAuditLog(
        actor_type="user",
        actor_id=user.id,
        action="test.audit",
        object_type="application",
        object_id=application.id,
        summary={
            "id_card_number": "11010519491231002X",
            "contact_phone": "13800138000",
            "safe_count": 2,
        },
        result="succeeded",
    )
    db_session.add(audit)
    await db_session.flush()
    assert audit.summary == {
        "id_card_number": "[REDACTED]",
        "contact_phone": "[REDACTED]",
        "safe_count": 2,
    }


async def test_own_application_list_recovers_only_current_user_records(
    db_session: AsyncSession,
    monkeypatch,
) -> None:
    suffix = uuid4().hex[:12]
    current_user = User(openid=f"rsh_list_current_{suffix}")
    other_user = User(openid=f"rsh_list_other_{suffix}")
    plan = Plan(
        product_type="RS-ZY",
        name=f"rsh_list_plan_{suffix}",
        status="published",
        capacity=0,
        price_cents=50000,
    )
    db_session.add_all([current_user, other_user, plan])
    await db_session.flush()

    pending = RensheApplication(
        user_id=current_user.id,
        plan_id=plan.id,
        status="pending_payment",
    )
    closed = RensheApplication(
        user_id=current_user.id,
        plan_id=plan.id,
        status="closed",
        closed_at=datetime.now(timezone.utc),
        close_reason="refund_succeeded",
    )
    other = RensheApplication(
        user_id=other_user.id,
        plan_id=plan.id,
        status="draft",
    )
    db_session.add_all([pending, closed, other])
    await db_session.flush()

    now = datetime.now(timezone.utc)
    pending_order = Order(
        user_id=current_user.id,
        order_kind="certification",
        product_type="RS-ZY",
        plan_id=plan.id,
        application_id=pending.id,
        price=50000,
        status="pending",
        out_trade_no=f"RS_LIST_PENDING_{suffix}",
        expires_at=now + timedelta(minutes=60),
    )
    refunded_order = Order(
        user_id=current_user.id,
        order_kind="certification",
        product_type="RS-ZY",
        plan_id=plan.id,
        application_id=closed.id,
        price=50000,
        status="refunded",
        out_trade_no=f"RS_LIST_REFUNDED_{suffix}",
        closed_at=now,
    )
    db_session.add_all([pending_order, refunded_order])
    await db_session.flush()
    refund = RensheRefundRequest(
        application_id=closed.id,
        order_id=refunded_order.id,
        user_id=current_user.id,
        request_kind="normal",
        reason_code="user_requested",
        amount_cents=50000,
        status="succeeded",
        previous_application_status="external_approved",
        requested_at=now,
        due_at=now + timedelta(days=3),
        succeeded_at=now,
    )
    db_session.add(refund)
    await db_session.flush()

    @asynccontextmanager
    async def current_transaction():
        yield db_session

    monkeypatch.setattr(
        "app.services.renshe_application.get_db_ctx",
        current_transaction,
    )
    result = await RensheApplicationService().list_applications(
        current_user.id,
        plan_id=plan.id,
        page=1,
        page_size=20,
    )

    assert result.total == 2
    assert {item.id for item in result.items} == {pending.id, closed.id}
    assert all(item.user_id == current_user.id for item in result.items)
    pending_item = next(item for item in result.items if item.id == pending.id)
    closed_item = next(item for item in result.items if item.id == closed.id)
    assert pending_item.current_order.id == pending_order.id
    assert pending_item.current_order.expires_at == pending_order.expires_at
    assert closed_item.current_refund.id == refund.id
    assert closed_item.current_refund.status == "succeeded"


async def test_batch_impact_preview_is_read_only_on_postgresql(
    db_session: AsyncSession,
) -> None:
    suffix = uuid4().hex[:12]
    pending_user = User(openid=f"rsh_impact_pending_{suffix}")
    rejected_user = User(openid=f"rsh_impact_rejected_{suffix}")
    plan = Plan(
        product_type="RS-ZY",
        name=f"rsh_impact_plan_{suffix}",
        status="registration_closed",
        capacity=0,
        price_cents=50000,
    )
    db_session.add_all([pending_user, rejected_user, plan])
    await db_session.flush()
    pending = RensheApplication(
        user_id=pending_user.id,
        plan_id=plan.id,
        status="pending_payment",
    )
    rejected = RensheApplication(
        user_id=rejected_user.id,
        plan_id=plan.id,
        status="initial_rejected",
    )
    db_session.add_all([pending, rejected])
    await db_session.flush()
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            Order(
                user_id=pending_user.id,
                order_kind="certification",
                product_type="RS-ZY",
                plan_id=plan.id,
                application_id=pending.id,
                price=50000,
                status="pending",
                out_trade_no=f"RS_IMPACT_PENDING_{suffix}",
                expires_at=now + timedelta(minutes=60),
            ),
            Order(
                user_id=rejected_user.id,
                order_kind="certification",
                product_type="RS-ZY",
                plan_id=plan.id,
                application_id=rejected.id,
                price=50000,
                status="paid",
                out_trade_no=f"RS_IMPACT_PAID_{suffix}",
                paid_at=now,
            ),
        ]
    )
    await db_session.flush()
    assert not db_session.new
    assert not db_session.dirty

    result = await RensheBatchService().preview_impact(
        db_session,
        plan=plan,
        action="finalize",
    )

    assert result.affected_application_count == 2
    assert result.blocking_status_counts == {"pending_payment": 1}
    assert result.refund_candidate_count == 1
    assert result.refund_amount_cents == 50000
    assert result.can_execute is False
    assert not db_session.new
    assert not db_session.dirty


async def test_refund_success_is_atomic_idempotent_and_rebases_cleanup_on_postgresql(
    db_session: AsyncSession,
    monkeypatch,
) -> None:
    suffix = uuid4().hex[:12]
    now = datetime.now(timezone.utc)
    user = User(openid=f"rsh_refund_{suffix}")
    plan = Plan(
        product_type="RS-ZY",
        name=f"rsh_refund_plan_{suffix}",
        status="finalized",
        capacity=150,
        price_cents=1,
        finalized_at=now,
        cleanup_due_at=now,
    )
    db_session.add_all([user, plan])
    await db_session.flush()
    application = RensheApplication(
        user_id=user.id,
        plan_id=plan.id,
        status="external_approved",
        frozen_at=now,
        freeze_reason="refund:pending",
    )
    db_session.add(application)
    await db_session.flush()
    order = Order(
        user_id=user.id,
        order_kind="certification",
        product_type="RS-ZY",
        plan_id=plan.id,
        application_id=application.id,
        price=1,
        status="paid",
        out_trade_no=f"RSRFLOW{suffix}",
        transaction_id=f"4200{suffix}",
        paid_at=now - timedelta(hours=1),
    )
    db_session.add(order)
    await db_session.flush()
    refund = RensheRefundRequest(
        application_id=application.id,
        order_id=order.id,
        user_id=user.id,
        request_kind="exception",
        reason_code="uat",
        amount_cents=1,
        status="processing",
        previous_application_status="external_approved",
        requested_at=now - timedelta(days=1),
        due_at=now + timedelta(days=2),
        out_refund_no=f"RF{refund_id_suffix(suffix)}",
        wechat_refund_id=f"WXRF{suffix}",
        processing_at=now - timedelta(minutes=5),
    )
    cleanup = RensheCleanupRun(
        plan_id=plan.id,
        run_no=1,
        status="paused",
        due_at=now,
        paused_reason="active_refunds",
    )
    db_session.add_all([refund, cleanup])
    await db_session.flush()
    application.freeze_reason = f"refund:{refund.id}"

    @asynccontextmanager
    async def current_transaction():
        yield db_session

    monkeypatch.setattr(
        "app.services.renshe_refund.get_db_ctx",
        current_transaction,
    )
    monkeypatch.setattr(RensheRefundService, "_now", staticmethod(lambda: now))
    service = RensheRefundService(
        SimpleNamespace(mch_id="1900000001")
    )
    provider = WechatPayRefund(
        out_trade_no=order.out_trade_no,
        transaction_id=order.transaction_id,
        out_refund_no=refund.out_refund_no,
        refund_id=refund.wechat_refund_id,
        status="SUCCESS",
        amount_total=1,
        amount_refund=1,
        currency="CNY",
        mchid="1900000001",
        success_time=now,
    )

    first = await service._apply_provider_result(
        provider,
        source="query",
        expected_refund_id=refund.id,
        allow_success=True,
    )
    second = await service._apply_provider_result(
        provider,
        source="notification",
        expected_refund_id=None,
        allow_success=True,
    )

    assert first.processed is True
    assert second.processed is False
    assert refund.status == "succeeded"
    assert order.status == "refunded"
    assert application.status == "closed"
    assert application.freeze_reason is None
    assert cleanup.status == "scheduled"
    assert cleanup.due_at == now + timedelta(
        days=settings.RENSHE_CLEANUP_RETENTION_DAYS
    )
    assert plan.cleanup_due_at == cleanup.due_at


async def test_audit_query_filters_batch_and_appends_access_audit_on_postgresql(
    db_session: AsyncSession,
    monkeypatch,
) -> None:
    suffix = uuid4().hex[:12]
    user = User(openid=f"rsh_audit_query_{suffix}")
    plan = Plan(
        product_type="RS-ZY",
        name=f"rsh_audit_query_plan_{suffix}",
        status="draft",
        capacity=0,
        price_cents=50000,
    )
    db_session.add_all([user, plan])
    await db_session.flush()
    application = RensheApplication(
        user_id=user.id,
        plan_id=plan.id,
        status="draft",
    )
    db_session.add(application)
    await db_session.flush()
    event = RensheAuditLog(
        actor_type="admin",
        actor_id=900001,
        action="test.batch.audit",
        object_type="application",
        object_id=application.id,
        application_id=application.id,
        result="succeeded",
        summary={"contact_phone": "13800138000", "safe": True},
    )
    db_session.add(event)
    await db_session.flush()

    @asynccontextmanager
    async def current_transaction():
        yield db_session

    monkeypatch.setattr(
        "app.services.renshe_audit.get_db_ctx",
        current_transaction,
    )
    result = await RensheAuditQueryService().list_logs(
        admin_id=900001,
        ip_address="192.0.2.1",
        plan_id=plan.id,
        application_id=application.id,
        actor_admin_id=900001,
        action="test.batch.audit",
        result="succeeded",
        started_at=None,
        ended_at=None,
        page=1,
        page_size=20,
    )

    assert result.total == 1
    assert result.items[0].id == event.id
    assert result.items[0].summary["contact_phone"] == "[REDACTED]"
    access_count = await db_session.scalar(
        select(func.count())
        .select_from(RensheAuditLog)
        .where(
            RensheAuditLog.actor_type == "admin",
            RensheAuditLog.actor_id == 900001,
            RensheAuditLog.action == "audit.query",
        )
    )
    assert access_count == 1


def refund_id_suffix(value: str) -> str:
    """Return a deterministic legal merchant suffix for integration fixtures."""

    return value[:30]
