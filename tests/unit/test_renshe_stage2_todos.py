"""Unit and contract coverage for RSF-11 and RSF-13."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.admin.plans import preview_plan_impact
from app.main import app
from app.port.config import settings
from app.port.exceptions import ForbiddenException
from app.schemas.plan import PlanImpactResponse
from app.services.plan import PlanService
from app.services.renshe_application import RensheApplicationService
from app.services.renshe_batch import RensheBatchService
from app.services.renshe_cleanup import RensheCleanupService


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalars(self):
        return self


class _FakeDb:
    def __init__(self, *, scalars=(), rows=(), gets=()):
        self._scalars = list(scalars)
        self._rows = list(rows)
        self._gets = list(gets)
        self.executed = []
        self.added = []
        self.commit_count = 0

    async def scalar(self, statement):
        self.executed.append(statement)
        return self._scalars.pop(0)

    async def execute(self, statement):
        self.executed.append(statement)
        rows = self._rows.pop(0) if self._rows else []
        return _RowsResult(rows)

    async def get(self, _model, _object_id):
        return self._gets.pop(0)

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commit_count += 1


def _db_context(db):
    @asynccontextmanager
    async def context():
        yield db

    return context


def _application(
    application_id: int,
    status: str,
    *,
    plan_id: int = 7,
    user_id: int = 42,
    updated_at: datetime | None = None,
):
    now = datetime(2026, 8, 9, 8, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=application_id,
        plan_id=plan_id,
        user_id=user_id,
        current_version_id=None,
        status=status,
        draft_data={"contact_phone": "13800138000"} if status == "draft" else None,
        submitted_at=None if status == "draft" else now,
        frozen_at=None,
        freeze_reason=None,
        closed_at=now if status == "closed" else None,
        close_reason="refund_succeeded" if status == "closed" else None,
        created_at=now - timedelta(days=1),
        updated_at=updated_at or now,
    )


def _plan(*, plan_id: int = 7, status: str = "published"):
    now = datetime(2026, 8, 9, 8, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=plan_id,
        product_type="RS-ZY",
        name=f"人社批次-{plan_id}",
        status=status,
        apply_start=now - timedelta(days=1),
        apply_end=now + timedelta(days=2),
        exam_date=now + timedelta(days=10),
        exam_location="成都市测试中心",
        price_cents=50000,
    )


def _order(
    order_id: int,
    application_id: int,
    status: str,
    *,
    price: int = 50000,
):
    now = datetime(2026, 8, 9, 8, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=order_id,
        application_id=application_id,
        user_id=42,
        status=status,
        price=price,
        expires_at=now + timedelta(minutes=60) if status == "pending" else None,
        paid_at=now if status in {"paid", "completed", "refunded"} else None,
        closed_at=now if status in {"closed", "refunded"} else None,
    )


def _refund(
    refund_id: int,
    application_id: int,
    order_id: int,
    status: str,
):
    now = datetime(2026, 8, 9, 8, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=refund_id,
        application_id=application_id,
        order_id=order_id,
        user_id=42,
        request_kind="batch_cancel",
        amount_cents=50000,
        status=status,
        requested_at=now,
        due_at=now + timedelta(days=3),
        succeeded_at=now if status == "succeeded" else None,
    )


@pytest.mark.asyncio
async def test_cancel_impact_counts_latest_orders_and_existing_refunds_without_writes(
    monkeypatch,
):
    now = datetime(2026, 8, 9, 9, tzinfo=timezone.utc)
    applications = [
        _application(1, "draft"),
        _application(2, "pending_payment"),
        _application(3, "external_approved"),
        _application(4, "initial_rejected"),
        _application(5, "closed"),
    ]
    orders = [
        _order(20, 2, "pending"),
        _order(30, 3, "paid", price=50000),
        _order(40, 4, "completed", price=60000),
        _order(50, 5, "paid", price=70000),
    ]
    refunds = [_refund(90, 4, 40, "succeeded")]
    db = _FakeDb(rows=(applications, orders, refunds))
    monkeypatch.setattr(RensheBatchService, "_now", staticmethod(lambda: now))
    monkeypatch.setattr(settings, "RENSHE_CLEANUP_RETENTION_DAYS", 30)

    result = await RensheBatchService().preview_impact(
        db,
        plan=_plan(),
        action="cancel",
    )

    assert result.affected_application_count == 4
    assert result.pending_order_close_count == 1
    assert result.refund_candidate_count == 1
    assert result.refund_amount_cents == 50000
    assert result.blocking_application_count == 0
    assert result.can_execute is True
    assert result.cleanup_due_at == now + timedelta(days=30)
    assert db.added == []
    assert db.commit_count == 0
    assert all("FOR UPDATE" not in str(statement) for statement in db.executed)


@pytest.mark.asyncio
async def test_finalize_impact_groups_blockers_and_does_not_double_count_refunds(
    monkeypatch,
):
    now = datetime(2026, 8, 9, 10, tzinfo=timezone.utc)
    applications = [
        _application(1, "pending_payment"),
        _application(2, "pending_initial_review"),
        _application(3, "pending_external_review"),
        _application(4, "initial_rejected"),
        _application(5, "external_rejected"),
        _application(6, "external_approved"),
        _application(7, "closed"),
    ]
    orders = [
        _order(101, 1, "pending"),
        _order(102, 2, "paid"),
        _order(103, 3, "paid"),
        _order(104, 4, "paid", price=40000),
        _order(105, 5, "completed", price=60000),
        _order(106, 6, "paid", price=50000),
    ]
    refunds = [_refund(201, 5, 105, "processing")]
    db = _FakeDb(rows=(applications, orders, refunds))
    monkeypatch.setattr(RensheBatchService, "_now", staticmethod(lambda: now))
    monkeypatch.setattr(settings, "RENSHE_CLEANUP_RETENTION_DAYS", 30)

    result = await RensheBatchService().preview_impact(
        db,
        plan=_plan(status="registration_closed"),
        action="finalize",
    )

    assert result.affected_application_count == 6
    assert result.pending_order_close_count == 0
    assert result.refund_candidate_count == 1
    assert result.refund_amount_cents == 40000
    assert result.blocking_application_count == 3
    assert result.blocking_status_counts == {
        "pending_payment": 1,
        "pending_initial_review": 1,
        "pending_external_review": 1,
    }
    assert result.can_execute is False
    assert [blocker.code for blocker in result.blockers] == ["pending_applications"]
    assert db.added == []
    assert db.commit_count == 0


@pytest.mark.asyncio
async def test_impact_preview_reports_invalid_plan_state_instead_of_mutating():
    db = _FakeDb(rows=([],))

    result = await RensheBatchService().preview_impact(
        db,
        plan=_plan(status="finalized"),
        action="cancel",
    )

    assert result.can_execute is False
    assert [blocker.code for blocker in result.blockers] == ["plan_status"]
    assert db.added == []
    assert db.commit_count == 0


@pytest.mark.asyncio
async def test_cleanup_resume_reuses_the_same_configured_retention(monkeypatch):
    now = datetime(2026, 8, 9, 10, tzinfo=timezone.utc)
    run = SimpleNamespace(
        id=71,
        plan_id=7,
        status="paused",
        paused_reason="active_refunds",
        due_at=now,
        rebase_count=0,
    )
    plan = SimpleNamespace(id=7, cleanup_due_at=now)
    db = _FakeDb(scalars=(run,), gets=(plan,))
    monkeypatch.setattr(
        "app.services.renshe_cleanup.get_db_ctx",
        _db_context(db),
    )
    monkeypatch.setattr(RensheCleanupService, "_now", staticmethod(lambda: now))
    monkeypatch.setattr(settings, "RENSHE_CLEANUP_RETENTION_DAYS", 2.5)

    resumed = await RensheCleanupService(
        storage=SimpleNamespace()
    )._resume_one_paused_run()

    assert resumed is True
    assert run.status == "scheduled"
    assert run.paused_reason is None
    assert run.due_at == now + timedelta(days=2.5)
    assert plan.cleanup_due_at == run.due_at
    assert run.rebase_count == 1
    assert db.commit_count == 1


@pytest.mark.asyncio
async def test_finalize_impact_preview_requires_super_admin_but_cancel_does_not(
    monkeypatch,
):
    admin = SimpleNamespace(id=8, role="admin")
    with pytest.raises(ForbiddenException, match="仅超级管理员"):
        await preview_plan_impact(
            code="RS-ZY",
            plan_id=7,
            action="finalize",
            admin=admin,
        )

    now = datetime(2026, 8, 9, 10, tzinfo=timezone.utc)
    preview = PlanImpactResponse(
        action="cancel",
        plan_id=7,
        plan_name="人社批次-7",
        plan_status="published",
        affected_application_count=0,
        pending_order_close_count=0,
        refund_candidate_count=0,
        refund_amount_cents=0,
        blocking_application_count=0,
        previewed_at=now,
        cleanup_due_at=now + timedelta(days=30),
        can_execute=True,
    )
    service_call = AsyncMock(return_value=preview)
    monkeypatch.setattr(PlanService, "preview_impact", service_call)

    response = await preview_plan_impact(
        code="RS-ZY",
        plan_id=7,
        action="cancel",
        admin=admin,
    )

    assert response.data == preview
    service_call.assert_awaited_once_with(
        7,
        product_type="RS-ZY",
        action="cancel",
    )


@pytest.mark.asyncio
async def test_own_application_list_is_user_scoped_sorted_paginated_and_recoverable(
    monkeypatch,
):
    pending = _application(12, "pending_payment")
    closed = _application(11, "closed")
    plan = _plan()
    current_pending_order = _order(102, 12, "pending")
    db = _FakeDb(
        scalars=(2,),
        rows=(
            [(pending, plan), (closed, plan)],
            [
                _order(101, 12, "closed"),
                current_pending_order,
                _order(103, 11, "refunded"),
            ],
            [
                _refund(201, 11, 103, "rejected"),
                _refund(202, 11, 103, "succeeded"),
            ],
        ),
    )
    monkeypatch.setattr(
        "app.services.renshe_application.get_db_ctx",
        _db_context(db),
    )

    result = await RensheApplicationService().list_applications(
        42,
        plan_id=7,
        page=1,
        page_size=20,
    )

    assert result.total == 2
    assert result.page == 1
    assert result.page_size == 20
    assert [item.id for item in result.items] == [12, 11]
    assert result.items[0].status == "pending_payment"
    assert result.items[0].plan.id == 7
    assert result.items[0].plan.name == "人社批次-7"
    assert result.items[0].current_order.id == 102
    assert result.items[0].current_order.expires_at == current_pending_order.expires_at
    assert result.items[1].status == "closed"
    assert result.items[1].current_refund.id == 202
    assert result.items[1].current_refund.status == "succeeded"

    assert len(db.executed) == 4
    count_sql, page_sql, order_sql, refund_sql = map(str, db.executed)
    assert "renshe_application.user_id" in count_sql
    assert "renshe_application.user_id" in page_sql
    assert "plan.product_type" in count_sql
    assert "renshe_application.plan_id" in count_sql
    assert "renshe_application.status !=" not in page_sql
    assert "ORDER BY renshe_application.updated_at DESC" in page_sql
    assert "renshe_application.id DESC" in page_sql
    assert '"order".user_id' in order_sql
    assert "renshe_refund_request.user_id" in refund_sql
    for statement in db.executed:
        params = statement.compile().params
        if "user_id" in str(statement):
            assert 42 in params.values()


@pytest.mark.asyncio
async def test_own_application_list_supports_status_filter_and_skips_empty_fanout(
    monkeypatch,
):
    db = _FakeDb(scalars=(0,), rows=([],))
    monkeypatch.setattr(
        "app.services.renshe_application.get_db_ctx",
        _db_context(db),
    )

    result = await RensheApplicationService().list_applications(
        99,
        status="closed",
        page=3,
        page_size=10,
    )

    assert result.model_dump() == {
        "items": [],
        "total": 0,
        "page": 3,
        "page_size": 10,
    }
    assert len(db.executed) == 2
    assert all("renshe_application.user_id" in str(item) for item in db.executed)
    assert all("renshe_application.status" in str(item) for item in db.executed)
    assert all("closed" in item.compile().params.values() for item in db.executed)


def test_stage2_todo_routes_publish_bearer_filters_and_strict_response_schemas():
    app.openapi_schema = None
    schema = app.openapi()

    list_operation = schema["paths"]["/api/renshe/applications"]["get"]
    assert list_operation["security"] == [{"BearerAuth": []}]
    assert {item["name"] for item in list_operation["parameters"]} == {
        "plan_id",
        "status",
        "page",
        "page_size",
    }
    assert list_operation["x-contract-version"] == "2026-08-10"

    impact_operation = schema["paths"][
        "/admin/certifications/{code}/plans/{plan_id}/impact"
    ]["get"]
    assert impact_operation["security"] == [{"BearerAuth": []}]
    action = next(
        item for item in impact_operation["parameters"] if item["name"] == "action"
    )
    assert action["schema"]["enum"] == ["cancel", "finalize"]
    assert impact_operation["x-contract-version"] == "2026-08-10"

    schemas = schema["components"]["schemas"]
    assert {
        "plan",
        "current_order",
        "current_refund",
    } <= set(schemas["RensheApplicationListItem"]["properties"])
    assert {
        "affected_application_count",
        "pending_order_close_count",
        "refund_candidate_count",
        "refund_amount_cents",
        "blocking_status_counts",
        "cleanup_due_at",
        "can_execute",
        "blockers",
    } <= set(schemas["PlanImpactResponse"]["properties"])

    list_route = next(
        route
        for route in app.routes
        if getattr(route, "path", None) == "/api/renshe/applications"
        and "GET" in (getattr(route, "methods", None) or set())
    )
    assert {
        getattr(dependency.call, "__name__", "")
        for dependency in list_route.dependant.dependencies
    } == {"get_current_user"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    (
        "/api/renshe/applications",
        "/admin/certifications/RS-ZY/plans/7/impact?action=cancel",
    ),
)
async def test_stage2_todo_routes_reject_requests_without_bearer_token(path):
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path)

    assert response.status_code == 401
    assert response.json()["code"] == 40100
