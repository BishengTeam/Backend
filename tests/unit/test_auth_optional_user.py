"""get_current_user_optional 回归测试。

2026-11-26 事故：手机号闸门给 get_current_user 加了 request 参数，
get_current_user_optional 仍按旧签名直接调用，导致带 Bearer 的
GET /api/quiz/libraries?schema=2 全部 500（小程序学习页题库目录空）。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.adapter.security import create_access_token
from app.middleware import auth


class _FakeDB:
    def __init__(self, user):
        self._user = user

    async def get(self, _model, _pk):
        return self._user


class _FakeURL:
    def __init__(self, path: str):
        self.path = path


class _FakeRequest:
    def __init__(self, method: str, path: str):
        self.method = method
        self.url = _FakeURL(path)


@pytest.fixture
def user():
    return SimpleNamespace(id=7, is_active=True, phone="13800000000")


async def test_optional_auth_with_bearer_resolves_user(monkeypatch, user):
    monkeypatch.setattr(auth, "is_token_revoked", AsyncMock(return_value=False))
    token = create_access_token(user.id, "openid-7")

    resolved = await auth.get_current_user_optional(
        request=_FakeRequest("GET", "/api/quiz/libraries"),
        authorization=f"Bearer {token}",
        db=_FakeDB(user),
    )

    assert resolved is user


async def test_optional_auth_keeps_phone_gate_for_writes(monkeypatch):
    phone_less = SimpleNamespace(id=8, is_active=True, phone="")
    monkeypatch.setattr(auth, "is_token_revoked", AsyncMock(return_value=False))
    monkeypatch.setattr(auth.settings, "REQUIRE_BOUND_PHONE", True)
    token = create_access_token(phone_less.id, "openid-8")

    resolved = await auth.get_current_user_optional(
        request=_FakeRequest("POST", "/api/quiz/practice-sessions"),
        authorization=f"Bearer {token}",
        db=_FakeDB(phone_less),
    )

    assert resolved is None
