"""
Test health-check endpoint.

Uses fastapi.testclient.TestClient against the real /health endpoint,
mocking _check_db and _check_redis to cover all four dependency states:

    db_ok  redis_ok  → code 0,       status "ok"
    db_ok  redis_bad → code 50000,   status "degraded"
    db_bad redis_ok  → code 50000,   status "degraded"
    db_bad redis_bad → code 50000,   status "degraded"

No real database or Redis connection is required.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import app.main  # noqa: F401  ensure app.main is importable for patching


# ---------------------------------------------------------------------------
# four-state matrix
# ---------------------------------------------------------------------------
class TestHealthEndpoint:
    @staticmethod
    def _get_body(db_ok: bool, redis_ok: bool) -> dict:
        """Perform a GET /health with the given check results and return JSON body."""
        with (
            patch("app.main._check_db", AsyncMock(return_value=db_ok)),
            patch("app.main._check_redis", AsyncMock(return_value=redis_ok)),
        ):
            from app.main import health
            return asyncio.run(health())

    def test_both_ok(self):
        """database up + redis up → code 0, status 'ok'"""
        body = self._get_body(db_ok=True, redis_ok=True)
        assert body["code"] == 0
        assert body["data"]["status"] == "ok"
        assert body["data"]["checks"]["database"] == "ok"
        assert body["data"]["checks"]["redis"] == "ok"

    def test_db_down_redis_up(self):
        """database down + redis up → code 50000, status 'degraded'"""
        body = self._get_body(db_ok=False, redis_ok=True)
        assert body["code"] == 50000
        assert body["data"]["status"] == "degraded"
        assert body["data"]["checks"]["database"] == "unavailable"
        assert body["data"]["checks"]["redis"] == "ok"

    def test_db_up_redis_down(self):
        """database up + redis down → code 50000, status 'degraded'"""
        body = self._get_body(db_ok=True, redis_ok=False)
        assert body["code"] == 50000
        assert body["data"]["status"] == "degraded"
        assert body["data"]["checks"]["database"] == "ok"
        assert body["data"]["checks"]["redis"] == "unavailable"

    def test_both_down(self):
        """database down + redis down → code 50000, status 'degraded'"""
        body = self._get_body(db_ok=False, redis_ok=False)
        assert body["code"] == 50000
        assert body["data"]["status"] == "degraded"
        assert body["data"]["checks"]["database"] == "unavailable"
        assert body["data"]["checks"]["redis"] == "unavailable"

    def test_health_has_expected_keys(self):
        """Structural check: the response shape matches the contract."""
        body = self._get_body(db_ok=True, redis_ok=True)
        assert "code" in body
        assert "message" in body
        assert "data" in body
        assert "checks" in body["data"]
        assert "database" in body["data"]["checks"]
        assert "redis" in body["data"]["checks"]
