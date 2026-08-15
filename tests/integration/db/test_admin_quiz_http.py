"""HTTP contract tests for the frozen /admin/quiz API."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.adapter.security import create_admin_access_token
from app.domain.community.src.index import QuizAdminAuditLog
from app.domain.user.src.index import AdminUser
from app.port.config import settings
from app.services.admin_quiz import AdminQuizService


pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


def _database_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    assert url.startswith("postgresql+asyncpg://")
    return url


@pytest.fixture
async def quiz_http_env(monkeypatch, tmp_path):
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
    monkeypatch.setattr("app.services.admin_quiz_v2.get_db_ctx", test_db_ctx)
    monkeypatch.setattr("app.middleware.auth.is_token_revoked", token_is_not_revoked)
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "QUIZ_IMPORT_STORAGE_TYPE", "local")

    async with factory() as db:
        admin = AdminUser(
            username=f"{prefix}_admin",
            password_hash="integration-test-only",
            role="super_admin",
            must_change_password=False,
        )
        db.add(admin)
        await db.commit()
        await db.refresh(admin)

    from app.adapter.database import get_db
    from app.main import app

    previous_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = override_get_db
    token = create_admin_access_token(
        admin.id,
        admin.username,
        admin.role,
        auth_version=admin.auth_version,
        session_mode="normal",
    )
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
                        "DELETE FROM quiz_admin_audit_log "
                        "WHERE admin_id = :admin_id OR ("
                        "  actor_type = 'system' AND object_type = 'import_job' "
                        "  AND object_id IN ("
                        "    SELECT id FROM quiz_import_job WHERE admin_id = :admin_id"
                        "  )"
                        ")"
                    ),
                    {"admin_id": admin.id},
                )
                await db.execute(
                    text(
                        "DELETE FROM quiz_import_error WHERE job_id IN ("
                        "SELECT id FROM quiz_import_job WHERE admin_id = :admin_id)"
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
        return SimpleNamespace(
            id=env.admin.id,
            role="auditor",
            is_active=True,
            _session_mode="normal",
        )

    env.app.dependency_overrides[get_current_admin] = admin_without_quiz_permission
    try:
        forbidden = await env.client.get("/admin/quiz/categories")
    finally:
        env.app.dependency_overrides.pop(get_current_admin, None)
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == 40101
    async with env.factory() as db:
        denial = (
            await db.execute(
                select(QuizAdminAuditLog).where(
                    QuizAdminAuditLog.admin_id == env.admin.id,
                    QuizAdminAuditLog.action == "permission.denied",
                    QuizAdminAuditLog.permission == "quiz:list",
                )
            )
        ).scalar_one()
        assert denial.result == "failed"

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


async def test_new_admin_quiz_operations_are_mounted_and_strict(quiz_http_env) -> None:
    env = quiz_http_env
    category_response = await env.client.post(
        "/admin/quiz/categories",
        headers=env.headers,
        json=_category_payload(env, "new_ops"),
    )
    assert category_response.status_code == 200, category_response.text
    category_id = category_response.json()["data"]["id"]

    preview = await env.client.get(
        f"/admin/quiz/categories/{category_id}/impact?action=delete",
        headers=env.headers,
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]["can_execute"] is True
    assert preview.json()["data"]["history_snapshot_affected"] is False

    invalid_preview = await env.client.get(
        f"/admin/quiz/categories/{category_id}/impact?action=delete&target_parent_id=1",
        headers=env.headers,
    )
    assert invalid_preview.status_code == 422
    assert invalid_preview.json()["code"] == 40001

    overview = await env.client.get("/admin/quiz/stats/overview", headers=env.headers)
    assert overview.status_code == 200, overview.text
    assert overview.json()["data"]["library_count"] >= 0
    assert "module_count" in overview.json()["data"]
    assert "knowledge_point_count" in overview.json()["data"]

    stats = await env.client.get(
        "/admin/quiz/stats/questions?library_id=999999999",
        headers=env.headers,
    )
    assert stats.status_code == 200, stats.text
    assert stats.json()["data"]["total"] == 0

    too_many = await env.client.post(
        "/admin/quiz/questions/batch-publish",
        headers=env.headers,
        json={
            "items": [
                {"question_id": index, "lock_version": 1}
                for index in range(1, 102)
            ]
        },
    )
    assert too_many.status_code == 422
    assert too_many.json()["code"] == 40001

    missing_source = await env.client.get(
        "/admin/quiz/imports/999999999/source-url", headers=env.headers
    )
    assert missing_source.status_code == 404
    missing_retry = await env.client.post(
        "/admin/quiz/imports/999999999/retry", headers=env.headers
    )
    assert missing_retry.status_code == 404


async def test_import_errors_and_category_confirmation_http_workflow(
    quiz_http_env,
) -> None:
    env = quiz_http_env
    category_response = await env.client.post(
        "/admin/quiz/categories",
        headers=env.headers,
        json=_category_payload(env, "import_confirm"),
    )
    assert category_response.status_code == 200, category_response.text
    category = category_response.json()["data"]

    missing_name = f"{env.prefix}_missing_child"
    create_response = await env.client.post(
        "/admin/quiz/imports/json",
        headers=env.headers,
        json={
            "questions": [
                {
                    "category_path": [category["name"], missing_name],
                    "question_type": "judge",
                    "question_text": f"{env.prefix}_confirm_http_question",
                    "options": {"A": "正确", "B": "错误"},
                    "correct_answer": "A",
                    "explanation": None,
                }
            ]
        },
    )
    assert create_response.status_code == 200, create_response.text
    job = create_response.json()["data"]
    assert job["status"] == "queued"
    assert await AdminQuizService().process_import_job(job["id"]) is True

    impact_response = await env.client.get(
        f"/admin/quiz/imports/{job['id']}/category-impact",
        headers=env.headers,
    )
    assert impact_response.status_code == 200, impact_response.text
    impact = impact_response.json()["data"]
    assert impact["new_category_count"] == 1
    assert impact["affected_question_count"] == 1
    assert impact["tree"][0]["children"][0]["status"] == "will_create"

    stale_confirm = await env.client.post(
        f"/admin/quiz/imports/{job['id']}/confirm-categories",
        headers=env.headers,
        json={
            "lock_version": impact["lock_version"] + 1,
            "impact_version": impact["impact_version"],
        },
    )
    assert stale_confirm.status_code == 409
    assert stale_confirm.json()["code"] == 40201

    confirm_response = await env.client.post(
        f"/admin/quiz/imports/{job['id']}/confirm-categories",
        headers=env.headers,
        json={
            "lock_version": impact["lock_version"],
            "impact_version": impact["impact_version"],
        },
    )
    assert confirm_response.status_code == 200, confirm_response.text
    assert confirm_response.json()["data"]["status"] == "queued"
    assert await AdminQuizService().process_import_job(job["id"]) is True
    completed = await env.client.get(
        f"/admin/quiz/imports/{job['id']}",
        headers=env.headers,
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["data"]["status"] == "succeeded"
    assert completed.json()["data"]["created_count"] == 1

    duplicate_text = f"{env.prefix}_duplicate_http_question"
    duplicate_response = await env.client.post(
        "/admin/quiz/imports/json",
        headers=env.headers,
        json={
            "questions": [
                {
                    "category_path": [category["name"]],
                    "question_type": "judge",
                    "question_text": duplicate_text,
                    "options": {"A": "正确", "B": "错误"},
                    "correct_answer": "A",
                    "explanation": None,
                },
                {
                    "category_path": [category["name"]],
                    "question_type": "judge",
                    "question_text": duplicate_text,
                    "options": {"A": "正确", "B": "错误"},
                    "correct_answer": "A",
                    "explanation": None,
                },
            ]
        },
    )
    assert duplicate_response.status_code == 200, duplicate_response.text
    duplicate_job = duplicate_response.json()["data"]
    assert await AdminQuizService().process_import_job(duplicate_job["id"]) is True
    errors_response = await env.client.get(
        f"/admin/quiz/imports/{duplicate_job['id']}/errors?field=question_text&page=1",
        headers=env.headers,
    )
    assert errors_response.status_code == 200, errors_response.text
    errors = errors_response.json()["data"]
    assert errors["page_size"] == 50
    assert errors["total"] == 1
    assert errors["available_fields"] == ["question_text"]
    assert errors["items"][0]["field"] == "question_text"
    assert duplicate_text not in json.dumps(errors, ensure_ascii=False)

    cancel_response = await env.client.post(
        "/admin/quiz/imports/json",
        headers=env.headers,
        json={
            "questions": [
                {
                    "category_path": [f"{env.prefix}_cancel_http"],
                    "question_type": "judge",
                    "question_text": f"{env.prefix}_cancel_http_question",
                    "options": {"A": "正确", "B": "错误"},
                    "correct_answer": "B",
                    "explanation": None,
                }
            ]
        },
    )
    cancel_job = cancel_response.json()["data"]
    assert await AdminQuizService().process_import_job(cancel_job["id"]) is True
    cancel_impact = (
        await env.client.get(
            f"/admin/quiz/imports/{cancel_job['id']}/category-impact",
            headers=env.headers,
        )
    ).json()["data"]
    cancelled = await env.client.post(
        f"/admin/quiz/imports/{cancel_job['id']}/cancel",
        headers=env.headers,
        json={"lock_version": cancel_impact["lock_version"]},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["data"]["status"] == "cancelled"


async def test_audit_request_id_and_time_filters(quiz_http_env) -> None:
    env = quiz_http_env
    request_id = f"req-{env.prefix}"
    response = await env.client.post(
        "/admin/quiz/categories",
        headers={**env.headers, "X-Request-ID": request_id},
        json=_category_payload(env, "audit_filter"),
    )
    assert response.status_code == 200, response.text

    filtered = await env.client.get(
        f"/admin/quiz/audit-logs?request_id={request_id}",
        headers=env.headers,
    )
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["data"]["total"] == 1
    assert filtered.json()["data"]["items"][0]["request_id"] == request_id

    invalid_range = await env.client.get(
        "/admin/quiz/audit-logs"
        "?start_at=2026-08-12T12%3A00%3A00%2B08%3A00"
        "&end_at=2026-08-12T11%3A00%3A00%2B08%3A00",
        headers=env.headers,
    )
    assert invalid_range.status_code == 422
    assert invalid_range.json()["code"] == 40001
