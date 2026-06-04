"""HTTP-layer integration tests for /admin/auth/me and /admin/auth/logout.

Requires TEST_DATABASE_URL and TEST_DATABASE_URL_SYNC environment variables.
Tests create admin users via direct DB insert, issue JWT tokens, and call
endpoints through FastAPI TestClient.
"""

import os
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]

ROLES = ("super_admin", "content_editor", "customer_service", "finance", "auditor")


def _make_admin_token(admin_id: int, username: str, role: str) -> str:
    from app.core.security import create_admin_access_token
    return create_admin_access_token(admin_id, username, role)


@pytest.fixture
async def test_client():
    """Fixtures a FastAPI TestClient wired to the real test database."""
    from app.core.database import get_db

    engine = create_async_engine(os.environ["TEST_DATABASE_URL"], echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"ah_{uuid4().hex[:12]}"

    async def _override_get_db():
        async with factory() as session:
            yield session

    # Build app without lifespan (no Redis / background tasks)
    from app.main import app
    app.dependency_overrides[get_db] = _override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            yield client, factory, prefix
        finally:
            from sqlalchemy import text
            async with factory() as db:
                pl = f"{prefix}%"
                await db.execute(text("DELETE FROM admin_user WHERE username LIKE :pl"), {"pl": pl})
                await db.commit()
            await engine.dispose()
            app.dependency_overrides.clear()


class TestAdminAuthMe:
    """GET /admin/auth/me — returns admin info + permissions for each role."""

    async def test_me_returns_admin_info_and_permissions(self, test_client):
        """Verify /me returns correct AdminInfo and ROLE_PERMISSIONS for super_admin."""
        from app.domain.user.src.index import AdminUser

        client, factory, prefix = test_client

        async with factory() as db:
            admin = AdminUser(
                username=f"{prefix}_sa",
                password_hash="ignored_for_test",
                role="super_admin",
            )
            db.add(admin)
            await db.flush()
            token = _make_admin_token(admin.id, admin.username, admin.role)
            await db.commit()

        resp = await client.get("/admin/auth/me", headers={"Authorization": f"Bearer {token}"})

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["code"] == 0
        data = body["data"]
        assert data["admin"]["id"] == admin.id
        assert data["admin"]["username"] == admin.username
        assert data["admin"]["role"] == "super_admin"
        assert data["permissions"] == ["*"]

    async def test_me_returns_role_specific_permissions(self, test_client):
        """content_editor gets quiz:write but not order:write in /me."""
        from app.domain.user.src.index import AdminUser

        client, factory, prefix = test_client

        async with factory() as db:
            admin = AdminUser(
                username=f"{prefix}_ce",
                password_hash="ignored_for_test",
                role="content_editor",
            )
            db.add(admin)
            await db.flush()
            token = _make_admin_token(admin.id, admin.username, admin.role)
            await db.commit()

        resp = await client.get("/admin/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        data = body["data"]
        assert data["admin"]["role"] == "content_editor"
        assert "quiz:write" in data["permissions"]
        assert "order:write" not in data["permissions"]

    async def test_me_rejects_invalid_token(self, test_client):
        """Invalid or missing token returns 401."""
        client, _, _ = test_client

        resp = await client.get("/admin/auth/me", headers={"Authorization": "Bearer bad-token"})
        assert resp.status_code == 401

    async def test_me_rejects_user_token(self, test_client):
        """A regular user JWT (type=access) is rejected by /admin/auth/me."""
        from app.core.security import create_access_token

        client, factory, prefix = test_client

        async with factory() as db:
            from app.domain.user.src.index import User
            user = User(openid=f"{prefix}_user", phone="13800000000")
            db.add(user)
            await db.flush()
            token = create_access_token(user.id, user.openid)
            await db.commit()

        resp = await client.get("/admin/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401


class TestAdminAuthLogout:
    """POST /admin/auth/logout — stateless logout endpoint."""

    async def test_logout_returns_success(self, test_client):
        """Logout returns code=0 even without auth (stateless endpoint)."""
        client, _, _ = test_client
        resp = await client.post("/admin/auth/logout")
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["message"] == "已退出登录"
