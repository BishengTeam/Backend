from pathlib import Path

import pytest

from app.port.config import settings
from app.services.videoweb import VideoWebService


REPO_ROOT = Path(__file__).resolve().parents[2]


class _FakeResponse:
    status_code = 200

    @staticmethod
    def json():
        return [{"id": 1, "code": "VW-TEST"}]


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def request(self, method, url, **kwargs):
        self.method = method
        self.url = url
        self.kwargs = kwargs
        return _FakeResponse()


async def test_videoweb_issue_code_sends_service_token_and_idempotent_order(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "VIDEOWEB_BASE_URL", "http://videoweb.local")
    monkeypatch.setattr(settings, "VIDEOWEB_SERVICE_TOKEN", "service-token")
    client = _FakeAsyncClient()
    monkeypatch.setattr(
        "app.services.videoweb.httpx.AsyncClient", lambda *args, **kwargs: client
    )

    result = await VideoWebService.issue_code(
        course_id=3,
        order_id=501,
        miniapp_user_id=100,
        created_by="miniapp-backend",
        note="订单支付完成自动生成",
    )

    assert result[0]["code"] == "VW-TEST"
    assert client.method == "POST"
    assert client.url == "http://videoweb.local/internal/codes"
    assert client.kwargs["headers"] == {"X-Service-Token": "service-token"}
    assert client.kwargs["json"] == {
        "course_id": 3,
        "quantity": 1,
        "source_order_id": 501,
        "owner_miniapp_user_id": 100,
        "created_by": "miniapp-backend",
        "note": "订单支付完成自动生成",
    }


async def test_videoweb_manual_code_omits_owner_and_order(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "VIDEOWEB_BASE_URL", "http://videoweb.local")
    monkeypatch.setattr(settings, "VIDEOWEB_SERVICE_TOKEN", "service-token")
    client = _FakeAsyncClient()
    monkeypatch.setattr(
        "app.services.videoweb.httpx.AsyncClient", lambda *args, **kwargs: client
    )

    await VideoWebService.issue_code(
        course_id=3,
        order_id=None,
        miniapp_user_id=None,
        created_by="admin",
    )

    assert client.kwargs["json"]["source_order_id"] is None
    assert "owner_miniapp_user_id" not in client.kwargs["json"]


def test_videoweb_bridge_routes_and_payment_hook_exist():
    api_source = (REPO_ROOT / "app/api/videoweb.py").read_text(encoding="utf-8")
    fulfillment_source = (REPO_ROOT / "app/services/order_fulfillment.py").read_text(
        encoding="utf-8"
    )
    config_source = (REPO_ROOT / "app/port/config.py").read_text(encoding="utf-8")

    assert '"/users/lookup"' in api_source
    assert '"/codes"' in api_source
    assert "compare_digest(x_service_token, expected)" in api_source
    assert "require_permission(\"course:read\")" in api_source
    assert "require_permission(\"course:write\")" in api_source
    assert "VideoWebService.schedule_issue_for_order(order.id)" in fulfillment_source
    assert "VIDEOWEB_BASE_URL" in config_source
    assert '"VIDEOWEB_SERVICE_TOKEN"' in config_source
    assert '"VIDEOWEB_LOOKUP_SERVICE_TOKEN"' in config_source


def test_fastapi_registers_videoweb_routes():
    from app.main import app

    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/internal/videoweb/users/lookup" in paths
    assert "/api/videoweb/codes" in paths
    assert "/admin/videoweb/codes" in paths
