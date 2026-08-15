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

ROLES = ("super_admin", "quiz_admin")


def _make_admin_token(
    admin_id: int, username: str, role: str, *, auth_version: int = 1
) -> str:
    from app.adapter.security import create_admin_access_token
    return create_admin_access_token(
        admin_id, username, role, auth_version=auth_version
    )


@pytest.fixture
async def test_client():
    """Fixtures a FastAPI TestClient wired to the real test database."""
    from app.adapter.database import get_db

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
                must_change_password=False,
            )
            db.add(admin)
            await db.flush()
            token = _make_admin_token(
                admin.id,
                admin.username,
                admin.role,
                auth_version=admin.auth_version,
            )
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
        assert data["session_mode"] == "normal"
        assert data["must_change_password"] is False

    async def test_me_returns_role_specific_permissions(self, test_client):
        """quiz_admin gets explicit quiz-only permissions."""
        from app.domain.user.src.index import AdminUser

        client, factory, prefix = test_client

        async with factory() as db:
            admin = AdminUser(
                username=f"{prefix}_quiz_admin",
                password_hash="ignored_for_test",
                role="quiz_admin",
                must_change_password=False,
            )
            db.add(admin)
            await db.flush()
            token = _make_admin_token(
                admin.id,
                admin.username,
                admin.role,
                auth_version=admin.auth_version,
            )
            await db.commit()

        resp = await client.get("/admin/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        data = body["data"]
        assert data["admin"]["role"] == "quiz_admin"
        assert "quiz:write" in data["permissions"]
        assert "order:write" not in data["permissions"]
        assert "*" not in data["permissions"]
        assert data["session_mode"] == "normal"

    async def test_restricted_session_only_reaches_auth_completion_routes(
        self, test_client
    ):
        from app.adapter.security import create_admin_access_token
        from app.domain.user.src.index import AdminUser

        client, factory, prefix = test_client
        async with factory() as db:
            admin = AdminUser(
                username=f"{prefix}_restricted",
                password_hash="ignored-for-test",
                role="quiz_admin",
                must_change_password=True,
            )
            db.add(admin)
            await db.flush()
            token = create_admin_access_token(
                admin.id,
                admin.username,
                admin.role,
                auth_version=admin.auth_version,
                session_mode="restricted",
            )
            await db.commit()

        headers = {"Authorization": f"Bearer {token}"}
        me_response = await client.get("/admin/auth/me", headers=headers)
        assert me_response.status_code == 200
        assert me_response.json()["data"]["session_mode"] == "restricted"
        forbidden = await client.get("/admin/quiz/categories", headers=headers)
        assert forbidden.status_code == 403
        assert forbidden.json()["code"] == 40101

    async def test_me_rejects_invalid_token(self, test_client):
        """Invalid or missing token returns 401."""
        client, _, _ = test_client

        resp = await client.get("/admin/auth/me", headers={"Authorization": "Bearer bad-token"})
        assert resp.status_code == 401

    async def test_me_rejects_user_token(self, test_client):
        """A regular user JWT (type=access) is rejected by /admin/auth/me."""
        from app.adapter.security import create_access_token

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
    """POST /admin/auth/logout — revoke the authenticated admin token."""

    async def test_logout_returns_success(self, test_client, monkeypatch):
        """A valid admin token is revoked and receives the success contract."""
        from app.domain.user.src.index import AdminUser
        import app.api.admin.auth as admin_auth_api

        client, factory, prefix = test_client
        async with factory() as db:
            admin = AdminUser(
                username=f"{prefix}_logout",
                password_hash="ignored_for_test",
                role="quiz_admin",
                must_change_password=False,
            )
            db.add(admin)
            await db.flush()
            token = _make_admin_token(
                admin.id,
                admin.username,
                admin.role,
                auth_version=admin.auth_version,
            )
            await db.commit()

        revoked: list[str] = []

        async def _capture_revoke(value: str) -> bool:
            revoked.append(value)
            return True

        async def _skip_permanent_audit(_self, **_kwargs) -> None:
            # This endpoint test validates token revocation.  Permanent audit
            # persistence is covered against an isolated migration database;
            # do not leave an undeletable row in the shared integration DB.
            return None

        monkeypatch.setattr(admin_auth_api, "revoke_token", _capture_revoke)
        monkeypatch.setattr(
            admin_auth_api.AdminAuthService,
            "record_logout",
            _skip_permanent_audit,
        )
        resp = await client.post(
            "/admin/auth/logout",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["message"] == "已退出登录"
        assert revoked == [token]

    async def test_logout_requires_admin_auth(self, test_client):
        client, _, _ = test_client
        resp = await client.post("/admin/auth/logout")
        assert resp.status_code == 401
        assert resp.json()["code"] == 40100
