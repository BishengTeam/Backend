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
        }

    def test_super_admin_has_wildcard(self):
        from app.policy.permissions import ROLE_PERMISSIONS

        assert ROLE_PERMISSIONS["super_admin"] == ["*"]

    def test_quiz_admin_has_only_quiz_operational_permissions(self):
        from app.policy.permissions import ROLE_PERMISSIONS

        permissions = ROLE_PERMISSIONS["quiz_admin"]
        for permission in (
            "quiz:list",
            "quiz:write",
            "quiz:import",
            "quiz_content_edit",
            "quiz_content_publish",
            "quiz_library_manage",
            "course_quiz_bind",
        ):
            assert permission in permissions
        for forbidden in (
            "dashboard:view",
            "user:list",
            "order:list",
            "content:write",
            "course:list",
        ):
            assert forbidden not in permissions

    def test_unknown_legacy_role_defaults_to_empty(self):
        from app.policy.permissions import ROLE_PERMISSIONS

        assert ROLE_PERMISSIONS.get("content_editor", []) == []

    def test_course_admin_has_only_course_operational_permissions(self):
        from app.policy.permissions import ROLE_PERMISSIONS

        permissions = ROLE_PERMISSIONS["course_admin"]
        assert set(permissions) == {
            "course:read",
            "course:write",
            "course:publish",
        }

    def test_cert_admin_manages_all_certification_business(self):
        from app.policy.permissions import ROLE_PERMISSIONS

        permissions = ROLE_PERMISSIONS["cert_admin"]
        assert set(permissions) == {
            "content:read",
            "content:list",
            "content:write",
            "user:list",
            "user:write",
            "order:list",
            "h3c:batch_manage",
            "h3c:review",
            "h3c:export",
            "h3c:refund",
            "h3c:order_close",
        }
