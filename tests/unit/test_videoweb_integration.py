from pathlib import Path

import pytest

from app.schemas.user import UserProfileUpdate
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


class _FakeRedis:
    def __init__(self):
        self.data: dict[str, str] = {}

    async def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    async def get(self, key):
        return self.data.get(key)

    async def delete(self, *keys):
        deleted = 0
        for key in keys:
            if key in self.data:
                del self.data[key]
                deleted += 1
        return deleted

    async def incr(self, key):
        self.data[key] = str(int(self.data.get(key, "0")) + 1)
        return int(self.data[key])

    async def expire(self, key, ttl):
        return key in self.data


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


async def test_videoweb_login_code_is_single_use_and_cooldown_applies(
    monkeypatch: pytest.MonkeyPatch,
):
    fake_redis = _FakeRedis()
    monkeypatch.setattr("app.services.videoweb.redis_client", fake_redis)
    login_code = await VideoWebService.create_login_code(100)

    assert len(login_code) == 6
    assert login_code.isdigit()
    assert fake_redis.data[f"videoweb:login:code:{login_code}"] == "100"
    assert fake_redis.data["videoweb:login:owner:100"] == login_code

    assert await VideoWebService.exchange_login_code(login_code) == 100
    assert f"videoweb:login:code:{login_code}" not in fake_redis.data
    assert "videoweb:login:owner:100" not in fake_redis.data

    with pytest.raises(Exception, match="已使用"):
        await VideoWebService.exchange_login_code(login_code)

    with pytest.raises(Exception, match="过于频繁"):
        await VideoWebService.create_login_code(100)


def test_user_profile_update_rejects_phone_field():
    import json

    parsed = UserProfileUpdate(nickname="张三", email="test@example.com", phone="13800138000")
    payload = json.loads(
        parsed.model_dump_json(exclude_none=True)
    )
    assert "phone" not in payload


def test_videoweb_bridge_routes_and_payment_hook_exist():
    api_source = (REPO_ROOT / "app/api/videoweb.py").read_text(encoding="utf-8")
    fulfillment_source = (REPO_ROOT / "app/services/order_fulfillment.py").read_text(
        encoding="utf-8"
    )
    config_source = (REPO_ROOT / "app/port/config.py").read_text(encoding="utf-8")

    assert '"/users/lookup"' in api_source
    assert '"/login-code"' in api_source
    assert '"/login/exchange"' in api_source
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
    assert "/api/videoweb/login-code" in paths
    assert "/internal/videoweb/login/exchange" in paths
    assert "/admin/videoweb/codes" in paths
