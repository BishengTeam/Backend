from unittest.mock import MagicMock

import pytest

from app.middleware.auth import require_permission, require_super_admin
from app.policy.permissions import ROLE_PERMISSIONS
from app.port.exceptions import ForbiddenException


def _admin(role: str):
    admin = MagicMock()
    admin.role = role
    admin.id = 1
    admin.is_active = True
    return admin


async def _check(permission: str, role: str):
    return await require_permission(permission)(admin=_admin(role))


class TestTwoRoleRbac:
    def test_role_matrix_is_frozen(self):
        assert set(ROLE_PERMISSIONS) == {"super_admin", "admin"}

    @pytest.mark.asyncio
    async def test_super_admin_passes_unknown_permission(self):
        result = await _check("future:permission", "super_admin")
        assert result.role == "super_admin"

    @pytest.mark.asyncio
    async def test_normal_admin_passes_operational_permission(self):
        result = await _check("user:write", "admin")
        assert result.role == "admin"

    @pytest.mark.asyncio
    async def test_unknown_legacy_role_is_denied(self):
        with pytest.raises(ForbiddenException):
            await _check("user:list", "customer_service")

    @pytest.mark.asyncio
    async def test_normal_admin_fails_super_admin_dependency(self):
        with pytest.raises(ForbiddenException):
            await require_super_admin(admin=_admin("admin"))

    @pytest.mark.asyncio
    async def test_super_admin_dependency_accepts_super_admin(self):
        result = await require_super_admin(admin=_admin("super_admin"))
        assert result.role == "super_admin"
