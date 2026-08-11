"""PostgreSQL and HTTP coverage for the simulated-exam chain (QB-23–QB-29)."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.adapter.security import create_access_token
from app.domain.community.src.index import (
    QuizCategory,
    QuizExam,
    QuizExamAnswer,
    QuizQuestion,
    QuizUserStats,
    QuizWrongItem,
)
from app.domain.community.src.rule.quiz import (
    normalize_question_text,
    question_text_digest,
)
from app.domain.user.src.index import AdminUser, User
from app.port.exceptions import BusinessException, ConflictException, NotFoundException
from app.schemas.quiz_contract import (
    QuizExamAnswerSave,
    QuizExamCreate,
    QuizExamListQuery,
)
from app.services.quiz_exam import QuizExamService
from app.services.quiz_practice import QuizPracticeService


pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


def _database_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    assert url.startswith("postgresql+asyncpg://")
    return url


@pytest.fixture
async def quiz_exam_env(monkeypatch):
    engine = create_async_engine(_database_url(), pool_size=8, max_overflow=8)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"qe_{uuid4().hex[:12]}"

    @asynccontextmanager
    async def test_db_ctx():
        async with factory() as session:
            yield session

    monkeypatch.setattr("app.services.quiz_exam.get_db_ctx", test_db_ctx)
    monkeypatch.setattr("app.services.quiz_practice.get_db_ctx", test_db_ctx)

    async with factory() as db:
        admin = AdminUser(
            username=f"{prefix}_admin",
            password_hash="integration-test-only",
            role="super_admin",
        )
        user = User(openid=f"{prefix}_user")
        other_user = User(openid=f"{prefix}_other")
        db.add_all([admin, user, other_user])
        await db.commit()
        await db.refresh(admin)
        await db.refresh(user)
        await db.refresh(other_user)

    env = SimpleNamespace(
        service=QuizExamService(),
        practice=QuizPracticeService(),
        factory=factory,
        prefix=prefix,
        admin=admin,
        user=user,
        other_user=other_user,
    )
    try:
        yield env
    finally:
        async with factory() as db:
            # User deletion first cascades exams, snapshots, answers, wrong
            # items and personal stats so question RESTRICT references vanish.
            await db.execute(
                text('DELETE FROM "user" WHERE id IN (:user_id, :other_user_id)'),
                {"user_id": user.id, "other_user_id": other_user.id},
            )
            await db.execute(
                text(
                    "DELETE FROM quiz_question_stats WHERE question_id IN ("
                    "SELECT id FROM quiz_question WHERE created_by = :admin_id)"
                ),
                {"admin_id": admin.id},
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
                text("DELETE FROM admin_user WHERE id = :admin_id"),
                {"admin_id": admin.id},
            )
            await db.commit()
        await engine.dispose()


async def _create_category(
    env,
    suffix: str,
    *,
    parent: QuizCategory | None = None,
    status: str = "active",
) -> QuizCategory:
    async with env.factory() as db:
        name = f"{env.prefix}_{suffix}"
        category = QuizCategory(
            name=name,
            normalized_name=name,
            parent_id=parent.id if parent is not None else None,
            depth=(parent.depth + 1) if parent is not None else 1,
            description=None,
            status=status,
            sort_order=0,
            ever_had_question=False,
            lock_version=1,
            created_by=env.admin.id,
            updated_by=env.admin.id,
        )
        db.add(category)
        await db.commit()
        await db.refresh(category)
        return category


async def _create_questions(
    env,
    category: QuizCategory,
    count: int,
    *,
    suffix: str,
) -> list[QuizQuestion]:
    now = datetime.now(timezone.utc)
    async with env.factory() as db:
        persisted_category = await db.get(QuizCategory, category.id)
        assert persisted_category is not None
        persisted_category.ever_had_question = True
        questions: list[QuizQuestion] = []
        for index in range(count):
            question_text = f"{env.prefix}_{suffix}_{index}"
            normalized = normalize_question_text(question_text)
            is_multiple = index == 0
            question = QuizQuestion(
                category_id=category.id,
                question_type=("multiple_choice" if is_multiple else "single_choice"),
                status="published",
                question_text=question_text,
                normalized_question_text=normalized,
                question_text_hash=question_text_digest(normalized),
                options={"A": "One", "B": "Two", "C": "Three", "D": "Four"},
                correct_answer=(["A", "C"] if is_multiple else "A"),
                explanation=f"Explanation {index}",
                ever_published=True,
                published_at=now,
                disabled_at=None,
                lock_version=1,
                created_by=env.admin.id,
                updated_by=env.admin.id,
            )
            db.add(question)
            questions.append(question)
        await db.commit()
        for question in questions:
            await db.refresh(question)
        return questions


def _create_request(category_id: int, count: int = 10) -> QuizExamCreate:
    return QuizExamCreate(category_id=category_id, question_count=count)


async def _expire_exam(env, exam_id: int, now: datetime) -> None:
    async with env.factory() as db:
        exam = await db.get(QuizExam, exam_id)
        assert exam is not None
        exam.started_at = now - timedelta(seconds=3601)
        exam.deadline_at = now - timedelta(seconds=1)
        await db.commit()


async def test_exam_subtree_selection_unique_active_snapshot_and_visibility(
    quiz_exam_env,
) -> None:
    env = quiz_exam_env
    root = await _create_category(env, "root")
    child = await _create_category(env, "child", parent=root)
    disabled = await _create_category(env, "disabled", parent=root, status="disabled")
    await _create_questions(env, root, 4, suffix="root")
    await _create_questions(env, child, 8, suffix="child")
    await _create_questions(env, disabled, 5, suffix="disabled")

    created = await env.service.create_exam(env.user.id, _create_request(root.id))
    repeated = await env.service.create_exam(
        env.user.id,
        _create_request(child.id, count=11),
    )
    assert repeated.id == created.id
    assert created.status == "in_progress"
    assert created.duration_seconds == 3600
    assert created.deadline_at - created.started_at == timedelta(seconds=3600)
    assert [item.position for item in created.questions] == list(range(1, 11))
    assert len({item.id for item in created.questions}) == 10
    assert {item.category_id for item in created.questions} <= {root.id, child.id}
    for item in created.questions:
        payload = item.model_dump()
        assert list(payload["options"]) == ["A", "B", "C", "D"]
        assert "correct_answer" not in payload
        assert "explanation" not in payload

    current = await env.service.get_current_exam(env.user.id)
    assert current is not None and current.id == created.id
    history = await env.service.list_exams(
        env.user.id,
        QuizExamListQuery(page=1, page_size=20),
    )
    assert history.total == 1

    with pytest.raises(NotFoundException):
        await env.service.get_exam(env.other_user.id, created.id)


async def test_exam_rejects_disabled_or_insufficient_pool(quiz_exam_env) -> None:
    env = quiz_exam_env
    small = await _create_category(env, "small")
    disabled = await _create_category(env, "disabled-root", status="disabled")
    await _create_questions(env, small, 9, suffix="small")
    await _create_questions(env, disabled, 10, suffix="disabled-root")

    with pytest.raises(BusinessException, match="最大可选数为 9"):
        await env.service.create_exam(env.user.id, _create_request(small.id))
    with pytest.raises(BusinessException, match="停用"):
        await env.service.create_exam(env.user.id, _create_request(disabled.id))


async def test_answer_optimistic_lock_snapshot_settlement_wrong_book_and_stats(
    quiz_exam_env,
) -> None:
    env = quiz_exam_env
    category = await _create_category(env, "settle")
    await _create_questions(env, category, 10, suffix="settle")
    exam = await env.service.create_exam(env.user.id, _create_request(category.id))
    multiple = next(
        item for item in exam.questions if item.question_type == "multiple_choice"
    )
    single = next(item for item in exam.questions if item.question_type == "single_choice")

    first = await env.service.save_answer(
        env.user.id,
        exam.id,
        multiple.exam_question_id,
        QuizExamAnswerSave(user_answer=["C", "A", "A"], lock_version=0),
    )
    assert first.user_answer == ["A", "C"]
    assert first.lock_version == 1
    with pytest.raises(ConflictException, match="版本冲突"):
        await env.service.save_answer(
            env.user.id,
            exam.id,
            multiple.exam_question_id,
            QuizExamAnswerSave(user_answer=["A"], lock_version=0),
        )
    replaced = await env.service.save_answer(
        env.user.id,
        exam.id,
        multiple.exam_question_id,
        QuizExamAnswerSave(user_answer=["A", "C"], lock_version=1),
    )
    assert replaced.lock_version == 2
    await env.service.save_answer(
        env.user.id,
        exam.id,
        single.exam_question_id,
        QuizExamAnswerSave(user_answer="B", lock_version=0),
    )

    original_text = multiple.question_text
    async with env.factory() as db:
        question = await db.get(QuizQuestion, multiple.id)
        assert question is not None
        changed_text = f"{question.question_text}_changed"
        normalized = normalize_question_text(changed_text)
        question.question_text = changed_text
        question.normalized_question_text = normalized
        question.question_text_hash = question_text_digest(normalized)
        question.correct_answer = ["B", "D"]
        question.explanation = "Changed explanation"
        question.status = "disabled"
        question.disabled_at = datetime.now(timezone.utc)
        question.lock_version += 1
        await db.commit()

    action = await env.service.submit_exam(env.user.id, exam.id)
    duplicate = await env.service.submit_exam(env.user.id, exam.id)
    assert action.status == "completed"
    assert duplicate.status == "completed"
    assert duplicate.score == action.score == 10

    detail = await env.service.get_exam(env.user.id, exam.id)
    assert detail.status == "completed"
    assert detail.correct_count == 1
    assert detail.wrong_count == 1
    assert detail.unanswered_count == 8
    assert detail.score == 10
    multiple_result = next(item for item in detail.questions if item.id == multiple.id)
    assert multiple_result.question_text == original_text
    assert multiple_result.correct_answer == ["A", "C"]
    assert multiple_result.explanation != "Changed explanation"

    stats = await env.practice.get_stats(env.user.id)
    assert stats.practice.total_attempts == 0
    assert stats.exam.completed_exam_count == 1
    assert stats.exam.timed_out_exam_count == 0
    assert stats.exam.total_questions == 10
    assert stats.exam.correct_count == 1
    assert stats.exam.wrong_count == 1
    assert stats.exam.unanswered_count == 8
    assert stats.exam.average_score == 10
    assert stats.exam.highest_score == 10
    assert stats.exam.latest_score == 10

    async with env.factory() as db:
        wrong = (
            await db.execute(
                select(QuizWrongItem).where(QuizWrongItem.user_id == env.user.id)
            )
        ).scalars().all()
        assert [item.question_id for item in wrong] == [single.id]


async def test_abandon_retains_answer_without_disclosure_or_stats(quiz_exam_env) -> None:
    env = quiz_exam_env
    category = await _create_category(env, "abandon")
    await _create_questions(env, category, 10, suffix="abandon")
    exam = await env.service.create_exam(env.user.id, _create_request(category.id))
    target = exam.questions[0]
    await env.service.save_answer(
        env.user.id,
        exam.id,
        target.exam_question_id,
        QuizExamAnswerSave(
            user_answer=(
                ["A", "C"] if target.question_type == "multiple_choice" else "A"
            ),
            lock_version=0,
        ),
    )

    action = await env.service.abandon_exam(env.user.id, exam.id)
    duplicate = await env.service.abandon_exam(env.user.id, exam.id)
    assert action.status == duplicate.status == "abandoned"
    detail = await env.service.get_exam(env.user.id, exam.id)
    payload = detail.model_dump()
    assert payload["status"] == "abandoned"
    assert "score" not in payload
    for question in payload["questions"]:
        assert "user_answer" not in question
        assert "correct_answer" not in question
        assert "explanation" not in question

    stats = await env.practice.get_stats(env.user.id)
    assert stats.exam.completed_exam_count == 0
    assert stats.exam.timed_out_exam_count == 0
    assert stats.exam.total_questions == 0
    async with env.factory() as db:
        answers = (
            await db.execute(
                select(QuizExamAnswer).where(QuizExamAnswer.exam_id == exam.id)
            )
        ).scalars().all()
        assert len(answers) == 1
        assert answers[0].is_correct is None

    replacement = await env.service.create_exam(env.user.id, _create_request(category.id))
    assert replacement.id != exam.id


async def test_timeout_worker_is_multi_worker_safe_and_lazy_save_settles_first(
    quiz_exam_env,
) -> None:
    env = quiz_exam_env
    category = await _create_category(env, "timeout")
    await _create_questions(env, category, 10, suffix="timeout")
    first = await env.service.create_exam(env.user.id, _create_request(category.id))
    second = await env.service.create_exam(
        env.other_user.id,
        _create_request(category.id),
    )
    now = datetime.now(timezone.utc)
    await _expire_exam(env, first.id, now)
    await _expire_exam(env, second.id, now)

    result_a, result_b = await asyncio.gather(
        QuizExamService().settle_expired_exams(now=now),
        QuizExamService().settle_expired_exams(now=now),
    )
    assert result_a + result_b == 2
    first_detail = await env.service.get_exam(env.user.id, first.id)
    second_detail = await env.service.get_exam(env.other_user.id, second.id)
    assert first_detail.status == second_detail.status == "timed_out"
    assert first_detail.unanswered_count == second_detail.unanswered_count == 10

    # A new expired exam exercises the request-path fallback: the attempted
    # write returns 409 only after the database has durably transitioned it.
    third = await env.service.create_exam(env.user.id, _create_request(category.id))
    await _expire_exam(env, third.id, now)
    with pytest.raises(ConflictException, match="已自动结算"):
        await env.service.save_answer(
            env.user.id,
            third.id,
            third.questions[0].exam_question_id,
            QuizExamAnswerSave(
                user_answer=(
                    ["A", "C"]
                    if third.questions[0].question_type == "multiple_choice"
                    else "A"
                ),
                lock_version=0,
            ),
        )
    async with env.factory() as db:
        persisted = await db.get(QuizExam, third.id)
        assert persisted is not None
        assert persisted.status == "timed_out"
        stats = (
            await db.execute(
                select(QuizUserStats).where(QuizUserStats.user_id == env.user.id)
            )
        ).scalar_one()
        assert stats.timed_out_exam_count == 2

    fourth = await env.service.create_exam(
        env.other_user.id,
        _create_request(category.id),
    )
    await _expire_exam(env, fourth.id, now)
    lazy_detail = await env.service.get_exam(env.other_user.id, fourth.id)
    assert lazy_detail.status == "timed_out"
    assert lazy_detail.unanswered_count == 10


async def test_exam_http_auth_validation_visibility_conflict_and_ownership(
    quiz_exam_env,
    monkeypatch,
) -> None:
    env = quiz_exam_env
    category = await _create_category(env, "http")
    await _create_questions(env, category, 10, suffix="http")
    small_category = await _create_category(env, "http-small")
    await _create_questions(env, small_category, 9, suffix="http-small")

    async def override_get_db():
        async with env.factory() as session:
            yield session

    async def token_is_not_revoked(_token: str) -> bool:
        return False

    from app.adapter.database import get_db
    from app.main import app

    previous_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("app.middleware.auth.is_token_revoked", token_is_not_revoked)
    user_headers = {
        "Authorization": f"Bearer {create_access_token(env.user.id, env.user.openid)}"
    }
    other_headers = {
        "Authorization": (
            f"Bearer {create_access_token(env.other_user.id, env.other_user.openid)}"
        )
    }
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            unauthenticated = await client.post(
                "/api/quiz/exams",
                json={"category_id": category.id, "question_count": 10},
            )
            assert unauthenticated.status_code == 401
            assert unauthenticated.json()["code"] == 40100

            invalid = await client.post(
                "/api/quiz/exams",
                headers=user_headers,
                json={"category_id": category.id, "question_count": 9},
            )
            assert invalid.status_code == 422
            assert invalid.json()["code"] == 40001

            insufficient = await client.post(
                "/api/quiz/exams",
                headers=user_headers,
                json={"category_id": small_category.id, "question_count": 10},
            )
            assert insufficient.status_code == 422
            assert insufficient.json()["code"] == 40200
            assert "最大可选数为 9" in insufficient.json()["message"]

            created = await client.post(
                "/api/quiz/exams",
                headers=user_headers,
                json={"category_id": category.id, "question_count": 10},
            )
            assert created.status_code == 200, created.text
            exam = created.json()["data"]
            assert exam["status"] == "in_progress"
            assert all(
                "correct_answer" not in question and "explanation" not in question
                for question in exam["questions"]
            )

            hidden = await client.get(
                f"/api/quiz/exams/{exam['id']}", headers=other_headers
            )
            assert hidden.status_code == 404
            assert hidden.json()["code"] == 40300

            target = exam["questions"][0]
            answer = ["A", "C"] if target["question_type"] == "multiple_choice" else "A"
            saved = await client.put(
                f"/api/quiz/exams/{exam['id']}/answers/{target['exam_question_id']}",
                headers=user_headers,
                json={"user_answer": answer, "lock_version": 0},
            )
            assert saved.status_code == 200, saved.text
            stale = await client.put(
                f"/api/quiz/exams/{exam['id']}/answers/{target['exam_question_id']}",
                headers=user_headers,
                json={"user_answer": answer, "lock_version": 0},
            )
            assert stale.status_code == 409
            assert stale.json()["code"] == 40201

            submitted = await client.post(
                f"/api/quiz/exams/{exam['id']}/submit",
                headers=user_headers,
            )
            assert submitted.status_code == 200, submitted.text
            settled = await client.get(
                f"/api/quiz/exams/{exam['id']}", headers=user_headers
            )
            assert settled.status_code == 200, settled.text
            settled_questions = settled.json()["data"]["questions"]
            assert all(
                "correct_answer" in question and "explanation" in question
                for question in settled_questions
            )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
