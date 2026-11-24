"""WeChat Pay is retired: its HTTP surface must answer ``410 Gone``."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.contracts.payment import (
    RETIRED_WECHAT_PAY_ENDPOINTS,
    WECHAT_PAY_RETIRED_CODE,
    WECHAT_PAY_RETIRED_MESSAGE,
    wechat_pay_retired,
)
from app.main import app


def _resolve(template: str) -> str:
    return template.replace("{order_id}", "117").replace("{refund_id}", "7")


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "template"), RETIRED_WECHAT_PAY_ENDPOINTS)
async def test_retired_wechat_endpoints_answer_410_before_auth(method, template):
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.request(method, _resolve(template), json={})

    assert response.status_code == 410
    assert response.json() == {
        "code": WECHAT_PAY_RETIRED_CODE,
        "message": WECHAT_PAY_RETIRED_MESSAGE,
        "data": None,
    }


@pytest.mark.asyncio
async def test_retirement_middleware_only_matches_the_frozen_routes():
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        wrong_method = await client.get("/api/payment/prepay")
        lookalike = await client.post("/api/payment/prepay/extra")

    assert wrong_method.status_code == 405
    assert lookalike.status_code == 404


def test_openapi_marks_retired_routes_deprecated():
    schema = app.openapi()
    for _, template in RETIRED_WECHAT_PAY_ENDPOINTS:
        operation = schema["paths"][template]["post"]
        assert operation.get("deprecated") is True
        assert "410" in operation["responses"]


def test_retirement_switch_is_on():
    assert wechat_pay_retired() is True
