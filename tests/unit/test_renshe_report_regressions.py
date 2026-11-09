"""Reported regressions: real isolated SQL reads and PostgreSQL query compilation.

No production data, bank operation or provider request is used.
"""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.domain.order.src.index import Order
from app.domain.renshe.src.index import RensheApplication, RensheApplicationVersion, RensheRefundRequest
from app.models.consultation_group import CertConsultationGroup
from app.schemas.renshe import RensheRefundCreate
from app.services.consultation_group import ConsultationGroupService
from app.services.renshe_application import RensheApplicationService
from app.services.renshe_refund import RensheRefundService
from tests.unit.test_renshe_current_export import approved
from tests.unit.test_renshe_projects import isolated as _isolated

isolated = _isolated




def _batch_field(batch, field: str, default=""):
    """Safely get a field from batch (BatchResponse or mock) — checks products[0] first."""
    products = getattr(batch, "products", None)
    if products:
        return getattr(products[0], field, default)
    return getattr(batch, field, default)


def _batch_occ(batch) -> str:
    return _batch_field(batch, "occupation_name", "信息安全管理员")


def _batch_occ_code(batch) -> str:
    return _batch_field(batch, "occupation_code", "")


def _batch_skill(batch) -> str:
    return _batch_field(batch, "skill_level", "中级工")



@pytest.mark.asyncio
async def test_refund_search_matches_current_name_order_and_refund_number(isolated, monkeypatch):
    _, batch, submitted = await approved(isolated, monkeypatch)
    service = RensheRefundService()
    refund = await service.request_refund(user_id=1, application_id=submitted.application.id,
        data=RensheRefundCreate(reason_code="test_report"))
    async with isolated.db() as db:
        version = await db.get(RensheApplicationVersion, submitted.version.id)
        version.realname_snapshot = {**version.realname_snapshot, "real_name": "退款搜索测试考生"}
        db.add(RensheApplicationVersion(application_id=submitted.application.id, version_no=99,
            submitted_by_user_id=1, realname_snapshot={"real_name": "历史版本旧姓名"},
            student_snapshot={}, form_data={}, submitted_at=datetime.now(timezone.utc)))
        order = await db.get(Order, submitted.order_id)
        order.out_trade_no = "RS-ORDER-SEARCH-001"
        row = await db.get(RensheRefundRequest, refund.id)
        row.out_refund_no = "RS-REFUND-SEARCH-001"
        await db.commit()

    async def search(**params):
        return await service.list_refunds(status=None, page=1, page_size=20, **params)

    for keyword in [None, "", "  ", "退款搜索", "  测试考生  ", "RS-ORDER-SEARCH-001", "RS-REFUND-SEARCH-001"]:
        result = await search(keyword=keyword, plan_id=batch.id)
        assert result.total == 1
        assert [r.id for r in result.items] == [refund.id]
        assert result.items[0].candidate_name == "退款搜索测试考生"
        assert result.status_counts == {"requested": 1}
    for keyword in ["不存在的考生", "历史版本旧姓名", "' OR 1=1 --"]:
        result = await search(keyword=keyword)
        assert result.total == 0 and result.items == [] and result.status_counts == {}
    pending = await search(keyword="退款搜索", group="pending")
    assert pending.total == 1
    processing = await search(keyword="退款搜索", group="processing")
    assert processing.total == 0 and processing.status_counts == {"requested": 1}
    assert (await search(keyword="退款搜索", plan_id=batch.id + 1000)).total == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("keyword", [None, "搜索考生", "RS-ORDER-001"])
async def test_all_refund_search_queries_compile_for_postgresql(monkeypatch, keyword):
    statements = []

    class Database:
        def compile(self, statement):
            statements.append(statement.compile(dialect=postgresql.dialect()))

        async def execute(self, statement):
            self.compile(statement)
            return SimpleNamespace(all=lambda: [])

        async def scalar(self, statement):
            self.compile(statement)
            return 0

    @asynccontextmanager
    async def context():
        yield Database()

    monkeypatch.setattr("app.services.renshe_refund.get_db_ctx", context)
    result = await RensheRefundService().list_refunds(keyword=keyword, status=None, page=1, page_size=20)
    assert result.total == 0
    assert len(statements) == 3  # status counts, total and paginated rows


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending_initial_review", "initial_rejected", "pending_external_review", "external_rejected", "external_approved", "approved", "final_approved"])
async def test_consultation_group_stays_visible_after_review(status):
    db = SimpleNamespace(scalar=AsyncMock(return_value=SimpleNamespace(
        qrcode_url="/api/media/test-group.png", join_instructions="请备注报名编号", expires_at=None)))
    plan = SimpleNamespace(id=12, status="published", contact_name="测试老师", contact_phone="13800138000")
    result = await ConsultationGroupService.get_user_group(db, plan=plan, registration_status=status)
    assert result is not None and result.qrcode_url == "/api/media/test-group.png"


@pytest.mark.asyncio
@pytest.mark.parametrize("plan_status,registration_status,expired", [
    ("draft", "external_approved", False), ("cancelled", "external_approved", False),
    ("archived", "external_approved", False), ("published", "pending_payment", False),
    ("published", "draft", False), ("published", "closed", False),
    ("published", "refunded_closed", False), ("published", "external_approved", True),
])
async def test_consultation_group_retains_visibility_restrictions(plan_status, registration_status, expired):
    row = SimpleNamespace(qrcode_url="/api/media/test-group.png", join_instructions=None,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1) if expired else None)
    db = SimpleNamespace(scalar=AsyncMock(return_value=row))
    plan = SimpleNamespace(id=12, status=plan_status, contact_name=None, contact_phone=None)
    assert await ConsultationGroupService.get_user_group(db, plan=plan, registration_status=registration_status) is None


@pytest.mark.asyncio
async def test_external_approved_application_detail_exposes_group_without_changing_review_status(isolated, monkeypatch):
    _, batch, submitted = await approved(isolated, monkeypatch)
    async with isolated.db() as db:
        application = await db.get(RensheApplication, submitted.application.id)
        application.status = "external_approved"
        db.add(CertConsultationGroup(plan_product_id=1, qrcode_url="/api/media/test-renshe-group.png",
            join_instructions="报名前请先阅读群公告", version=1))
        await db.commit()
    detail = await RensheApplicationService(isolated.storage).get_application(1, submitted.application.id)
    assert detail.status == "external_approved"
    assert detail.consultation_group is not None
    assert detail.consultation_group.qrcode_url == "/api/media/test-renshe-group.png"
