"""Stage-four refund, cleanup, audit, concurrency, and worker coverage."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

import app.services.renshe_audit as audit_module
import app.services.renshe_refund as refund_module
import app.services.renshe_refund_reconciliation as refund_worker_module
from app.domain.renshe.src.index import RensheAuditLog
from app.integrations.wechat_pay import WechatPayRefund, WechatPayResultUnknownError
from app.main import app
from app.port.config import settings
from app.port.exceptions import ConflictException
from app.schemas.renshe import RensheRefundDecision, RensheRefundResponse
from app.services.renshe_audit import RensheAuditQueryService
from app.services.renshe_refund import (
    PreparedRensheRefund,
    RensheRefundApplyResult,
    RensheRefundService,
)
from app.services.renshe_refund_reconciliation import (
    RensheRefundReconciliationService,
)


NOW = datetime(2026, 8, 10, 4, 0, tzinfo=timezone.utc)


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def scalars(self):
        return self


class _Session:
    def __init__(self, *, scalars=(), rows=()):
        self.scalar_values = list(scalars)
        self.row_values = list(rows)
        self.added = []
        self.statements = []
        self.commits = 0
        self.flushes = 0

    async def scalar(self, statement):
        self.statements.append(statement)
        return self.scalar_values.pop(0)

    async def execute(self, statement):
        self.statements.append(statement)
        return _Rows(self.row_values.pop(0) if self.row_values else [])

    async def get(self, _model, _identifier):
        return self.scalar_values.pop(0)

    async def scalars(self, statement):
        self.statements.append(statement)
        return _Rows(self.row_values.pop(0) if self.row_values else [])

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        self.commits += 1

    async def refresh(self, _value):
        return None


def _install_sessions(monkeypatch, module, sessions, *, serialized=False):
    queue = list(sessions)
    lock = asyncio.Lock()

    @asynccontextmanager
    async def context():
        if serialized:
            await lock.acquire()
        try:
            yield queue.pop(0)
        finally:
            if serialized:
                lock.release()

    monkeypatch.setattr(module, "get_db_ctx", context)


def _refund(**overrides):
    values = {
        "id": 1,
        "application_id": 11,
        "order_id": 21,
        "user_id": 31,
        "request_kind": "normal",
        "reason_code": "personal",
        "reason_detail": None,
        "amount_cents": 12800,
        "status": "requested",
        "previous_application_status": "pending_external_review",
        "requested_at": NOW - timedelta(days=1),
        "due_at": NOW + timedelta(days=2),
        "approved_by_admin_id": None,
        "decided_at": None,
        "rejection_reason": None,
        "out_refund_no": None,
        "wechat_refund_id": None,
        "processing_at": None,
        "succeeded_at": None,
        "last_error": None,
        "retry_count": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _order(**overrides):
    values = {
        "id": 21,
        "application_id": 11,
        "user_id": 31,
        "status": "paid",
        "price": 12800,
        "out_trade_no": "order-1001",
        "transaction_id": "transaction-1001",
        "paid_at": NOW - timedelta(hours=1),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _application(**overrides):
    values = {
        "id": 11,
        "plan_id": 41,
        "user_id": 31,
        "current_version_id": 51,
        "status": "pending_external_review",
        "frozen_at": NOW - timedelta(days=1),
        "freeze_reason": "refund:1",
        "closed_at": None,
        "close_reason": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _provider_refund(**overrides):
    values = {
        "out_trade_no": "order-1001",
        "transaction_id": "transaction-1001",
        "out_refund_no": "RSRF0000000000000000000000000001",
        "refund_id": "wechat-refund-1001",
        "status": "SUCCESS",
        "amount_total": 12800,
        "amount_refund": 12800,
        "currency": "CNY",
        "mchid": "1900000001",
        "success_time": NOW,
    }
    values.update(overrides)
    return WechatPayRefund(**values)


def _wechat(**overrides):
    values = {
        "mch_id": "1900000001",
        "refund_notify_url": "https://pay.test/api/payment/refund-callback",
        "ensure_refund_configured": lambda: None,
        "refund": AsyncMock(),
        "query_refund": AsyncMock(),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_approval_persists_one_stable_refund_number_before_provider_io(
    monkeypatch,
) -> None:
    refund = _refund()
    order = _order()
    application = _application()
    first = _Session(scalars=(refund, order, application))
    second = _Session(scalars=(refund, order, application))
    _install_sessions(monkeypatch, refund_module, (first, second))
    monkeypatch.setattr(RensheRefundService, "_now", staticmethod(lambda: NOW))
    service = RensheRefundService(_wechat())

    prepared = await service._prepare_submission(
        refund_id=1,
        actor_type="admin",
        actor_id=9,
        system_only=False,
    )
    assert isinstance(prepared, PreparedRensheRefund)
    stable_number = prepared.out_refund_no
    assert stable_number == "RSRF0000000000000000000000000001"
    assert len(stable_number) == 32
    assert refund.status == "approved"
    assert first.commits == 1

    refund.status = "failed"
    retried = await service._prepare_submission(
        refund_id=1,
        actor_type="admin",
        actor_id=9,
        system_only=False,
    )
    assert isinstance(retried, PreparedRensheRefund)
    assert retried.out_refund_no == stable_number
    assert refund.retry_count == 1
    assert second.commits == 1


@pytest.mark.asyncio
async def test_submit_success_response_stays_processing_until_query_or_callback() -> None:
    provider_payload = {
        "out_trade_no": "order-1001",
        "transaction_id": "transaction-1001",
        "out_refund_no": "RSRF0000000000000000000000000001",
        "refund_id": "wechat-refund-1001",
        "status": "SUCCESS",
        "success_time": NOW.isoformat(),
        "amount": {"total": 12800, "refund": 12800, "currency": "CNY"},
    }
    client = _wechat(refund=AsyncMock(return_value=provider_payload))
    service = RensheRefundService(client)
    response = RensheRefundResponse.model_validate(
        _refund(
            status="processing",
            out_refund_no="RSRF0000000000000000000000000001",
            wechat_refund_id="wechat-refund-1001",
            processing_at=NOW,
        )
    )
    service._apply_provider_result = AsyncMock(
        return_value=RensheRefundApplyResult(refund=response, processed=True)
    )
    prepared = PreparedRensheRefund(
        refund_id=1,
        out_refund_no="RSRF0000000000000000000000000001",
        out_trade_no="order-1001",
        transaction_id="transaction-1001",
        amount_cents=12800,
    )

    result = await service._submit_prepared(prepared)

    assert result.status == "processing"
    client.refund.assert_awaited_once_with(
        out_trade_no="order-1001",
        out_refund_no=prepared.out_refund_no,
        amount_total=12800,
        refund_amount=12800,
        reason="人社报名全额退款",
        notify_url=client.refund_notify_url,
    )
    assert service._apply_provider_result.await_args.kwargs["allow_success"] is False


@pytest.mark.asyncio
async def test_unknown_submit_result_is_kept_queryable_under_same_number(
    monkeypatch,
) -> None:
    refund = _refund(
        status="approved",
        out_refund_no="RSRF0000000000000000000000000001",
    )
    application = _application()
    session = _Session(scalars=(refund, application))
    _install_sessions(monkeypatch, refund_module, (session,))
    client = _wechat(
        refund=AsyncMock(
            side_effect=WechatPayResultUnknownError("timeout; result unknown")
        )
    )
    service = RensheRefundService(client)

    result = await service._submit_prepared(
        PreparedRensheRefund(
            refund_id=1,
            out_refund_no=refund.out_refund_no,
            out_trade_no="order-1001",
            transaction_id="transaction-1001",
            amount_cents=12800,
        )
    )

    assert result.status == "processing"
    assert refund.out_refund_no == "RSRF0000000000000000000000000001"
    assert refund.processing_at is not None
    assert session.commits == 1


@pytest.mark.asyncio
async def test_query_after_unknown_submit_persists_provider_metadata(
    monkeypatch,
) -> None:
    refund = _refund(
        status="processing",
        out_refund_no="RSRF0000000000000000000000000001",
        processing_at=NOW - timedelta(minutes=5),
        last_error="WechatPayResultUnknownError",
    )
    session = _Session(scalars=(refund, _order(), _application(), None))
    _install_sessions(monkeypatch, refund_module, (session,))
    service = RensheRefundService(_wechat())

    result = await service._apply_provider_result(
        _provider_refund(status="PROCESSING", success_time=None),
        source="query",
        expected_refund_id=refund.id,
        allow_success=True,
    )

    assert result.processed is False
    assert result.status == "processing"
    assert refund.wechat_refund_id == "wechat-refund-1001"
    assert refund.last_error is None
    assert session.commits == 1


@pytest.mark.asyncio
async def test_callback_and_query_competition_has_one_atomic_success(
    monkeypatch,
) -> None:
    refund = _refund(
        status="processing",
        out_refund_no="RSRF0000000000000000000000000001",
        wechat_refund_id="wechat-refund-1001",
        processing_at=NOW - timedelta(minutes=5),
    )
    order = _order()
    application = _application()
    cleanup = SimpleNamespace(
        id=61,
        status="paused",
        paused_reason="active_refunds",
        due_at=NOW,
        rebase_count=0,
    )
    plan = SimpleNamespace(id=41, cleanup_due_at=NOW)
    first = _Session(
        scalars=(refund, order, application, None, 0, plan),
        rows=((cleanup,),),
    )
    second = _Session(scalars=(refund, order, application, None))
    _install_sessions(
        monkeypatch,
        refund_module,
        (first, second),
        serialized=True,
    )
    monkeypatch.setattr(RensheRefundService, "_now", staticmethod(lambda: NOW))
    service = RensheRefundService(_wechat())
    provider = _provider_refund()

    callback, query = await asyncio.gather(
        service._apply_provider_result(
            provider,
            source="notification",
            expected_refund_id=None,
            allow_success=True,
        ),
        service._apply_provider_result(
            provider,
            source="query",
            expected_refund_id=1,
            allow_success=True,
        ),
    )

    assert {callback.processed, query.processed} == {True, False}
    assert refund.status == "succeeded"
    assert order.status == "refunded"
    assert application.status == "closed"
    assert application.freeze_reason is None
    assert cleanup.status == "scheduled"
    assert cleanup.due_at == NOW + timedelta(
        days=settings.RENSHE_CLEANUP_RETENTION_DAYS
    )
    assert plan.cleanup_due_at == cleanup.due_at
    assert first.commits + second.commits == 1


@pytest.mark.parametrize(
    ("provider_overrides", "error"),
    [
        ({"amount_refund": 1}, "全额退款金额"),
        ({"out_trade_no": "other-order"}, "商户订单号"),
        ({"transaction_id": "other-transaction"}, "交易号"),
        ({"mchid": "other-merchant"}, "商户号"),
        ({"currency": "USD"}, "币种"),
    ],
)
@pytest.mark.asyncio
async def test_provider_field_mismatch_fails_closed_without_mutation(
    monkeypatch,
    provider_overrides,
    error,
) -> None:
    refund = _refund(
        status="processing",
        out_refund_no="RSRF0000000000000000000000000001",
    )
    order = _order()
    application = _application()
    session = _Session(scalars=(refund, order, application))
    _install_sessions(monkeypatch, refund_module, (session,))
    service = RensheRefundService(_wechat())

    with pytest.raises(ConflictException, match=error):
        await service._apply_provider_result(
            _provider_refund(**provider_overrides),
            source="notification",
            expected_refund_id=None,
            allow_success=True,
        )

    assert refund.status == "processing"
    assert order.status == "paid"
    assert session.commits == 0


@pytest.mark.asyncio
async def test_provider_refund_id_cannot_be_reused_by_another_request(
    monkeypatch,
) -> None:
    refund = _refund(
        status="processing",
        out_refund_no="RSRF0000000000000000000000000001",
    )
    session = _Session(scalars=(refund, _order(), _application(), 999))
    _install_sessions(monkeypatch, refund_module, (session,))
    service = RensheRefundService(_wechat())

    with pytest.raises(ConflictException, match="已绑定其他退款申请"):
        await service._apply_provider_result(
            _provider_refund(),
            source="notification",
            expected_refund_id=None,
            allow_success=True,
        )

    assert refund.status == "processing"
    assert session.commits == 0


@pytest.mark.asyncio
async def test_refund_worker_isolates_one_item_failure_and_continues(monkeypatch) -> None:
    selection = _Session(rows=((1, 2, 3),))
    _install_sessions(monkeypatch, refund_worker_module, (selection,))
    refund_service = SimpleNamespace(
        reconcile_refund=AsyncMock(
            side_effect=[
                SimpleNamespace(status="succeeded"),
                RuntimeError("provider unavailable"),
                SimpleNamespace(status="processing"),
            ]
        )
    )
    worker = RensheRefundReconciliationService(refund_service)
    worker._status = AsyncMock(side_effect=["processing", "processing", "processing"])

    batch = await worker.reconcile_batch(limit=3)

    assert batch.scanned == 3
    assert batch.synchronized == 2
    assert batch.changed == 1
    assert batch.failed == 1
    assert batch.next_after_id == 3
    selection_sql = str(selection.statements[0])
    assert "renshe_refund_request.request_kind IN" in selection_sql
    assert "renshe_refund_request.status =" in selection_sql


@pytest.mark.asyncio
async def test_worker_automatically_submits_batch_generated_refund(monkeypatch) -> None:
    refund = _refund(status="requested", request_kind="batch_cancel")
    lookup = _Session(scalars=(refund,))
    _install_sessions(monkeypatch, refund_module, (lookup,))
    service = RensheRefundService(_wechat())
    expected = RensheRefundResponse.model_validate(refund)
    service.submit_system_refund = AsyncMock(return_value=expected)

    result = await service.reconcile_refund(refund.id)

    assert result.request_kind == "batch_cancel"
    service.submit_system_refund.assert_awaited_once_with(refund.id)


@pytest.mark.asyncio
async def test_audit_query_is_redacted_read_only_and_audits_itself(monkeypatch) -> None:
    row = RensheAuditLog(
        actor_type="admin",
        actor_id=9,
        action="review.initial.approved",
        object_type="review",
        object_id=71,
        application_id=11,
        result="succeeded",
        summary={
            "note": "身份证 510105199001011234 手机 13800138000",
            "openid": "openid-sensitive",
        },
    )
    row.id = 81
    row.created_at = NOW
    row.updated_at = NOW
    session = _Session(scalars=(1,), rows=((row,),))
    _install_sessions(monkeypatch, audit_module, (session,))

    result = await RensheAuditQueryService().list_logs(
        admin_id=9,
        ip_address="192.0.2.10",
        plan_id=None,
        application_id=11,
        actor_admin_id=9,
        action="review.initial.approved",
        result="succeeded",
        started_at=NOW - timedelta(days=1),
        ended_at=NOW + timedelta(days=1),
        page=1,
        page_size=20,
    )

    assert result.total == 1
    assert result.items[0].summary == {
        "note": "身份证 [REDACTED] 手机 [REDACTED]",
        "openid": "[REDACTED]",
    }
    query_audit = next(
        item for item in session.added if isinstance(item, RensheAuditLog)
    )
    assert query_audit.action == "audit.query"
    assert query_audit.result == "succeeded"
    assert query_audit.ip_address == "192.0.2.10"
    assert session.commits == 1

    audit_route = next(
        route
        for route in app.routes
        if getattr(route, "path", None) == "/admin/cert-products/renshe/audit-logs"
    )
    assert audit_route.methods == {"GET"}


def test_audit_plan_filter_covers_related_domain_objects() -> None:
    sql = str(RensheAuditQueryService._plan_scope(41))
    assert "renshe_application.plan_id" in sql
    assert "renshe_refund_request.application_id" in sql
    assert "renshe_cleanup_run.plan_id" in sql
    assert "renshe_export_job.plan_id" in sql


def test_refund_decision_contract_allows_approved_retry() -> None:
    decision = RensheRefundDecision(decision="approved")
    assert decision.reason is None


@pytest.mark.asyncio
async def test_refund_callback_http_uses_direct_v3_ack(monkeypatch) -> None:
    handler = AsyncMock(
        return_value=SimpleNamespace(refund_id=1, processed=True)
    )
    monkeypatch.setattr(
        RensheRefundService,
        "handle_callback_raw",
        handler,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/payment/refund-callback",
            content=b"signed-refund-envelope",
            headers={"Wechatpay-Serial": "platform-serial"},
        )

    assert response.status_code == 200
    assert response.json() == {"code": "SUCCESS", "message": "成功"}
    handler.assert_awaited_once()
    assert handler.await_args.kwargs["raw_body"] == b"signed-refund-envelope"


@pytest.mark.asyncio
async def test_audit_query_http_requires_admin_authentication() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/admin/cert-products/renshe/audit-logs")

    assert response.status_code == 401
