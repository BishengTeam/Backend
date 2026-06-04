"""Unit tests for GET /admin/auth/me — ROLE_PERMISSIONS mapping.

These tests validate the permission matrix without database access,
covering all 5 admin roles and the default/unknown-role fallback.
"""

import pytest


class TestRolePermissions:
    """ROLE_PERMISSIONS dict correctness for every role."""

    def test_content_editor_has_quiz_write_not_order_write(self):
        """content_editor: quiz:write in permissions, order:write not in permissions."""
        from app.policy.permissions import ROLE_PERMISSIONS

        perms = ROLE_PERMISSIONS["content_editor"]
        assert "quiz:write" in perms, "content_editor should have quiz:write"
        assert "order:write" not in perms, "content_editor should NOT have order:write"

    def test_super_admin_has_wildcard(self):
        """super_admin permissions == ['*']."""
        from app.policy.permissions import ROLE_PERMISSIONS

        assert ROLE_PERMISSIONS["super_admin"] == ["*"]

    def test_unknown_role_defaults_to_empty(self):
        """ROLE_PERMISSIONS.get with unknown role returns []."""
        from app.policy.permissions import ROLE_PERMISSIONS

        assert ROLE_PERMISSIONS.get("nonexistent_role", []) == []

    def test_all_roles_have_dashboard_view(self):
        """Every non-super_admin role includes dashboard:view."""
        from app.policy.permissions import ROLE_PERMISSIONS

        for role in ("content_editor", "customer_service", "finance", "auditor"):
            assert "dashboard:view" in ROLE_PERMISSIONS[role], (
                f"{role} missing dashboard:view"
            )

    def test_auditor_is_read_only(self):
        """auditor permissions contain no :write entries."""
        from app.policy.permissions import ROLE_PERMISSIONS

        for perm in ROLE_PERMISSIONS["auditor"]:
            assert not perm.endswith(":write"), (
                f"auditor should not have write perm: {perm}"
            )

    def test_finance_has_order_write(self):
        """finance role includes order:write."""
        from app.policy.permissions import ROLE_PERMISSIONS

        assert "order:write" in ROLE_PERMISSIONS["finance"]

    def test_customer_service_permissions(self):
        """customer_service role has user:list and order:list."""
        from app.policy.permissions import ROLE_PERMISSIONS

        perms = ROLE_PERMISSIONS["customer_service"]
        assert "user:list" in perms
        assert "order:list" in perms
        assert "order:write" not in perms
