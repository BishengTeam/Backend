"""Versioned, non-editable administrator role definitions.

Roles are deliberately code-owned.  There is no database role catalogue.
Per-administrator permission overrides are stored on ``admin_user.permissions``
(see ``app.policy.permissions``); adding a role still requires a coordinated
Backend/Admin release and a database migration.
"""

SUPER_ADMIN_ROLE = "super_admin"
QUIZ_ADMIN_ROLE = "quiz_admin"
CERT_ADMIN_ROLE = "cert_admin"
COURSE_ADMIN_ROLE = "course_admin"
TEACHER_ROLE = "teacher"
OPERATIONS_ADMIN_ROLE = "operations_admin"

ADMIN_ROLES = (
    SUPER_ADMIN_ROLE,
    QUIZ_ADMIN_ROLE,
    CERT_ADMIN_ROLE,
    COURSE_ADMIN_ROLE,
    TEACHER_ROLE,
    OPERATIONS_ADMIN_ROLE,
)

__all__ = [
    "ADMIN_ROLES",
    "COURSE_ADMIN_ROLE",
    "OPERATIONS_ADMIN_ROLE",
    "TEACHER_ROLE",
    "CERT_ADMIN_ROLE",
    "QUIZ_ADMIN_ROLE",
    "SUPER_ADMIN_ROLE",
]
