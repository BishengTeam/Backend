from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.adapter.database import get_db
from app.api import h3c, renshe, quiz, points, points_mall, certification, zone
from app.middleware.error_handler import app_exception_handler
from app.port.exceptions import AppException
from app.schemas.plan import PlanResponse
from app.schemas.renshe_public import public_plan


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    for router in [h3c.router, h3c.order_router, renshe.router, quiz.router, points.router, points_mall.router, certification.router, zone.router]:
        app.include_router(router, prefix="/api")
    app.add_exception_handler(AppException, app_exception_handler)
    async def db(): yield SimpleNamespace()
    app.dependency_overrides[get_db] = db
    monkeypatch.setattr(h3c.H3cRegistrationService, "list_user_batches", AsyncMock(return_value=[]))
    monkeypatch.setattr(quiz.QuizV2Service, "list_libraries", AsyncMock(return_value=[]))
    monkeypatch.setattr(points_mall._service, "user_list_items", AsyncMock(return_value=[]))
    monkeypatch.setattr(points._virtual_service, "list_products_for_user", AsyncMock(return_value={"items": [], "total": 0, "page": 1, "page_size": 20}))
    from app.services.renshe_project import RensheProjectService
    from app.services.plan import PlanService
    monkeypatch.setattr(RensheProjectService, "list_projects", AsyncMock(return_value=[]))
    monkeypatch.setattr(PlanService, "list_renshe_plans", AsyncMock(return_value=[]))
    monkeypatch.setattr(certification.CertificationService, "list_certifications", AsyncMock(return_value=[]))
    return TestClient(app)


@pytest.mark.parametrize("path", ["/api/h3c/exam-batches", "/api/renshe/projects", "/api/renshe/batches", "/api/cert/certifications", "/api/quiz/libraries", "/api/points-mall/items", "/api/points/virtual-products"])
def test_public_catalogs_accept_anonymous_clients(client, path):
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert response.json()["code"] == 0


@pytest.mark.parametrize("path", ["/api/orders/h3c/profile", "/api/h3c/registrations", "/api/renshe/applications", "/api/renshe/verification-materials/portrait/signed-url", "/api/quiz/questions", "/api/quiz/stats", "/api/quiz/libraries/1/questions", "/api/points", "/api/points/history", "/api/points-mall/my-coupons"])
def test_personal_information_stays_protected(client, path):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path,body", [
    ("/api/points-mall/redeem", {"item_id": 1}),
    ("/api/points/virtual-orders/precheck", {"product_id": 1}),
    ("/api/renshe/applications/draft", {}),
    ("/api/orders/h3c", {}),
    ("/api/quiz/practice-sessions", {}),
])
def test_business_actions_stay_protected(client, path, body):
    assert client.post(path, json=body).status_code == 401


def test_renshe_public_projection_omits_admin_contacts_config_ids_and_template_storage():
    plan = PlanResponse.model_construct(id=1, name="公开批次", status="published", contact_name="内部人员", contact_phone="private",
        renshe_config_id=99, renshe_config={"qualification_rule":"related_major_graduated_degree_v1",
        "reporting_policy":{"contract_version":2, "commitment_template":{"filename":"private.pdf", "sha256":"secret"}},
        "materials":[], "rule_profile":{"source_conditions":["已毕业"], "internal_note":"private"}})
    result = public_plan(plan)
    assert result["can_apply"] is True
    assert not {"renshe_config_id", "contact_name", "contact_phone"} & result.keys()
    assert "reporting_policy" not in result["renshe_config"]
    assert result["renshe_config"]["rule_profile"] == {"source_conditions":["已毕业"]}


@pytest.mark.asyncio
async def test_anonymous_points_catalog_never_queries_personal_balance_or_redemptions(monkeypatch):
    import app.services.points_mall as module
    item = SimpleNamespace(id=1, name="公开优惠券", description="兑换规则", discount_type="fixed", discount_value=100,
        scope_type="global", scope_value=None, min_order_amount_cents=0, points_cost=10, total_stock=0,
        total_redeemed=0, per_user_limit=1, validity_type="days", valid_until=None)
    class DB:
        async def execute(self, stmt): return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [item]))
        async def scalar(self, stmt): raise AssertionError("guest catalog queried personal information")
    @asynccontextmanager
    async def context(): yield DB()
    monkeypatch.setattr(module, "get_db_ctx", context)
    result = await module.PointsMallService().user_list_items(None)
    assert result[0].name == "公开优惠券"
    assert result[0].user_redeemed_count is None
    assert result[0].redeem_blocked_reason == "登录后兑换"
