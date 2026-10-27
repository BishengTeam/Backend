"""The review batch selector preserves the NISP review permission boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from httpx import ASGITransport, AsyncClient

from app.adapter.database import get_db
from app.api.admin import nisp as nisp_api
from app.middleware import auth
from app.middleware.error_handler import (
    app_exception_handler,
    validation_exception_handler,
)
from app.port.exceptions import AppException


pytestmark = pytest.mark.asyncio


@pytest.fixture
async def review_client(monkeypatch):
    # Exercise the real login and permission dependencies without opening a DB
    # connection. A reviewer-only role must not inherit batch management.
    monkeypatch.setitem(auth.ROLE_PERMISSIONS, "nisp_reviewer_only", ["nisp:review"])
    principal = SimpleNamespace(
        id=7,
        role="nisp_reviewer_only",
        is_active=True,
        auth_version=1,
        must_change_password=False,
    )
    db = SimpleNamespace(get=AsyncMock(return_value=principal))

    async def database():
        yield db

    monkeypatch.setattr(auth, "decode_access_token", lambda token: {
        "type": "admin",
        "admin_id": 7,
        "auth_version": 1,
        "jti": "review-options-test",
        "session_mode": "normal",
    })
    monkeypatch.setattr(auth, "is_token_revoked", AsyncMock(return_value=False))
    options = AsyncMock(return_value=([
        {"id": 18, "name": "历史考试批次", "level": "2", "status": "archived"},
    ], 1))
    monkeypatch.setattr(nisp_api._service, "list_review_batch_options", options)
    batches = AsyncMock(return_value=([], 0))
    monkeypatch.setattr(nisp_api._service, "list_batches", batches)
    app = FastAPI()
    app.include_router(nisp_api.router, prefix="/admin")
    app.dependency_overrides[get_db] = database
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer test-review-token"},
    ) as client:
        yield SimpleNamespace(
            client=client,
            principal=principal,
            options=options,
            batches=batches,
            db=db,
        )


async def test_reviewer_can_read_lightweight_options_with_default_pagination(review_client):
    response = await review_client.client.get("/admin/nisp/review-batch-options")

    assert response.status_code == 200
    assert response.json() == {
        "code": 0,
        "message": "ok",
        "data": {
            "items": [{
                "id": 18,
                "name": "历史考试批次",
                "level": "2",
                "status": "archived",
            }],
            "total": 1,
            "page": 1,
            "page_size": 20,
        },
    }
    review_client.options.assert_awaited_once_with(keyword=None, page=1, page_size=20)
    review_client.db.get.assert_awaited_once()


async def test_options_forward_search_and_pagination(review_client):
    response = await review_client.client.get(
        "/admin/nisp/review-batch-options",
        params={"keyword": "#18", "page": 2, "page_size": 100},
    )

    assert response.status_code == 200
    assert response.json()["data"]["page"] == 2
    assert response.json()["data"]["page_size"] == 100
    review_client.options.assert_awaited_once_with(keyword="#18", page=2, page_size=100)


async def test_options_reject_unauthenticated_request(review_client):
    review_client.client.headers.pop("Authorization")

    response = await review_client.client.get("/admin/nisp/review-batch-options")

    assert response.status_code == 401
    assert response.json()["code"] == 40100
    review_client.options.assert_not_awaited()


async def test_options_require_review_permission(review_client):
    review_client.principal.role = "course_admin"

    response = await review_client.client.get("/admin/nisp/review-batch-options")

    assert response.status_code == 403
    assert response.json()["message"] == "缺少权限: nisp:review"
    review_client.options.assert_not_awaited()


async def test_reviewer_options_do_not_grant_batch_management(review_client):
    response = await review_client.client.get("/admin/nisp/batches")

    assert response.status_code == 403
    assert response.json()["message"] == "缺少权限: nisp:batch_manage"
    review_client.batches.assert_not_awaited()


@pytest.mark.parametrize("params, field", [
    ({"page": 0}, "page"),
    ({"page_size": 0}, "page_size"),
    ({"page_size": 101}, "page_size"),
    ({"keyword": "批" * 65}, "keyword"),
])
async def test_options_validate_query_bounds(review_client, params, field):
    response = await review_client.client.get(
        "/admin/nisp/review-batch-options", params=params,
    )

    assert response.status_code == 422
    assert response.json()["code"] == 40001
    assert any(error["field"] == field for error in response.json()["detail"])
    review_client.options.assert_not_awaited()
