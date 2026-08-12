"""Frozen-contract PostgreSQL integration tests for admin quiz workflows."""

from __future__ import annotations

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
    AdminQuizCategoryStatusUpdate,
    AdminQuizCategoryUpdate,
    AdminQuizQuestionCreate,
    AdminQuizQuestionUpdate,
    AdminQuizVersionRequest,
)
from app.services.admin_quiz import AdminQuizService


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


async def test_five_thousand_row_import_validation_rolls_back_batch(quiz_env) -> None:
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
        validation_audit = (
            await db.execute(
                select(QuizAdminAuditLog).where(
                    QuizAdminAuditLog.action == "import.validation_failed",
                    QuizAdminAuditLog.object_id == job.id,
                )
            )
        ).scalar_one()
        assert persisted.status == "validation_failed"
        assert persisted.total_rows == 5000
        assert persisted.created_count == 0
        assert persisted.error_count == 1
        assert persisted.report_object_key is not None
        assert persisted.finished_at is not None
        assert persisted.expires_at - persisted.finished_at == timedelta(days=7)
        assert created_count == 0
        assert validation_audit.result == "failed"
        assert validation_audit.changed_fields["error_count"]["after"] == 1

    report = json.loads(
        (await env.service._get_import_object(persisted.report_object_key)).decode()
    )
    assert report["errors"][0]["row"] == 5000
    assert report["errors"][0]["field"] == "category_path"


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
            json.dumps({"errors": [{"row": 1}]}).encode(),
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
    assert payload["errors"] == [{"row": 1}]

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
