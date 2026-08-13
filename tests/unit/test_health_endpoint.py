"""
Test health-check endpoint.

Calls the real /health handler while mocking the database fingerprint probe
and Redis check to cover all four dependency states:

    db_ok  redis_ok  → code 0,       status "ok"
    db_ok  redis_bad → code 50000,   status "degraded"
    db_bad redis_ok  → code 50000,   status "degraded"
    db_bad redis_bad → code 50000,   status "degraded"

No real database or Redis connection is required.
"""
import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import app.main  # noqa: F401  ensure app.main is importable for patching


def test_database_probe_returns_stable_one_way_identity() -> None:
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                one=lambda: ("wemini_app_acceptance", "test-system-id")
            )
        )
    )

    class _Context:
        async def __aenter__(self):
            return session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    with patch("app.main.get_db_ctx", return_value=_Context()):
        result = asyncio.run(app.main._database_probe())

    assert result == {
        "status": "ok",
        "fingerprint_sha256": hashlib.sha256(
            b"test-system-id/wemini_app_acceptance"
        ).hexdigest(),
    }
    statement = str(session.execute.await_args.args[0])
    assert "current_database" in statement
    assert "pg_control_system" in statement


def test_database_probe_fails_closed_without_exception_detail() -> None:
    class _Context:
        async def __aenter__(self):
            raise RuntimeError("database credential must not leak")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    with patch("app.main.get_db_ctx", return_value=_Context()):
        result = asyncio.run(app.main._database_probe())

    assert result == {"status": "unavailable"}


# ---------------------------------------------------------------------------
# four-state matrix
# ---------------------------------------------------------------------------
class TestHealthEndpoint:
    @staticmethod
    def _get_body(db_ok: bool, redis_ok: bool) -> dict:
        """Perform a GET /health with the given check results and return JSON body."""
        with (
            patch(
                "app.main._database_probe",
                AsyncMock(
                    return_value={
                        "status": "ok" if db_ok else "unavailable"
                    }
                ),
            ),
            patch("app.main._check_redis", AsyncMock(return_value=redis_ok)),
            patch(
                "app.main.read_quiz_task_snapshot",
                AsyncMock(
                    return_value={
                        "source": "process",
                        "heartbeat_at": None,
                        "processors": {},
                    }
                ),
            ),
            patch("app.main.quiz_task_snapshot_ready", return_value=True),
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
        assert "quiz_oss" in body["data"]["checks"]
        assert body["data"]["checks"]["quiz_worker"] == "ok"
