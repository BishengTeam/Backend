"""
Test Admin RBAC permission enforcement.

Verifies that require_permission dependency factory:
- super_admin passes all permission checks
- content_editor is denied order:write
- customer_service is denied quiz:write

Uses mocked get_current_admin so no real database or JWT is required.
"""
from unittest.mock import MagicMock

import pytest

from app.core.exceptions import ForbiddenException
from app.middleware.auth import require_permission
from app.policy.permissions import ROLE_PERMISSIONS


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _make_mock_admin(role: str, is_active: bool = True):
    """Return a MagicMock that looks like an AdminUser row."""
    admin = MagicMock()
    admin.role = role
    admin.is_active = is_active
    admin.id = 1
    admin.username = f"{role}_test"
    return admin


async def _resolve_permission_check(permission: str, admin_role: str):
    """Call the inner dependency produced by require_permission with
    a mock admin directly, bypassing FastAPI DI entirely."""
    dependency = require_permission(permission)
    mock_admin = _make_mock_admin(admin_role)
    # _check is defined as `async def _check(admin=Depends(get_current_admin))`.
    # Passing `admin=` explicitly overrides the Depends default.
    return await dependency(admin=mock_admin)


# ---------------------------------------------------------------------------
# ROLE_PERMISSIONS structure
# ---------------------------------------------------------------------------
class TestRolePermissionsDefinition:
    def test_super_admin_has_wildcard(self):
        assert ROLE_PERMISSIONS["super_admin"] == ["*"]

    def test_content_editor_lacks_order_write(self):
        assert "order:write" not in ROLE_PERMISSIONS["content_editor"]

    def test_content_editor_has_quiz_write(self):
        assert "quiz:write" in ROLE_PERMISSIONS["content_editor"]

    def test_customer_service_lacks_quiz_write(self):
        assert "quiz:write" not in ROLE_PERMISSIONS["customer_service"]

    def test_customer_service_has_order_list(self):
        assert "order:list" in ROLE_PERMISSIONS["customer_service"]

    def test_all_roles_have_dashboard_view(self):
        for role, perms in ROLE_PERMISSIONS.items():
            if role == "super_admin":
                continue  # wildcard covers everything
            assert (
                "dashboard:view" in perms
            ), f"{role} missing dashboard:view"


# ---------------------------------------------------------------------------
# require_permission enforcement
# ---------------------------------------------------------------------------
class TestRequirePermissionEnforcement:
    @pytest.mark.asyncio
    async def test_super_admin_passes_order_write(self):
        """super_admin with wildcard '*' must pass any permission check."""
        admin = await _resolve_permission_check("order:write", "super_admin")
        assert admin.role == "super_admin"

    @pytest.mark.asyncio
    async def test_super_admin_passes_quiz_write(self):
        admin = await _resolve_permission_check("quiz:write", "super_admin")
        assert admin.role == "super_admin"

    @pytest.mark.asyncio
    async def test_super_admin_passes_unknown_permission(self):
        """Wildcard must also grant permissions not explicitly listed."""
        admin = await _resolve_permission_check("some:future", "super_admin")
        assert admin.role == "super_admin"

    @pytest.mark.asyncio
    async def test_content_editor_passes_quiz_write(self):
        """content_editor has quiz:write in its allow-list."""
        admin = await _resolve_permission_check("quiz:write", "content_editor")
        assert admin.role == "content_editor"

    @pytest.mark.asyncio
    async def test_content_editor_denied_order_write(self):
        """content_editor is NOT allowed order:write."""
        with pytest.raises(ForbiddenException) as exc_info:
            await _resolve_permission_check("order:write", "content_editor")
        assert "order:write" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_customer_service_denied_quiz_write(self):
        """customer_service is NOT allowed quiz:write."""
        with pytest.raises(ForbiddenException) as exc_info:
            await _resolve_permission_check("quiz:write", "customer_service")
        assert "quiz:write" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_customer_service_passes_order_list(self):
        """customer_service has order:list."""
        admin = await _resolve_permission_check("order:list", "customer_service")
        assert admin.role == "customer_service"

    @pytest.mark.asyncio
    async def test_finance_passes_order_write(self):
        """finance role has order:write."""
        admin = await _resolve_permission_check("order:write", "finance")
        assert admin.role == "finance"

    @pytest.mark.asyncio
    async def test_finance_denied_quiz_write(self):
        """finance role does NOT have quiz:write."""
        with pytest.raises(ForbiddenException) as exc_info:
            await _resolve_permission_check("quiz:write", "finance")
        assert "quiz:write" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_auditor_readonly_access(self):
        """auditor has read-only permissions (dashboard, user, order, quiz, content, course)."""
        for perm in ("dashboard:view", "user:list", "order:list",
                     "quiz:list", "content:list", "course:list"):
            admin = await _resolve_permission_check(perm, "auditor")
            assert admin.role == "auditor"

    @pytest.mark.asyncio
    async def test_auditor_denied_write(self):
        """auditor must be denied write permissions."""
        for perm in ("order:write", "quiz:write", "content:write", "course:write"):
            with pytest.raises(ForbiddenException):
                await _resolve_permission_check(perm, "auditor")
