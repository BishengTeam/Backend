"""Versioned, non-editable administrator role definitions.

Roles are deliberately code-owned.  There is no database role catalogue and
no per-administrator permission override: adding a role requires a coordinated
Backend/Admin release and a database migration.
"""

SUPER_ADMIN_ROLE = "super_admin"
QUIZ_ADMIN_ROLE = "quiz_admin"

ADMIN_ROLES = (SUPER_ADMIN_ROLE, QUIZ_ADMIN_ROLE)

__all__ = ["ADMIN_ROLES", "QUIZ_ADMIN_ROLE", "SUPER_ADMIN_ROLE"]
