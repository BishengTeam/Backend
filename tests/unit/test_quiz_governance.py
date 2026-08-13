"""Unit-level acceptance checks for QB-30 through QB-34."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.requests import Request as StarletteRequest

from app.adapter.security import create_access_token
from app.contracts.quiz import DELETED_QUIZ_ENDPOINTS, QUIZ_API_CONTRACTS
from app.main import app
from app.middleware.error_handler import rate_limit_exception_handler
from app.middleware.rate_limit import ResilientLimiter, quiz_admin_key, quiz_user_key
from app.adapter.security import create_admin_access_token
from app.services.quiz_tasks import QuizTaskRegistry


def _request(*, authorization: str | None = None, host: str = "127.0.0.1"):
    headers: list[tuple[bytes, bytes]] = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode("ascii")))
    return StarletteRequest(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/quiz/questions",
            "raw_path": b"/api/quiz/questions",
            "query_string": b"",
            "headers": headers,
            "client": (host, 12345),
            "server": ("testserver", 80),
        }
    )


def test_quiz_rate_limit_key_is_per_authenticated_user() -> None:
    first = create_access_token(17, "openid-17")
    same_user = create_access_token(17, "openid-17-second-login")
    other_user = create_access_token(18, "openid-18")

    assert quiz_user_key(_request(authorization=f"Bearer {first}")) == "quiz:user:17"
    assert quiz_user_key(_request(authorization=f"Bearer {same_user}")) == "quiz:user:17"
    assert quiz_user_key(_request(authorization=f"Bearer {other_user}")) == "quiz:user:18"
    assert quiz_user_key(_request(host="192.0.2.9")) == "quiz:anonymous:192.0.2.9"


def test_quiz_admin_rate_limit_key_is_per_authenticated_admin() -> None:
    first = create_admin_access_token(31, "admin-31", "admin")
    same_admin = create_admin_access_token(31, "renamed-admin-31", "admin")
    other_admin = create_admin_access_token(32, "admin-32", "super_admin")

    assert quiz_admin_key(_request(authorization=f"Bearer {first}")) == "quiz:admin:31"
    assert quiz_admin_key(_request(authorization=f"Bearer {same_admin}")) == "quiz:admin:31"
    assert quiz_admin_key(_request(authorization=f"Bearer {other_admin}")) == "quiz:admin:32"


@pytest.mark.asyncio
async def test_slowapi_limit_returns_frozen_429_without_calling_business_twice() -> None:
    limiter = ResilientLimiter(
        key_func=lambda request: "test-user",
        storage_uri="memory://",
        headers_enabled=False,
    )
    test_app = FastAPI()
    test_app.state.limiter = limiter
    test_app.add_exception_handler(RateLimitExceeded, rate_limit_exception_handler)
    calls = 0

    @test_app.get("/limited")
    @limiter.limit("1/minute", key_func=lambda request: "quiz:user:17")
    async def limited(request: Request):
        nonlocal calls
        calls += 1
        return {"calls": calls}

    test_app.add_middleware(SlowAPIMiddleware)
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/limited")
        second = await client.get("/limited")

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json() == {
        "code": 40202,
        "message": "请求过于频繁，请稍后再试",
        "data": None,
    }
    assert second.headers["retry-after"] == "60"
    assert calls == 1


def test_runtime_openapi_matches_all_frozen_quiz_operations() -> None:
    app.openapi_schema = None
    schema = app.openapi()
    paths = schema["paths"]

    for contract in QUIZ_API_CONTRACTS:
        operation = paths[contract.path][contract.method.lower()]
        assert operation["x-quiz-contract-version"] == "2026-08-13"
        assert operation["x-error-codes"]
        success_response = operation["responses"]["200"]
        assert success_response["content"]["application/json"]["schema"]
        if contract.auth == "public":
            assert not operation.get("security")
        else:
            assert {"BearerAuth": []} in operation["security"]
        if contract.rate_limit_per_minute is not None:
            assert (
                operation["x-rate-limit-per-minute"]
                == contract.rate_limit_per_minute
            )
            assert "429" in operation["responses"]

    for method, path in DELETED_QUIZ_ENDPOINTS:
        assert path not in paths or method.lower() not in paths[path]


@pytest.mark.asyncio
async def test_task_registry_exposes_real_failure_retry_and_queue_metrics() -> None:
    registry = QuizTaskRegistry()

    async def queue_depth() -> int:
        return 4

    async def succeeds() -> bool:
        return True

    async def fails() -> bool:
        raise RuntimeError("test failure")

    registry.register("success", succeeds, queue_depth=queue_depth)
    registry.register("failure", fails)

    assert await registry.run_once() is True
    snapshot = registry.snapshot()
    success = snapshot["processors"]["success"]
    failure = snapshot["processors"]["failure"]

    assert snapshot["heartbeat_at"] is not None
    assert success["queue_depth"] == 4
    assert success["runs"] == success["successes"] == 1
    assert success["did_work"] is True
    assert failure["runs"] == failure["failures"] == failure["retries"] == 1
    assert failure["last_error_type"] == "RuntimeError"
    assert failure["last_runtime_seconds"] is not None
