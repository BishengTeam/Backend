"""登录时顺带绑定微信手机号：schema 透传 + 尽力而为的绑定逻辑。"""

from types import SimpleNamespace

import pytest

from app.schemas.user import LoginRequest
from app.services.auth import AuthService


class _FakeWechat:
    def __init__(self, phone: str | None = None, error: Exception | None = None):
        self.phone = phone
        self.error = error
        self.calls: list[str] = []

    async def get_phone_number(self, code: str) -> str:
        self.calls.append(code)
        if self.error is not None:
            raise self.error
        assert self.phone is not None
        return self.phone


class _FakeDB:
    def __init__(self, conflict: SimpleNamespace | None = None):
        self.conflict = conflict

    async def execute(self, statement):
        return SimpleNamespace(scalar_one_or_none=lambda: self.conflict)


def _service(wechat: _FakeWechat) -> AuthService:
    service = AuthService()
    service.wechat = wechat
    return service


def test_login_request_accepts_optional_phone_code() -> None:
    assert LoginRequest(code="login_code").phone_code is None
    request = LoginRequest(code="login_code", phone_code="phone_code")
    assert request.phone_code == "phone_code"


@pytest.mark.asyncio
async def test_bind_phone_sets_user_phone() -> None:
    wechat = _FakeWechat(phone="13800000000")
    user = SimpleNamespace(id=1, phone=None)

    await _service(wechat)._bind_phone_best_effort(_FakeDB(), user, "phone_code")

    assert user.phone == "13800000000"
    assert wechat.calls == ["phone_code"]


@pytest.mark.asyncio
async def test_bind_phone_skips_when_number_taken() -> None:
    wechat = _FakeWechat(phone="13800000000")
    user = SimpleNamespace(id=1, phone=None)
    db = _FakeDB(conflict=SimpleNamespace(id=2))

    await _service(wechat)._bind_phone_best_effort(db, user, "phone_code")

    assert user.phone is None


@pytest.mark.asyncio
async def test_bind_phone_failure_does_not_raise() -> None:
    wechat = _FakeWechat(error=RuntimeError("wechat down"))
    user = SimpleNamespace(id=1, phone=None)

    # 绑定失败绝不允许冒泡，否则会阻断登录
    await _service(wechat)._bind_phone_best_effort(_FakeDB(), user, "phone_code")

    assert user.phone is None
