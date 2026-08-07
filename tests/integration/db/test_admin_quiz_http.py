"""HTTP contract tests for the frozen /admin/quiz API."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.adapter.security import create_admin_access_token
from app.domain.user.src.index import AdminUser


pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


def _database_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    assert url.startswith("postgresql+asyncpg://")
    return url


@pytest.fixture
async def quiz_http_env(monkeypatch):
    engine = create_async_engine(_database_url(), pool_size=5, max_overflow=5)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"aqh_{uuid4().hex[:12]}"

    @asynccontextmanager
    async def test_db_ctx():
        async with factory() as session:
            yield session

    async def override_get_db():
        async with factory() as session:
            yield session

    async def token_is_not_revoked(_token: str) -> bool:
        return False

    monkeypatch.setattr("app.services.admin_quiz.get_db_ctx", test_db_ctx)
    monkeypatch.setattr("app.middleware.auth.is_token_revoked", token_is_not_revoked)

    async with factory() as db:
        admin = AdminUser(
            username=f"{prefix}_admin",
            password_hash="integration-test-only",
            role="super_admin",
        )
        db.add(admin)
        await db.commit()
        await db.refresh(admin)

    from app.adapter.database import get_db
    from app.main import app

    previous_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = override_get_db
    token = create_admin_access_token(admin.id, admin.username, admin.role)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        env = SimpleNamespace(
            app=app,
            client=client,
            factory=factory,
            prefix=prefix,
            admin=admin,
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            yield env
        finally:
            async with factory() as db:
                await db.execute(
                    text("DELETE FROM quiz_question WHERE created_by = :admin_id"),
                    {"admin_id": admin.id},
                )
                for depth in (3, 2, 1):
                    await db.execute(
                        text(
                            "DELETE FROM quiz_category "
                            "WHERE created_by = :admin_id AND depth = :depth"
                        ),
                        {"admin_id": admin.id, "depth": depth},
                    )
                await db.execute(
                    text(
                        "DELETE FROM quiz_admin_audit_log WHERE admin_id = :admin_id"
                    ),
                    {"admin_id": admin.id},
                )
                await db.execute(
                    text("DELETE FROM quiz_import_job WHERE admin_id = :admin_id"),
                    {"admin_id": admin.id},
                )
                await db.execute(
                    text("DELETE FROM admin_user WHERE id = :admin_id"),
                    {"admin_id": admin.id},
                )
                await db.commit()
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous_overrides)
            await engine.dispose()


def _category_payload(env, suffix: str) -> dict[str, object]:
    return {"name": f"{env.prefix}_{suffix}", "sort_order": 0}


def _question_payload(category_id: int, text_value: str) -> dict[str, object]:
    return {
        "category_id": category_id,
        "question_type": "single_choice",
        "question_text": text_value,
        "options": {"A": "答案一", "B": "答案二", "C": "答案三"},
        "correct_answer": "A",
        "explanation": "HTTP 集成测试解析",
    }


async def test_authentication_permission_and_real_route_prefix(quiz_http_env) -> None:
    env = quiz_http_env

    missing = await env.client.get("/admin/quiz/categories")
    assert missing.status_code == 401
    assert missing.json()["code"] == 40100

    invalid = await env.client.get(
        "/admin/quiz/categories",
        headers={"Authorization": "Bearer invalid-token"},
    )
    assert invalid.status_code == 401
    assert invalid.json()["code"] == 40100

    from app.middleware.auth import get_current_admin

    async def admin_without_quiz_permission():
        return SimpleNamespace(id=env.admin.id, role="auditor", is_active=True)

    env.app.dependency_overrides[get_current_admin] = admin_without_quiz_permission
    try:
        forbidden = await env.client.get("/admin/quiz/categories")
    finally:
        env.app.dependency_overrides.pop(get_current_admin, None)
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == 40101

    actual = await env.client.get("/admin/quiz/categories", headers=env.headers)
    assert actual.status_code == 200
    assert actual.json()["code"] == 0

    duplicate_prefix = await env.client.get(
        "/api/admin/quiz/categories",
        headers=env.headers,
    )
    assert duplicate_prefix.status_code == 404


async def test_parameter_validation_and_business_error_codes(quiz_http_env) -> None:
    env = quiz_http_env

    invalid_query = await env.client.get(
        "/admin/quiz/categories?parent_id=0",
        headers=env.headers,
    )
    assert invalid_query.status_code == 422
    assert invalid_query.json()["code"] == 40001

    invalid_body = await env.client.post(
        "/admin/quiz/categories",
        headers=env.headers,
        json={"name": ""},
    )
    assert invalid_body.status_code == 422
    assert invalid_body.json()["code"] == 40001

    missing_version = await env.client.request(
        "DELETE",
        "/admin/quiz/categories/999999999",
        headers=env.headers,
        json={},
    )
    assert missing_version.status_code == 422
    assert missing_version.json()["code"] == 40001
    assert any(
        item["field"] == "lock_version"
        for item in missing_version.json()["detail"]
    )

    missing_resource = await env.client.get(
        "/admin/quiz/questions/999999999/stats",
        headers=env.headers,
    )
    assert missing_resource.status_code == 404
    assert missing_resource.json()["code"] == 40300

    invalid_csv = await env.client.post(
        "/admin/quiz/imports/csv",
        headers=env.headers,
        data={"filename": "questions.txt", "size_bytes": "2"},
        files={"file": ("questions.txt", b"{}", "text/plain")},
    )
    assert invalid_csv.status_code == 422
    assert invalid_csv.json()["code"] == 40200

    oversized_csv = await env.client.post(
        "/admin/quiz/imports/csv",
        headers=env.headers,
        data={"filename": "questions.csv", "size_bytes": str(10 * 1024 * 1024 + 1)},
        files={"file": ("questions.csv", b"{}", "text/csv")},
    )
    assert oversized_csv.status_code == 422
    assert oversized_csv.json()["code"] == 40001


async def test_category_and_question_crud_state_machine_and_conflict(quiz_http_env) -> None:
    env = quiz_http_env
    created_category = await env.client.post(
        "/admin/quiz/categories",
        headers=env.headers,
        json=_category_payload(env, "crud"),
    )
    assert created_category.status_code == 200, created_category.text
    category = created_category.json()["data"]
    assert category["normalized_name"] == category["name"]
    assert category["lock_version"] == 1

    updated_category = await env.client.put(
        f"/admin/quiz/categories/{category['id']}",
        headers=env.headers,
        json={"lock_version": 1, "sort_order": 4},
    )
    assert updated_category.status_code == 200, updated_category.text
    assert updated_category.json()["data"]["lock_version"] == 2

    stale_category = await env.client.put(
        f"/admin/quiz/categories/{category['id']}",
        headers=env.headers,
        json={"lock_version": 1, "sort_order": 5},
    )
    assert stale_category.status_code == 409
    assert stale_category.json()["code"] == 40201

    created_question = await env.client.post(
        "/admin/quiz/questions",
        headers=env.headers,
        json=_question_payload(
            category["id"],
            f"{env.prefix}_HTTP 状态机题目？",
        ),
    )
    assert created_question.status_code == 200, created_question.text
    question = created_question.json()["data"]
    assert question["status"] == "draft"

    published = await env.client.post(
        f"/admin/quiz/questions/{question['id']}/publish",
        headers=env.headers,
        json={"lock_version": question["lock_version"]},
    )
    assert published.status_code == 200, published.text
    question = published.json()["data"]
    assert question["status"] == "published"

    cannot_delete = await env.client.request(
        "DELETE",
        f"/admin/quiz/questions/{question['id']}",
        headers=env.headers,
        json={"lock_version": question["lock_version"]},
    )
    assert cannot_delete.status_code == 422
    assert cannot_delete.json()["code"] == 40200

    disabled = await env.client.post(
        f"/admin/quiz/questions/{question['id']}/disable",
        headers=env.headers,
        json={"lock_version": question["lock_version"]},
    )
    assert disabled.status_code == 200, disabled.text
    question = disabled.json()["data"]
    assert question["status"] == "disabled"

    restored = await env.client.post(
        f"/admin/quiz/questions/{question['id']}/restore",
        headers=env.headers,
        json={"lock_version": question["lock_version"]},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["data"]["status"] == "published"

    disposable = await env.client.post(
        "/admin/quiz/questions",
        headers=env.headers,
        json=_question_payload(
            category["id"],
            f"{env.prefix}_可删除草稿？",
        ),
    )
    disposable_data = disposable.json()["data"]
    deleted = await env.client.request(
        "DELETE",
        f"/admin/quiz/questions/{disposable_data['id']}",
        headers=env.headers,
        json={"lock_version": disposable_data["lock_version"]},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["code"] == 0


async def test_batch_contract_and_removed_legacy_routes(quiz_http_env) -> None:
    env = quiz_http_env
    category_response = await env.client.post(
        "/admin/quiz/categories",
        headers=env.headers,
        json=_category_payload(env, "batch"),
    )
    category_id = category_response.json()["data"]["id"]
    questions = []
    for index in range(2):
        response = await env.client.post(
            "/admin/quiz/questions",
            headers=env.headers,
            json=_question_payload(
                category_id,
                f"{env.prefix}_批量题目_{index}？",
            ),
        )
        questions.append(response.json()["data"])

    batch = await env.client.post(
        "/admin/quiz/questions/batch-publish",
        headers=env.headers,
        json={
            "items": [
                {
                    "question_id": item["id"],
                    "lock_version": item["lock_version"],
                }
                for item in questions
            ]
        },
    )
    assert batch.status_code == 200, batch.text
    assert batch.json()["data"] == {
        "succeeded": True,
        "updated_count": 2,
        "errors": [],
    }

    duplicate_ids = await env.client.post(
        "/admin/quiz/questions/batch-disable",
        headers=env.headers,
        json={
            "items": [
                {"question_id": questions[0]["id"], "lock_version": 2},
                {"question_id": questions[0]["id"], "lock_version": 2},
            ]
        },
    )
    assert duplicate_ids.status_code == 422
    assert duplicate_ids.json()["code"] == 40001

    for path in (
        "/admin/quiz/questions/batch-delete",
        "/admin/quiz/import",
        "/admin/quiz/import/json",
    ):
        response = await env.client.post(path, headers=env.headers, json={})
        assert response.status_code == 404, (path, response.text)
