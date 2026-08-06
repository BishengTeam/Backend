"""Permission matrix for the frozen two-role administrator model."""


class TestRolePermissions:
    def test_only_super_and_normal_admin_roles_are_configured(self):
        from app.policy.permissions import ROLE_PERMISSIONS

        assert set(ROLE_PERMISSIONS) == {"super_admin", "admin"}

    def test_super_admin_has_wildcard(self):
        from app.policy.permissions import ROLE_PERMISSIONS

        assert ROLE_PERMISSIONS["super_admin"] == ["*"]

    def test_normal_admin_has_operational_permissions(self):
        from app.policy.permissions import ROLE_PERMISSIONS

        permissions = ROLE_PERMISSIONS["admin"]
        for permission in (
            "dashboard:view",
            "user:list",
            "user:write",
            "order:list",
            "content:write",
        ):
            assert permission in permissions

    def test_unknown_legacy_role_defaults_to_empty(self):
        from app.policy.permissions import ROLE_PERMISSIONS

        assert ROLE_PERMISSIONS.get("content_editor", []) == []
