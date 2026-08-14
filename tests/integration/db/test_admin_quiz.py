"""Frozen-contract PostgreSQL integration tests for admin quiz workflows."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.community.src.index import (
    QuizAdminAuditLog,
    QuizCategory,
    QuizExam,
    QuizExamAnswer,
    QuizExamQuestion,
    QuizImportError,
    QuizImportJob,
    QuizPracticeAttempt,
    QuizPracticeSession,
    QuizPracticeSessionQuestion,
    QuizQuestion,
    QuizQuestionStats,
)
from app.domain.user.src.index import AdminUser, User
from app.port.config import settings
from app.port.exceptions import BusinessException, ConflictException
from app.schemas.admin_quiz_contract import (
    AdminQuizBatchRequest,
    AdminQuizCategoryCreate,
    AdminQuizCategoryImpactQuery,
    AdminQuizCategoryStatusUpdate,
    AdminQuizCategoryUpdate,
    AdminQuizImportCancelRequest,
    AdminQuizImportConfirmCategoriesRequest,
    AdminQuizImportErrorQuery,
    AdminQuizQuestionCreate,
    AdminQuizQuestionUpdate,
    AdminQuizVersionRequest,
)
from app.services.admin_quiz import AdminQuizService
from scripts.quiz_acceptance_fixtures import build_fixture_set


pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


def _database_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    assert url.startswith("postgresql+asyncpg://")
    return url


@pytest.fixture
async def quiz_env(monkeypatch, tmp_path):
    engine = create_async_engine(_database_url(), pool_size=5, max_overflow=5)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"aq_{uuid4().hex[:12]}"

    @asynccontextmanager
    async def test_db_ctx():
        async with factory() as session:
            yield session

    async def run_inline(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("app.services.admin_quiz.get_db_ctx", test_db_ctx)
    monkeypatch.setattr("app.services.admin_quiz.asyncio.to_thread", run_inline)
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "QUIZ_IMPORT_STORAGE_TYPE", "local")

    async with factory() as db:
        admin = AdminUser(
            username=f"{prefix}_admin",
            password_hash="integration-test-only",
            role="super_admin",
        )
        db.add(admin)
        await db.commit()
        await db.refresh(admin)

    env = SimpleNamespace(
        service=AdminQuizService(),
        factory=factory,
        prefix=prefix,
        admin_id=admin.id,
    )
    try:
        yield env
    finally:
        async with factory() as db:
            await db.execute(
                text('DELETE FROM "user" WHERE openid LIKE :pattern'),
                {"pattern": f"{prefix}%"},
            )
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
        await engine.dispose()


async def _category(
    env,
    suffix: str,
    *,
    parent_id: int | None = None,
) -> QuizCategory:
    return await env.service.create_category(
        AdminQuizCategoryCreate(
            name=f"{env.prefix}_{suffix}",
            parent_id=parent_id,
        ),
        admin_id=env.admin_id,
    )


async def _question(
    env,
    category_id: int,
    suffix: str,
    *,
    explanation: str | None = "集成测试解析",
) -> QuizQuestion:
    return await env.service.create_question(
        AdminQuizQuestionCreate(
            category_id=category_id,
            question_type="single_choice",
            question_text=f"{env.prefix}_{suffix} 的正确答案是什么？",
            options={"A": "答案一", "B": "答案二", "C": "答案三"},
            correct_answer="A",
            explanation=explanation,
        ),
        admin_id=env.admin_id,
    )


async def test_category_cycles_depth_moves_and_stale_versions(quiz_env) -> None:
    env = quiz_env
    root = await _category(env, "root")
    child = await _category(env, "child", parent_id=root.id)
    grandchild = await _category(env, "grandchild", parent_id=child.id)
    other_root = await _category(env, "other_root")
    other_child = await _category(env, "other_child", parent_id=other_root.id)

    with pytest.raises(BusinessException, match="子分类"):
        await env.service.update_category(
            root.id,
            AdminQuizCategoryUpdate(
                lock_version=root.lock_version,
                parent_id=grandchild.id,
            ),
            admin_id=env.admin_id,
        )

    with pytest.raises(BusinessException, match="三级"):
        await env.service.update_category(
            other_root.id,
            AdminQuizCategoryUpdate(
                lock_version=other_root.lock_version,
                parent_id=child.id,
            ),
            admin_id=env.admin_id,
        )

    updated = await env.service.update_category(
        root.id,
        AdminQuizCategoryUpdate(lock_version=root.lock_version, sort_order=9),
        admin_id=env.admin_id,
    )
    assert updated.lock_version == root.lock_version + 1

    with pytest.raises(ConflictException) as conflict:
        await env.service.update_category(
            root.id,
            AdminQuizCategoryUpdate(lock_version=root.lock_version, sort_order=10),
            admin_id=env.admin_id,
        )
    assert conflict.value.code == 40201
    assert conflict.value.http_status_code == 409

    async with env.factory() as db:
        persisted_other_root = await db.get(QuizCategory, other_root.id)
        persisted_other_child = await db.get(QuizCategory, other_child.id)
        assert persisted_other_root.parent_id is None
        assert persisted_other_root.depth == 1
        assert persisted_other_child.parent_id == other_root.id
        assert persisted_other_child.depth == 2


async def test_question_state_machine_delete_rule_and_audit(quiz_env) -> None:
    env = quiz_env
    category = await _category(env, "state")
    question = await _question(env, category.id, "state_question")

    category = await env.service.update_category_status(
        category.id,
        AdminQuizCategoryStatusUpdate(
            status="disabled",
            lock_version=category.lock_version,
        ),
        admin_id=env.admin_id,
    )
    with pytest.raises(BusinessException, match="分类"):
        await env.service.publish_question(
            question.id,
            AdminQuizVersionRequest(lock_version=question.lock_version),
            admin_id=env.admin_id,
        )

    category = await env.service.update_category_status(
        category.id,
        AdminQuizCategoryStatusUpdate(
            status="active",
            lock_version=category.lock_version,
        ),
        admin_id=env.admin_id,
    )
    published = await env.service.publish_question(
        question.id,
        AdminQuizVersionRequest(lock_version=question.lock_version),
        admin_id=env.admin_id,
    )
    assert published.status == "published"
    assert published.ever_published is True
    assert published.published_at is not None

    with pytest.raises(BusinessException, match="未发布草稿"):
        await env.service.delete_question(
            published.id,
            published.lock_version,
            admin_id=env.admin_id,
        )

    disabled = await env.service.disable_question(
        published.id,
        AdminQuizVersionRequest(lock_version=published.lock_version),
        admin_id=env.admin_id,
    )
    assert disabled.status == "disabled"
    assert disabled.disabled_at is not None

    restored = await env.service.restore_question(
        disabled.id,
        AdminQuizVersionRequest(lock_version=disabled.lock_version),
        admin_id=env.admin_id,
    )
    assert restored.status == "published"
    assert restored.published_at == published.published_at

    edited = await env.service.update_question(
        restored.id,
        AdminQuizQuestionUpdate(
            lock_version=restored.lock_version,
            question_text=f"{env.prefix}_发布后修改的题干？",
        ),
        admin_id=env.admin_id,
    )
    assert edited.status == "published"

    disposable = await _question(env, category.id, "disposable")
    await env.service.delete_question(
        disposable.id,
        disposable.lock_version,
        admin_id=env.admin_id,
    )

    async with env.factory() as db:
        assert await db.get(QuizQuestion, disposable.id) is None
        update_audit = (
            await db.execute(
                select(QuizAdminAuditLog).where(
                    QuizAdminAuditLog.admin_id == env.admin_id,
                    QuizAdminAuditLog.action == "question.update",
                    QuizAdminAuditLog.object_id == edited.id,
                )
            )
        ).scalar_one()
        assert update_audit.changed_fields["question_text"] == {
            "before": question.question_text,
            "after": edited.question_text,
        }
        delete_audit = (
            await db.execute(
                select(QuizAdminAuditLog).where(
                    QuizAdminAuditLog.action == "question.delete",
                    QuizAdminAuditLog.object_id == disposable.id,
                )
            )
        ).scalar_one()
        assert delete_audit.changed_fields["status"]["before"] == "draft"


async def test_batch_publish_and_disable_are_atomic(quiz_env) -> None:
    env = quiz_env
    category = await _category(env, "batch")
    valid = await _question(env, category.id, "valid")
    incomplete = await _question(
        env,
        category.id,
        "incomplete",
        explanation=None,
    )

    failed_publish = await env.service.batch_publish_questions(
        AdminQuizBatchRequest(
            items=[
                {"question_id": valid.id, "lock_version": valid.lock_version},
                {
                    "question_id": incomplete.id,
                    "lock_version": incomplete.lock_version,
                },
            ]
        ),
        admin_id=env.admin_id,
    )
    assert failed_publish.succeeded is False
    assert failed_publish.updated_count == 0

    async with env.factory() as db:
        assert (await db.get(QuizQuestion, valid.id)).status == "draft"
        assert (await db.get(QuizQuestion, incomplete.id)).status == "draft"

    incomplete = await env.service.update_question(
        incomplete.id,
        AdminQuizQuestionUpdate(
            lock_version=incomplete.lock_version,
            explanation="补齐后的解析",
        ),
        admin_id=env.admin_id,
    )
    published = await env.service.batch_publish_questions(
        AdminQuizBatchRequest(
            items=[
                {"question_id": valid.id, "lock_version": valid.lock_version},
                {
                    "question_id": incomplete.id,
                    "lock_version": incomplete.lock_version,
                },
            ]
        ),
        admin_id=env.admin_id,
    )
    assert published.succeeded is True
    assert published.updated_count == 2

    failed_disable = await env.service.batch_disable_questions(
        AdminQuizBatchRequest(
            items=[
                {"question_id": valid.id, "lock_version": valid.lock_version + 1},
                {
                    "question_id": incomplete.id,
                    "lock_version": incomplete.lock_version,
                },
            ]
        ),
        admin_id=env.admin_id,
    )
    assert failed_disable.succeeded is False
    assert failed_disable.updated_count == 0
    assert any(error.code == 40201 for error in failed_disable.errors)

    async with env.factory() as db:
        valid_row = await db.get(QuizQuestion, valid.id)
        incomplete_row = await db.get(QuizQuestion, incomplete.id)
        assert valid_row.status == incomplete_row.status == "published"
        valid_version = valid_row.lock_version
        incomplete_version = incomplete_row.lock_version

    disabled = await env.service.batch_disable_questions(
        AdminQuizBatchRequest(
            items=[
                {"question_id": valid.id, "lock_version": valid_version},
                {
                    "question_id": incomplete.id,
                    "lock_version": incomplete_version,
                },
            ]
        ),
        admin_id=env.admin_id,
    )
    assert disabled.succeeded is True
    assert disabled.updated_count == 2


async def test_category_impact_preview_is_read_only_and_reports_blockers(quiz_env) -> None:
    env = quiz_env
    root = await _category(env, "impact_root")
    child = await env.service.create_category(
        AdminQuizCategoryCreate(
            name=f"{env.prefix}_impact_child",
            parent_id=root.id,
        ),
        admin_id=env.admin_id,
    )
    question = await _question(env, child.id, "impact_question")
    question = await env.service.publish_question(
        question.id,
        AdminQuizVersionRequest(lock_version=question.lock_version),
        admin_id=env.admin_id,
    )

    disable = await env.service.preview_category_impact(
        root.id, AdminQuizCategoryImpactQuery(action="disable")
    )
    assert disable.descendant_category_count == 1
    assert disable.published_question_count == 1
    assert disable.affected_new_pool_question_count == 1
    assert disable.history_snapshot_affected is False
    assert disable.can_execute is True

    delete = await env.service.preview_category_impact(
        root.id, AdminQuizCategoryImpactQuery(action="delete")
    )
    assert delete.can_execute is False
    assert any("子分类" in reason for reason in delete.blocking_reasons)
    assert any("题目" in reason for reason in delete.blocking_reasons)

    async with env.factory() as db:
        persisted_root = await db.get(QuizCategory, root.id)
        persisted_question = await db.get(QuizQuestion, question.id)
        assert persisted_root.status == "active"
        assert persisted_root.lock_version == root.lock_version
        assert persisted_question.status == "published"


async def test_category_move_impact_compares_old_and_new_ancestor_chains(quiz_env) -> None:
    env = quiz_env
    disabled_root = await _category(env, "impact_disabled_root")
    moving = await _category(
        env,
        "impact_moving",
        parent_id=disabled_root.id,
    )
    leaf = await _category(env, "impact_leaf", parent_id=moving.id)
    question = await _question(env, leaf.id, "impact_move_question")
    question = await env.service.publish_question(
        question.id,
        AdminQuizVersionRequest(lock_version=question.lock_version),
        admin_id=env.admin_id,
    )
    disabled_root = await env.service.update_category_status(
        disabled_root.id,
        AdminQuizCategoryStatusUpdate(
            status="disabled",
            lock_version=disabled_root.lock_version,
        ),
        admin_id=env.admin_id,
    )

    to_root = await env.service.preview_category_impact(
        moving.id,
        AdminQuizCategoryImpactQuery(action="move"),
    )
    assert to_root.can_execute is True
    assert to_root.affected_new_pool_question_count == 1

    active_root = await _category(env, "impact_active_root")
    to_active_root = await env.service.preview_category_impact(
        moving.id,
        AdminQuizCategoryImpactQuery(
            action="move",
            target_parent_id=active_root.id,
        ),
    )
    assert to_active_root.can_execute is True
    assert to_active_root.affected_new_pool_question_count == 1

    async with env.factory() as db:
        persisted = await db.get(QuizCategory, moving.id)
        assert persisted.parent_id == disabled_root.id
        assert persisted.lock_version == moving.lock_version


async def test_batch_contract_rejects_101_items() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AdminQuizBatchRequest(
            items=[
                {"question_id": index, "lock_version": 1}
                for index in range(1, 102)
            ]
        )


async def test_five_thousand_row_import_with_missing_category_waits_without_writes(
    quiz_env,
) -> None:
    env = quiz_env
    category = await _category(env, "import")
    questions = [
        {
            "category_path": [category.name],
            "question_type": "single_choice",
            "question_text": f"{env.prefix}_import_{index}",
            "options": {"A": "一", "B": "二", "C": "三"},
            "correct_answer": "A",
            "explanation": "导入解析",
        }
        for index in range(4999)
    ]
    questions.append(
        {
            "category_path": [f"{env.prefix}_missing"],
            "question_type": "judge",
            "question_text": f"{env.prefix}_invalid_category",
            "options": {"A": "正确", "B": "错误"},
            "correct_answer": "A",
            "explanation": "导入解析",
        }
    )
    content = json.dumps(
        {"questions": questions},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    job = await env.service.create_import_job(
        source_type="json",
        content=content,
        admin_id=env.admin_id,
        filename="questions.json",
    )
    assert await env.service.process_import_job(job.id) is True

    async with env.factory() as db:
        persisted = await db.get(QuizImportJob, job.id)
        created_count = await db.scalar(
            select(func.count()).select_from(QuizQuestion).where(
                QuizQuestion.category_id == category.id
            )
        )
        waiting_audit = (
            await db.execute(
                select(QuizAdminAuditLog).where(
                    QuizAdminAuditLog.action
                    == "import.awaiting_category_confirmation",
                    QuizAdminAuditLog.object_id == job.id,
                )
            )
        ).scalar_one()
        assert persisted.status == "awaiting_category_confirmation"
        assert persisted.total_rows == 5000
        assert persisted.created_count == 0
        assert persisted.error_count == 0
        assert persisted.report_object_key is None
        assert persisted.finished_at is None
        assert persisted.missing_category_count == 1
        assert persisted.affected_question_count == 5000
        assert created_count == 0
        assert waiting_audit.result == "succeeded"
        assert waiting_audit.changed_fields["missing_category_count"]["after"] == 1


async def test_qf55_frozen_import_fixtures_succeed_and_roll_back_atomically(
    quiz_env,
) -> None:
    """Run the frozen QF-55 files through real PostgreSQL transactions."""

    env = quiz_env
    artifacts, manifest = build_fixture_set()
    category_spec = json.loads(artifacts["categories.json"])["categories"]
    categories: dict[str, QuizCategory] = {}
    for item in category_spec:
        parent = categories.get(item["parent_ref"])
        category = await env.service.create_category(
            AdminQuizCategoryCreate(
                name=item["name"],
                parent_id=parent.id if parent is not None else None,
                sort_order=item["sort_order"],
            ),
            admin_id=env.admin_id,
        )
        categories[item["ref"]] = category

    disabled = categories["disabled_leaf"]
    categories["disabled_leaf"] = await env.service.update_category_status(
        disabled.id,
        AdminQuizCategoryStatusUpdate(
            status="disabled",
            lock_version=disabled.lock_version,
        ),
        admin_id=env.admin_id,
    )
    assert categories["import_leaf"].depth == 3
    assert categories["practice_leaf"].depth == 3
    assert categories["exam_leaf"].depth == 3

    success_jobs: list[QuizImportJob] = []
    for source_type in ("json", "csv"):
        name = f"import-success-5000.{source_type}"
        assert manifest["artifacts"][name]["row_or_case_count"] == 5000
        job = await env.service.create_import_job(
            source_type=source_type,
            content=artifacts[name],
            admin_id=env.admin_id,
            filename=name,
        )
        assert await env.service.process_import_job(job.id) is True
        success_jobs.append(job)

    async with env.factory() as db:
        persisted_success = [
            await db.get(QuizImportJob, job.id) for job in success_jobs
        ]
        imported = await db.scalar(
            select(func.count())
            .select_from(QuizQuestion)
            .where(
                QuizQuestion.category_id == categories["import_leaf"].id,
                QuizQuestion.status == "draft",
                QuizQuestion.question_text.like("QF55-V1-%"),
            )
        )
        type_counts = dict(
            (
                await db.execute(
                    select(QuizQuestion.question_type, func.count())
                    .where(
                        QuizQuestion.category_id == categories["import_leaf"].id,
                        QuizQuestion.question_text.like("QF55-V1-%"),
                    )
                    .group_by(QuizQuestion.question_type)
                )
            ).all()
        )
        assert imported == 10_000
        assert set(type_counts) == {"single_choice", "multiple_choice", "judge"}
        for persisted in persisted_success:
            assert persisted is not None
            assert persisted.status == "succeeded"
            assert persisted.total_rows == 5000
            assert persisted.validated_rows == 5000
            assert persisted.created_count == 5000
            assert persisted.error_count == 0
            assert persisted.finished_at is not None
            assert persisted.expires_at - persisted.finished_at == timedelta(days=7)

    invalid_jobs: list[QuizImportJob] = []
    for source_type in ("json", "csv"):
        name = f"import-validation-errors.{source_type}"
        job = await env.service.create_import_job(
            source_type=source_type,
            content=artifacts[name],
            admin_id=env.admin_id,
            filename=name,
        )
        assert await env.service.process_import_job(job.id) is True
        invalid_jobs.append(job)

    expected_error_rows = ({1, 2, 4}, {2, 3, 4, 6})
    async with env.factory() as db:
        imported_after_failures = await db.scalar(
            select(func.count())
            .select_from(QuizQuestion)
            .where(
                QuizQuestion.category_id == categories["import_leaf"].id,
                QuizQuestion.question_text.like("QF55-V1-%"),
            )
        )
        assert imported_after_failures == 10_000
        for job, expected_rows in zip(
            invalid_jobs, expected_error_rows, strict=True
        ):
            persisted = await db.get(QuizImportJob, job.id)
            assert persisted is not None
            assert persisted.status == "validation_failed"
            assert persisted.created_count == 0
            assert persisted.error_count == len(expected_rows)
            assert persisted.report_object_key is not None
            report = json.loads(
                (
                    await env.service._get_import_object(
                        persisted.report_object_key
                    )
                ).decode("utf-8")
            )
            assert {item["row"] for item in report["errors"]} == expected_rows


async def test_import_job_claim_prevents_two_workers_from_processing_same_batch(
    quiz_env,
    monkeypatch,
) -> None:
    env = quiz_env
    category = await _category(env, "claim")
    content = json.dumps(
        {
            "questions": [
                {
                    "category_path": [category.name],
                    "question_type": "single_choice",
                    "question_text": f"{env.prefix}_claim_question",
                    "options": {"A": "一", "B": "二", "C": "三"},
                    "correct_answer": "A",
                    "explanation": "认领测试解析",
                }
            ]
        },
        ensure_ascii=False,
    ).encode("utf-8")
    job = await env.service.create_import_job(
        source_type="json",
        content=content,
        admin_id=env.admin_id,
        filename="claim.json",
    )

    claimed = asyncio.Event()
    release = asyncio.Event()
    original_get = env.service._get_import_object

    async def delayed_get(object_key: str) -> bytes:
        claimed.set()
        await release.wait()
        return await original_get(object_key)

    monkeypatch.setattr(env.service, "_get_import_object", delayed_get)
    first_worker = asyncio.create_task(env.service.process_import_job(job.id))
    await asyncio.wait_for(claimed.wait(), timeout=5)

    # The first worker has committed status=validating and a fresh heartbeat.
    # A second worker must not read the source or create the same draft batch.
    assert await env.service.process_import_job(job.id) is False
    release.set()
    assert await asyncio.wait_for(first_worker, timeout=10) is True

    async with env.factory() as db:
        persisted = await db.get(QuizImportJob, job.id)
        created_count = await db.scalar(
            select(func.count()).select_from(QuizQuestion).where(
                QuizQuestion.category_id == category.id,
                QuizQuestion.question_text == f"{env.prefix}_claim_question",
            )
        )
        assert persisted.status == "succeeded"
        assert persisted.created_count == 1
        assert created_count == 1


async def test_import_missing_categories_waits_for_confirmation_and_commits_atomically(
    quiz_env,
) -> None:
    env = quiz_env
    root = await _category(env, "confirm_root")
    missing_child = f"{env.prefix}_confirm_child"
    missing_leaf = f"{env.prefix}_confirm_leaf"
    questions = [
        {
            "category_path": [root.name, missing_child, missing_leaf],
            "question_type": "judge",
            "question_text": f"{env.prefix}_confirm_question_{index}",
            "options": {"A": "正确", "B": "错误"},
            "correct_answer": "A",
            "explanation": None,
        }
        for index in range(2)
    ]
    job = await env.service.create_import_job(
        source_type="json",
        content=json.dumps({"questions": questions}, ensure_ascii=False).encode(),
        admin_id=env.admin_id,
        filename="confirm.json",
    )

    assert await env.service.process_import_job(job.id) is True
    impact = await env.service.get_import_category_impact(
        job.id,
        admin_id=env.admin_id,
    )
    assert impact.new_category_count == 2
    assert impact.reused_category_count == 1
    assert impact.affected_question_count == 2
    assert impact.tree[0].status == "existing"
    assert impact.tree[0].children[0].status == "will_create"

    async with env.factory() as db:
        persisted = await db.get(QuizImportJob, job.id)
        assert persisted.status == "awaiting_category_confirmation"
        assert persisted.created_count == 0
        assert persisted.error_count == 0
        assert await db.scalar(
            select(func.count()).select_from(QuizCategory).where(
                QuizCategory.normalized_name.in_([missing_child, missing_leaf])
            )
        ) == 0

    queued = await env.service.confirm_import_categories(
        job.id,
        AdminQuizImportConfirmCategoriesRequest(
            lock_version=impact.lock_version,
            impact_version=impact.impact_version,
        ),
        admin_id=env.admin_id,
    )
    assert queued.status == "queued"
    assert queued.execution_protected_until - queued.confirmed_at == timedelta(
        minutes=30
    )
    assert await env.service.process_import_job(job.id) is True

    async with env.factory() as db:
        persisted = await db.get(QuizImportJob, job.id)
        created_categories = list(
            (
                await db.execute(
                    select(QuizCategory).where(
                        QuizCategory.normalized_name.in_([missing_child, missing_leaf])
                    )
                )
            ).scalars()
        )
        created_questions = await db.scalar(
            select(func.count()).select_from(QuizQuestion).where(
                QuizQuestion.question_text.like(f"{env.prefix}_confirm_question_%")
            )
        )
        assert persisted.status == "succeeded"
        assert persisted.created_count == 2
        assert len(created_categories) == 2
        assert created_questions == 2


async def test_confirmed_import_requires_reconfirmation_when_categories_appear(
    quiz_env,
) -> None:
    env = quiz_env
    root = await _category(env, "reconfirm_root")
    missing_child = f"{env.prefix}_reconfirm_child"
    question_text = f"{env.prefix}_reconfirm_question"
    content = json.dumps(
        {
            "questions": [
                {
                    "category_path": [root.name, missing_child],
                    "question_type": "judge",
                    "question_text": question_text,
                    "options": {"A": "正确", "B": "错误"},
                    "correct_answer": "A",
                    "explanation": None,
                }
            ]
        },
        ensure_ascii=False,
    ).encode()
    job = await env.service.create_import_job(
        source_type="json",
        content=content,
        admin_id=env.admin_id,
        filename="reconfirm.json",
    )
    assert await env.service.process_import_job(job.id) is True
    first_impact = await env.service.get_import_category_impact(
        job.id,
        admin_id=env.admin_id,
    )
    await env.service.confirm_import_categories(
        job.id,
        AdminQuizImportConfirmCategoriesRequest(
            lock_version=first_impact.lock_version,
            impact_version=first_impact.impact_version,
        ),
        admin_id=env.admin_id,
    )

    # Simulate another administrator creating the formerly missing category
    # after the impact preview was confirmed but before the Worker executes.
    child = await env.service.create_category(
        AdminQuizCategoryCreate(name=missing_child, parent_id=root.id),
        admin_id=env.admin_id,
    )
    assert await env.service.process_import_job(job.id) is True

    refreshed = await env.service.get_import_category_impact(
        job.id,
        admin_id=env.admin_id,
    )
    assert refreshed.impact_version != first_impact.impact_version
    assert refreshed.lock_version == first_impact.lock_version + 2
    assert refreshed.new_category_count == 0
    assert refreshed.reused_category_count == 2
    async with env.factory() as db:
        persisted = await db.get(QuizImportJob, job.id)
        assert persisted.status == "awaiting_category_confirmation"
        assert persisted.confirmed_at is None
        assert persisted.execution_protected_until is None
        assert await db.scalar(
            select(func.count()).select_from(QuizQuestion).where(
                QuizQuestion.question_text == question_text
            )
        ) == 0

    await env.service.confirm_import_categories(
        job.id,
        AdminQuizImportConfirmCategoriesRequest(
            lock_version=refreshed.lock_version,
            impact_version=refreshed.impact_version,
        ),
        admin_id=env.admin_id,
    )
    assert await env.service.process_import_job(job.id) is True
    async with env.factory() as db:
        persisted = await db.get(QuizImportJob, job.id)
        created = await db.scalar(
            select(func.count()).select_from(QuizQuestion).where(
                QuizQuestion.category_id == child.id,
                QuizQuestion.question_text == question_text,
            )
        )
        assert persisted.status == "succeeded"
        assert persisted.created_count == 1
        assert created == 1


async def test_import_error_page_is_redacted_fixed_size_and_filterable(quiz_env) -> None:
    env = quiz_env
    category = await _category(env, "errors")
    questions = [
        {
            "category_path": [category.name],
            "question_type": "judge",
            "question_text": f"{env.prefix}_error_{index}",
            "options": {"A": "错误", "B": "错误"},
            "correct_answer": "A",
            "explanation": None,
        }
        for index in range(55)
    ]
    job = await env.service.create_import_job(
        source_type="json",
        content=json.dumps({"questions": questions}, ensure_ascii=False).encode(),
        admin_id=env.admin_id,
        filename="errors.json",
    )
    assert await env.service.process_import_job(job.id) is True

    first = await env.service.list_import_errors(
        job.id,
        AdminQuizImportErrorQuery(page=1),
        admin_id=env.admin_id,
    )
    second = await env.service.list_import_errors(
        job.id,
        AdminQuizImportErrorQuery(field="options", page=2),
        admin_id=env.admin_id,
    )
    assert first.total == 55
    assert first.page_size == 50
    assert len(first.items) == 50
    assert first.available_fields == ["options"]
    assert len(second.items) == 5
    serialized = first.model_dump_json()
    assert "question_text" not in serialized
    assert "correct_answer" not in serialized
    assert f"{env.prefix}_error_" not in serialized
    assert [item.row for item in first.items[:2]] == [1, 2]
    assert [item.question_index for item in first.items[:2]] == [1, 2]


async def test_import_confirmation_cancel_is_optimistically_locked(quiz_env) -> None:
    env = quiz_env
    missing = f"{env.prefix}_cancel_missing"
    content = json.dumps(
        {
            "questions": [
                {
                    "category_path": [missing],
                    "question_type": "judge",
                    "question_text": f"{env.prefix}_cancel_question",
                    "options": {"A": "正确", "B": "错误"},
                    "correct_answer": "A",
                    "explanation": None,
                }
            ]
        },
        ensure_ascii=False,
    ).encode()
    job = await env.service.create_import_job(
        source_type="json",
        content=content,
        admin_id=env.admin_id,
        filename="cancel.json",
    )
    assert await env.service.process_import_job(job.id) is True
    impact = await env.service.get_import_category_impact(
        job.id, admin_id=env.admin_id
    )
    cancelled = await env.service.cancel_import_job(
        job.id,
        AdminQuizImportCancelRequest(lock_version=impact.lock_version),
        admin_id=env.admin_id,
    )
    assert cancelled.status == "cancelled"
    with pytest.raises(ConflictException):
        await env.service.confirm_import_categories(
            job.id,
            AdminQuizImportConfirmCategoriesRequest(
                lock_version=impact.lock_version,
                impact_version=impact.impact_version,
            ),
            admin_id=env.admin_id,
        )


async def test_import_expiry_cleanup_download_audit_and_local_signing(quiz_env) -> None:
    env = quiz_env
    now = datetime.now(timezone.utc)

    async def create_job(suffix: str, expires_at: datetime) -> QuizImportJob:
        source_key = env.service._import_object_key(f"{env.prefix}_{suffix}", "json")
        report_key = env.service._import_object_key(
            f"{env.prefix}_{suffix}", "report.json"
        )
        await env.service._put_import_object(source_key, b"{}", "application/json")
        await env.service._put_import_object(
            report_key,
            json.dumps(
                {
                    "job_id": 1,
                    "errors": [{"row": 1, "message": "校验错误"}],
                }
            ).encode(),
            "application/json",
        )
        async with env.factory() as db:
            job = QuizImportJob(
                admin_id=env.admin_id,
                import_batch_key=f"{env.prefix}_{suffix}",
                source_type="json",
                status="validation_failed",
                source_object_key=source_key,
                source_size_bytes=2,
                report_object_key=report_key,
                total_rows=1,
                validated_rows=0,
                created_count=0,
                error_count=1,
                expires_at=expires_at,
            )
            db.add(job)
            await db.commit()
            await db.refresh(job)
            return job

    expired = await create_job("expired", now - timedelta(days=1))
    active = await create_job("active", now + timedelta(days=1))
    running_source_key = env.service._import_object_key(
        f"{env.prefix}_running", "json"
    )
    await env.service._put_import_object(
        running_source_key,
        b"{}",
        "application/json",
    )
    async with env.factory() as db:
        running = QuizImportJob(
            admin_id=env.admin_id,
            import_batch_key=f"{env.prefix}_running",
            source_type="json",
            status="validating",
            source_object_key=running_source_key,
            source_size_bytes=2,
            total_rows=0,
            validated_rows=0,
            created_count=0,
            error_count=0,
            expires_at=now - timedelta(days=1),
            heartbeat_at=now,
        )
        db.add(running)
        await db.commit()
        await db.refresh(running)

    with pytest.raises(BusinessException, match="已过期"):
        await env.service.get_import_report_url(
            expired.id,
            admin_id=env.admin_id,
        )

    signed = await env.service.get_import_report_url(
        active.id,
        admin_id=env.admin_id,
    )
    assert signed.url.startswith(f"/admin/quiz/imports/{active.id}/report?")
    query = dict(
        part.split("=", 1) for part in signed.url.split("?", 1)[1].split("&")
    )
    payload = await env.service.read_import_report(
        active.id,
        expires=int(query["expires"]),
        admin_id=int(query["admin_id"]),
        token=query["token"],
    )
    assert [error.model_dump(exclude_none=True) for error in payload.errors] == [
        {"row": 1, "message": "校验错误"}
    ]

    assert await env.service.cleanup_expired_import_job(
        now=now,
        job_id=expired.id,
    ) is True
    assert await env.service.cleanup_expired_import_job(
        now=now,
        job_id=expired.id,
    ) is False
    assert await env.service.cleanup_expired_import_job(
        now=now,
        job_id=running.id,
    ) is False
    assert not env.service._local_import_path(expired.source_object_key).exists()
    assert not env.service._local_import_path(expired.report_object_key).exists()
    assert env.service._local_import_path(running.source_object_key).exists()

    async with env.factory() as db:
        queued_source_key = env.service._import_object_key(
            f"{env.prefix}_queued_expired", "json"
        )
        await env.service._put_import_object(
            queued_source_key,
            b"{}",
            "application/json",
        )
        queued = QuizImportJob(
            admin_id=env.admin_id,
            import_batch_key=f"{env.prefix}_queued_expired",
            source_type="json",
            status="queued",
            source_object_key=queued_source_key,
            source_size_bytes=2,
            expires_at=now - timedelta(days=1),
            heartbeat_at=now - timedelta(days=1),
        )
        db.add(queued)
        await db.commit()
        await db.refresh(queued)

    assert await env.service.cleanup_expired_import_job(
        now=now,
        job_id=queued.id,
    ) is False
    assert env.service._local_import_path(queued.source_object_key).exists()

    async with env.factory() as db:
        logs = list(
            (
                await db.execute(
                    select(QuizAdminAuditLog).where(
                        QuizAdminAuditLog.object_type == "import_job",
                        QuizAdminAuditLog.object_id.in_([expired.id, active.id]),
                    )
                )
            ).scalars()
        )
        actions = {(log.action, log.result, log.actor_type) for log in logs}
        assert ("import.report_download_url", "failed", "admin") in actions
        assert ("import.report_download_url", "succeeded", "admin") in actions
        assert ("import.report_download", "succeeded", "admin") in actions
        assert ("import.cleanup", "succeeded", "system") in actions


async def test_import_source_signing_and_manual_retry_state_machine(quiz_env) -> None:
    env = quiz_env
    source = json.dumps({"questions": []}).encode()
    job = await env.service.create_import_job(
        source_type="json",
        content=source,
        admin_id=env.admin_id,
        filename="source.json",
    )
    signed = await env.service.get_import_source_url(job.id, admin_id=env.admin_id)
    assert signed.url.startswith(f"/admin/quiz/imports/{job.id}/source?")
    query = dict(
        part.split("=", 1) for part in signed.url.split("?", 1)[1].split("&")
    )
    downloaded = await env.service.read_import_source(
        job.id,
        expires=int(query["expires"]),
        admin_id=int(query["admin_id"]),
        token=query["token"],
    )
    assert downloaded.data == source
    assert downloaded.media_type == "application/json; charset=utf-8"
    assert downloaded.extension == "json"

    with pytest.raises(BusinessException, match="仅 failed"):
        await env.service.retry_import_job(job.id, admin_id=env.admin_id)

    async with env.factory() as db:
        persisted = await db.get(QuizImportJob, job.id)
        persisted.status = "failed"
        persisted.error_message = "safe failure"
        persisted.retry_count = 1
        persisted.finished_at = datetime.now(timezone.utc)
        await db.commit()
        batch_key = persisted.import_batch_key

    retried = await env.service.retry_import_job(job.id, admin_id=env.admin_id)
    assert retried.id == job.id
    assert retried.import_batch_key == batch_key
    assert retried.status == "queued"
    assert retried.retry_count == 1

    async with env.factory() as db:
        actions = set(
            (
                await db.execute(
                    select(QuizAdminAuditLog.action).where(
                        QuizAdminAuditLog.object_id == job.id
                    )
                )
            ).scalars()
        )
        assert "import.source_download_url" in actions
        assert "import.source_download" in actions
        assert "import.manual_retry" in actions


async def test_expired_failed_import_cannot_be_retried(quiz_env) -> None:
    env = quiz_env
    source = json.dumps({"questions": []}).encode()
    job = await env.service.create_import_job(
        source_type="json",
        content=source,
        admin_id=env.admin_id,
        filename="expired-source.json",
    )
    async with env.factory() as db:
        persisted = await db.get(QuizImportJob, job.id)
        persisted.status = "failed"
        persisted.error_message = "safe failure"
        persisted.finished_at = datetime.now(timezone.utc) - timedelta(days=8)
        persisted.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        await db.commit()

    with pytest.raises(BusinessException, match="已过期"):
        await env.service.retry_import_job(job.id, admin_id=env.admin_id)

    async with env.factory() as db:
        persisted = await db.get(QuizImportJob, job.id)
        failure = (
            await db.execute(
                select(QuizAdminAuditLog).where(
                    QuizAdminAuditLog.object_id == job.id,
                    QuizAdminAuditLog.action == "import.manual_retry",
                    QuizAdminAuditLog.result == "failed",
                )
            )
        ).scalar_one()
        assert persisted.status == "failed"
        assert "已过期" in failure.error_summary


async def test_question_stats_aggregate_first_attempts_and_settled_exams(quiz_env) -> None:
    env = quiz_env
    category = await _category(env, "stats")
    question = await _question(env, category.id, "stats_question")
    question = await env.service.publish_question(
        question.id,
        AdminQuizVersionRequest(lock_version=question.lock_version),
        admin_id=env.admin_id,
    )
    event_at = datetime.now(timezone.utc) - timedelta(seconds=5)

    async with env.factory() as db:
        user = User(openid=f"{env.prefix}_stats_user")
        db.add(user)
        await db.flush()

        practice = QuizPracticeSession(
            user_id=user.id,
            mode="normal",
            category_id=category.id,
            requested_count=10,
            actual_count=1,
            status="completed",
            started_at=event_at,
            completed_at=event_at + timedelta(seconds=2),
            lock_version=1,
        )
        db.add(practice)
        await db.flush()
        practice_question = QuizPracticeSessionQuestion(
            session_id=practice.id,
            question_id=question.id,
            position=1,
            category_id=category.id,
            category_path=[{"id": category.id, "name": category.name}],
            question_type=question.question_type,
            question_text=question.question_text,
            options=question.options,
            correct_answer=question.correct_answer,
            explanation=question.explanation,
            question_lock_version=question.lock_version,
        )
        db.add(practice_question)
        await db.flush()
        db.add_all(
            [
                QuizPracticeAttempt(
                    user_id=user.id,
                    session_id=practice.id,
                    session_question_id=practice_question.id,
                    idempotency_key=f"{env.prefix}_first",
                    attempt_no=1,
                    is_first_attempt=True,
                    user_answer="B",
                    is_correct=False,
                    submitted_at=event_at + timedelta(seconds=1),
                ),
                QuizPracticeAttempt(
                    user_id=user.id,
                    session_id=practice.id,
                    session_question_id=practice_question.id,
                    idempotency_key=f"{env.prefix}_second",
                    attempt_no=2,
                    is_first_attempt=False,
                    user_answer="A",
                    is_correct=True,
                    submitted_at=event_at + timedelta(seconds=2),
                ),
            ]
        )

        async def add_exam(status: str, is_correct: bool, offset: int) -> None:
            started_at = event_at - timedelta(hours=2) + timedelta(seconds=offset)
            lifecycle = {
                "completed": {"submitted_at": started_at + timedelta(seconds=3)},
                "timed_out": {"timed_out_at": started_at + timedelta(hours=1)},
                "abandoned": {"abandoned_at": started_at + timedelta(seconds=3)},
            }[status]
            settled = status in {"completed", "timed_out"}
            exam = QuizExam(
                user_id=user.id,
                category_id=category.id,
                question_count=10,
                duration_seconds=3600,
                status=status,
                started_at=started_at,
                deadline_at=started_at + timedelta(hours=1),
                correct_count=(1 if is_correct else 0) if settled else None,
                wrong_count=(0 if is_correct else 1) if settled else None,
                unanswered_count=9 if settled else None,
                score=Decimal("10.0") if settled and is_correct else (Decimal("0.0") if settled else None),
                lock_version=1,
                **lifecycle,
            )
            db.add(exam)
            await db.flush()
            exam_question = QuizExamQuestion(
                exam_id=exam.id,
                question_id=question.id,
                position=1,
                category_id=category.id,
                category_path=[{"id": category.id, "name": category.name}],
                question_type=question.question_type,
                question_text=question.question_text,
                options=question.options,
                correct_answer=question.correct_answer,
                explanation=question.explanation,
                question_lock_version=question.lock_version,
            )
            db.add(exam_question)
            await db.flush()
            db.add(
                QuizExamAnswer(
                    exam_id=exam.id,
                    exam_question_id=exam_question.id,
                    user_answer="A" if is_correct else "B",
                    is_correct=is_correct,
                    saved_at=started_at + timedelta(seconds=2),
                    lock_version=1,
                )
            )

        await add_exam("completed", True, 10)
        await add_exam("timed_out", False, 20)
        await add_exam("abandoned", False, 30)
        await db.commit()

    cutoff = datetime.now(timezone.utc)
    assert await env.service.aggregate_question_stats(
        now=cutoff,
        question_ids=[question.id],
    ) is True
    assert await env.service.aggregate_question_stats(
        now=cutoff + timedelta(seconds=1),
        question_ids=[question.id],
    ) is True

    stats = await env.service.get_question_stats(question.id)
    assert stats.practice_first_attempts == 1
    assert stats.practice_first_correct == 0
    assert stats.practice_first_accuracy == Decimal("0.0")
    assert stats.exam_answers == 2
    assert stats.exam_correct == 1
    assert stats.exam_accuracy == Decimal("50.0")
    assert stats.aggregated_through is not None
    assert 0 <= (stats.aggregated_through - event_at).total_seconds() <= 60

    overview = await env.service.get_stats_overview()
    assert overview.practice_first_attempts >= 1
    assert overview.exam_answers >= 2
    assert overview.calculated_at is not None
    assert overview.aggregated_through is not None

    async with env.factory() as db:
        rows = list(
            (
                await db.execute(
                    select(QuizQuestionStats).where(
                        QuizQuestionStats.question_id == question.id
                    )
                )
            ).scalars()
        )
        assert len(rows) == 1
