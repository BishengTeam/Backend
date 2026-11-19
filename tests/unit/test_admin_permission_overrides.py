"""按人权限覆盖（覆盖式勾选）：resolver 三态、目录白名单防漂移、
创建/编辑存库与审计、管理闸放宽。"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.domain.user.src.index import AdminSecurityAudit, AdminUser
from app.policy.permissions import (
    ALL_PERMISSIONS,
    ROLE_PERMISSIONS,
    resolve_admin_permissions,
)
from app.port.exceptions import BusinessException
from app.schemas.admin_settings import (
    AdminSettingsUserCreate,
    AdminSettingsUserListItem,
    AdminSettingsUserUpdate,
)
from app.services import admin_settings as service_module
from app.services.admin_security_audit import AdminSecurityAuditService
from app.services.admin_settings import AdminSettingsService

APP_ROOT = Path(__file__).resolve().parents[2] / "app"


class _Result:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeDB:
    def __init__(self, results=(), *, scalar_value=None):
        self.results = list(results)
        self.scalar_value = scalar_value
        self.added: list[object] = []
        self.commits = 0

    async def execute(self, _statement):
        return self.results.pop(0)

    async def scalar(self, _statement):
        return self.scalar_value

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        for value in self.added:
            if isinstance(value, AdminUser) and value.id is None:
                value.id = 91

    async def commit(self):
        self.commits += 1

    async def refresh(self, value):
        now = datetime.now(timezone.utc)
        if getattr(value, "created_at", None) is None:
            value.created_at = now
        if getattr(value, "updated_at", None) is None:
            value.updated_at = now


def _db_context(db: _FakeDB):
    @asynccontextmanager
    async def _context():
        yield db

    return _context


def _admin(**overrides):
    now = datetime.now(timezone.utc)
    values = {
        "id": 7,
        "username": "quiz.operator",
        "display_name": "题库运营",
        "password_hash": "hash:current",
        "role": "quiz_admin",
        "permissions": None,
        "is_active": True,
        "must_change_password": False,
        "auth_version": 3,
        "failed_login_attempts": 0,
        "locked_until": None,
        "last_login_at": None,
        "password_changed_at": now,
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _audits(db: _FakeDB) -> list[AdminSecurityAudit]:
    return [row for row in db.added if isinstance(row, AdminSecurityAudit)]


def test_resolver_super_admin_is_always_wildcard() -> None:
    assert resolve_admin_permissions(_admin(role="super_admin")) == ["*"]


def test_resolver_prefers_override_then_falls_back_to_preset() -> None:
    admin = _admin(permissions=["points:manage"])
    assert resolve_admin_permissions(admin) == ["points:manage"]
    assert resolve_admin_permissions(_admin()) == ROLE_PERMISSIONS["quiz_admin"]
    assert resolve_admin_permissions(_admin(role="legacy_role")) == []


def test_permission_catalog_covers_every_route_literal() -> None:
    """防漂移：app/ 下所有权限字面量必须都在目录白名单内。"""

    pattern = re.compile(
        r"(?:require_permission\(\s*|permission\s*=\s*)(['\"])([a-z0-9_:]+)\1"
    )
    found: set[str] = set()
    for path in APP_ROOT.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        found |= {match.group(2) for match in pattern.finditer(source)}
    assert found <= ALL_PERMISSIONS
    # 关键单点权限必须有目录项（service 层校验、积分商城诉求）
    assert "quiz_content_publish" in ALL_PERMISSIONS
    assert "points:manage" in ALL_PERMISSIONS


@pytest.mark.asyncio
async def test_create_stores_override_permissions_and_audits_source(
    monkeypatch,
) -> None:
    db = _FakeDB(scalar_value=None)
    monkeypatch.setattr(service_module, "get_db_ctx", _db_context(db))
    service = AdminSettingsService()
    service.auth = SimpleNamespace(
        generate_temporary_password=lambda **_kwargs: "GeneratedSecureValue2026",
        hash_password=lambda value: f"hash:{value}",
    )
    service.audit = AdminSecurityAuditService()

    result = await service.create_admin(
        AdminSettingsUserCreate(
            username="mall.operator",
            display_name="商城运营",
            role="quiz_admin",
            permissions=["points:manage", "points:manage"],
        ),
        actor_id=1,
        idempotency_key="create-permission-0001",
    )

    admin = db.added[0]
    assert admin.permissions == ["points:manage"]  # 去重保序
    assert result.admin.permissions == ["points:manage"]
    assert result.admin.permissions_source == "override"
    assert _audits(db)[0].summary["permissions_source"] == "override"


@pytest.mark.asyncio
async def test_update_sets_permissions_with_diff_audit(monkeypatch) -> None:
    admin = _admin()  # 未覆盖 → 生效集 = quiz_admin 预设
    db = _FakeDB([_Result(value=admin)])
    monkeypatch.setattr(service_module, "get_db_ctx", _db_context(db))
    service = AdminSettingsService()
    service.audit = AdminSecurityAuditService()

    result = await service.update_admin(
        admin.id,
        AdminSettingsUserUpdate(permissions=["points:manage", "quiz:list"]),
        actor_id=1,
    )

    assert admin.permissions == ["points:manage", "quiz:list"]
    assert result.permissions == ["points:manage", "quiz:list"]
    assert result.permissions_source == "override"
    audit = _audits(db)[0]
    assert audit.reason_code == "permissions_updated"
    # quiz:list 本就在 quiz_admin 预设内，相对生效集只有 points:manage 是新增
    assert audit.summary["permissions_added"] == ["points:manage"]
    assert set(audit.summary["permissions_removed"]) == (
        set(ROLE_PERMISSIONS["quiz_admin"]) - {"quiz:list"}
    )


@pytest.mark.asyncio
async def test_update_clearing_permissions_falls_back_to_preset(monkeypatch) -> None:
    admin = _admin(permissions=["points:manage"])
    db = _FakeDB([_Result(value=admin)])
    monkeypatch.setattr(service_module, "get_db_ctx", _db_context(db))
    service = AdminSettingsService()
    service.audit = AdminSecurityAuditService()

    result = await service.update_admin(
        admin.id,
        AdminSettingsUserUpdate(display_name="新名字", permissions=None),
        actor_id=1,
    )

    assert admin.permissions is None
    assert admin.display_name == "新名字"
    assert result.permissions == ROLE_PERMISSIONS["quiz_admin"]
    assert result.permissions_source == "preset"
    audit = _audits(db)[0]
    assert audit.reason_code == "account_updated"
    assert audit.summary["permissions_removed"] == ["points:manage"]
    assert audit.summary["permissions_added"] == []


def test_update_rejects_unknown_permission() -> None:
    service = AdminSettingsService()
    with pytest.raises(BusinessException, match="未知权限项"):
        service._validated_permissions(["points:manage", "mall:fly_to_moon"])


def test_update_requires_at_least_one_field() -> None:
    with pytest.raises(ValidationError):
        AdminSettingsUserUpdate()


@pytest.mark.asyncio
async def test_manageable_gate_widened_but_super_admin_refused() -> None:
    db = _FakeDB([_Result(value=_admin(role="cert_admin", id=8))])
    admin = await AdminSettingsService._get_manageable_admin(db, 8)
    assert admin.role == "cert_admin"

    db = _FakeDB([_Result(value=_admin(role="teacher", id=9))])
    admin = await AdminSettingsService._get_manageable_admin(db, 9)
    assert admin.role == "teacher"

    db = _FakeDB([_Result(value=_admin(role="super_admin", id=1))])
    with pytest.raises(BusinessException, match="唯一超级管理员"):
        await AdminSettingsService._get_manageable_admin(db, 1)


def test_list_item_resolves_permissions_and_source() -> None:
    item = AdminSettingsUserListItem.model_validate(
        _admin(role="super_admin", id=1, username="boss")
    )
    assert item.permissions == ["*"]
    assert item.permissions_source == "wildcard"

    item = AdminSettingsUserListItem.model_validate(
        _admin(permissions=["offline:view"])
    )
    assert item.permissions == ["offline:view"]
    assert item.permissions_source == "override"

    item = AdminSettingsUserListItem.model_validate(_admin())
    assert item.permissions == ROLE_PERMISSIONS["quiz_admin"]
    assert item.permissions_source == "preset"
