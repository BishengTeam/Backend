"""Permission matrix for the frozen two-role administrator model."""


class TestRolePermissions:
    def test_only_frozen_system_roles_are_configured(self):
        from app.policy.permissions import ROLE_PERMISSIONS

        assert set(ROLE_PERMISSIONS) == {
            "super_admin",
            "quiz_admin",
            "cert_admin",
            "course_admin",
            "teacher",
            "operations_admin",
        }

    def test_super_admin_has_wildcard(self):
        from app.policy.permissions import ROLE_PERMISSIONS

        assert ROLE_PERMISSIONS["super_admin"] == ["*"]

    def test_quiz_admin_has_only_quiz_operational_permissions(self):
        from app.policy.permissions import ROLE_PERMISSIONS

        permissions = ROLE_PERMISSIONS["quiz_admin"]
        assert permissions == ["quiz:manage"]
        for forbidden in (
            "dashboard:view",
            "user:manage",
            "order:manage",
            "content:manage",
            "course:manage",
            "cert:h3c",
        ):
            assert forbidden not in permissions

    def test_unknown_legacy_role_defaults_to_empty(self):
        from app.policy.permissions import ROLE_PERMISSIONS

        assert ROLE_PERMISSIONS.get("content_editor", []) == []

    def test_course_admin_has_only_course_operational_permissions(self):
        from app.policy.permissions import ROLE_PERMISSIONS

        permissions = ROLE_PERMISSIONS["course_admin"]
        assert permissions == ["course:manage", "quiz:course"]

    def test_cert_admin_manages_all_certification_business(self):
        from app.policy.permissions import ROLE_PERMISSIONS

        permissions = ROLE_PERMISSIONS["cert_admin"]
        assert set(permissions) == {
            "content:manage",
            "user:manage",
            "order:manage",
            "cert:h3c",
            "cert:nisp",
            "cert:renshe",
            "cert:sangfor",
        }
