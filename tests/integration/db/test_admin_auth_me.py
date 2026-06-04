"""Integration tests for GET /admin/auth/me endpoint.

Validates AdminInfo serialization and ROLE_PERMISSIONS mapping across all
admin roles, exercising the same code path as the /me endpoint.
"""

import os

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]

ROLES = ("super_admin", "content_editor", "customer_service", "finance", "auditor")


@pytest.fixture
async def db_factory():
    """Session factory with a unique username prefix for isolation."""
    from uuid import uuid4

    engine = create_async_engine(os.environ["TEST_DATABASE_URL"], echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"am_{uuid4().hex[:12]}"
    try:
        yield factory, prefix
    finally:
        from sqlalchemy import text

        async with factory() as db:
            pl = f"{prefix}%"
            await db.execute(
                text("DELETE FROM admin_user WHERE username LIKE :pl"), {"pl": pl}
            )
            await db.commit()
        await engine.dispose()


class TestAdminAuthMe:
    """Covers AdminInfo.model_validate + ROLE_PERMISSIONS for every role."""

    async def test_admin_info_serialization_for_all_roles(self, db_factory):
        """AdminInfo.model_validate correctly serializes each AdminUser role."""
        from app.models.admin_user import AdminUser
        from app.schemas.admin import AdminInfo

        factory, prefix = db_factory

        admins: list[AdminUser] = []
        for role in ROLES:
            admin = AdminUser(
                username=f"{prefix}_{role}",
                password_hash="integration_test_hash",
                role=role,
            )
            admins.append(admin)

        async with factory() as db:
            db.add_all(admins)
            await db.flush()

            for admin in admins:
                info = AdminInfo.model_validate(admin)
                assert info.id == admin.id
                assert info.username == admin.username
                assert info.role == admin.role
                assert isinstance(info.id, int)
                assert isinstance(info.username, str)
                assert isinstance(info.role, str)

    async def test_role_permissions_mapping(self, db_factory):
        """ROLE_PERMISSIONS.get(admin.role, []) returns the correct list."""
        from app.models.admin_user import AdminUser
        from app.policy.permissions import ROLE_PERMISSIONS

        factory, prefix = db_factory

        admins: list[AdminUser] = []
        for role in ROLES:
            admin = AdminUser(
                username=f"{prefix}_{role}",
                password_hash="integration_test_hash",
                role=role,
            )
            admins.append(admin)

        async with factory() as db:
            db.add_all(admins)
            await db.flush()

            for admin in admins:
                permissions = ROLE_PERMISSIONS.get(admin.role, [])
                assert isinstance(permissions, list), f"permissions for {admin.role} should be a list"

                if admin.role == "super_admin":
                    assert permissions == ["*"], (
                        f"super_admin should have ['*'], got {permissions}"
                    )
                else:
                    assert "dashboard:view" in permissions, (
                        f"{admin.role} missing dashboard:view"
                    )
