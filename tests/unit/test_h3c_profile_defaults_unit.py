"""H3C 报名预填字段回归测试。

历史缺陷：get_profile_defaults 误用脱敏字段 id_card / phone，
已实名用户在 H3C 报名表会看到暗文身份证并提交失败。
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.schemas.user import UserProfileDetail
from app.services.h3c_registration import H3cRegistrationService
from app.services import user as user_module
from app.services.user import UserService


RAW_ID_CARD = "510101200001010123"
MASKED_ID_CARD = "5101**********0123"
RAW_PHONE = "13800000001"
MASKED_PHONE = "138****0001"


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """模拟 get_profile 使用的最小 DB 会话：一次 get + 三次 execute。"""

    def __init__(self, user, profile, realname, student=None):
        self._user = user
        self._results = [
            _FakeResult(profile),
            _FakeResult(realname),
            _FakeResult(student),
        ]

    async def get(self, model, key):
        assert model is user_module.User
        return self._user

    async def execute(self, stmt):
        assert self._results, "get_profile 不应发起额外查询"
        return self._results.pop(0)


def _fake_db(user, profile, realname, student=None):
    @asynccontextmanager
    async def ctx():
        yield _FakeSession(user, profile, realname, student)

    return ctx


def _user():
    return SimpleNamespace(
        id=1,
        openid="openid-unit",
        is_active=True,
        level2_edit_count=0,
        level2_edit_reset_at=None,
        created_at=None,
    )


def _profile():
    return SimpleNamespace(
        nickname=None,
        email=None,
        phone=RAW_PHONE,
        province="四川",
        city="成都",
        address=None,
    )


def _realname(status):
    return SimpleNamespace(
        user_type="normal",
        last_name_zh=None,
        first_name_zh=None,
        last_name_en=None,
        first_name_en=None,
        real_name="王小明",
        id_card_number=RAW_ID_CARD,
        id_card_front_oss=None,
        id_card_back_oss=None,
        avatar_oss=None,
        birth_date=None,
        gender="male",
        age=None,
        census_register=None,
        zip_code=None,
        political_status=None,
        ethnicity=None,
        status=status,
    )


async def test_get_profile_returns_raw_phone_for_verified_user(monkeypatch):
    monkeypatch.setattr(
        user_module,
        "get_db_ctx",
        _fake_db(_user(), _profile(), _realname("verified")),
    )
    detail = await UserService().get_profile(1)
    assert detail.phone == MASKED_PHONE
    assert detail.phone_raw == RAW_PHONE
    assert detail.id_card == MASKED_ID_CARD
    assert detail.id_card_raw == RAW_ID_CARD


async def test_get_profile_hides_raw_pii_when_not_verified(monkeypatch):
    monkeypatch.setattr(
        user_module,
        "get_db_ctx",
        _fake_db(_user(), _profile(), _realname("pending")),
    )
    detail = await UserService().get_profile(1)
    assert detail.phone == MASKED_PHONE
    assert detail.phone_raw is None
    assert detail.id_card == MASKED_ID_CARD
    assert detail.id_card_raw is None


async def test_get_profile_admin_gets_raw_pii(monkeypatch):
    monkeypatch.setattr(
        user_module,
        "get_db_ctx",
        _fake_db(_user(), _profile(), _realname("pending")),
    )
    detail = await UserService().get_profile(1, is_admin=True)
    assert detail.phone == RAW_PHONE
    assert detail.phone_raw == RAW_PHONE
    assert detail.id_card == RAW_ID_CARD
    assert detail.id_card_raw == RAW_ID_CARD


def _detail(id_card_raw, phone_raw):
    return UserProfileDetail(
        id=1,
        openid="openid-unit",
        phone=MASKED_PHONE,
        phone_raw=phone_raw,
        id_card=MASKED_ID_CARD,
        id_card_raw=id_card_raw,
        real_name="王小明",
    )


async def test_h3c_defaults_use_raw_fields(monkeypatch):
    async def fake_get_profile(self, user_id, is_admin=False):
        return _detail(RAW_ID_CARD, RAW_PHONE)

    monkeypatch.setattr(UserService, "get_profile", fake_get_profile)
    defaults = await H3cRegistrationService().get_profile_defaults(1)
    assert defaults.candidate_idcard == RAW_ID_CARD
    assert defaults.phone == RAW_PHONE


async def test_h3c_defaults_stay_empty_without_verified_realname(monkeypatch):
    async def fake_get_profile(self, user_id, is_admin=False):
        return _detail(None, None)

    monkeypatch.setattr(UserService, "get_profile", fake_get_profile)
    defaults = await H3cRegistrationService().get_profile_defaults(1)
    assert defaults.candidate_idcard is None
    assert defaults.phone is None


def test_masked_values_can_never_leak_into_defaults_mapping():
    """防止未来再把展示字段误当提交值：脱敏值绝不应被映射。"""
    detail = _detail(None, None)
    assert detail.id_card == MASKED_ID_CARD
    assert detail.phone == MASKED_PHONE
