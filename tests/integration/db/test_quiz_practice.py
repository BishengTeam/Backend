"""PostgreSQL and HTTP coverage for QB-13 through QB-22."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.adapter.security import create_access_token
from app.domain.community.src.index import (
    QuizCategory,
    QuizCheckin,
    QuizCollection,
    QuizPracticeSessionQuestion,
    QuizQuestion,
    QuizUserStats,
    QuizWrongItem,
)
from app.domain.community.src.rule.quiz import (
    normalize_question_text,
    question_text_digest,
)
from app.domain.user.src.index import AdminUser, User
from app.port.exceptions import ConflictException
from app.schemas.quiz_contract import (
    QuizCheckinCalendarQuery,
    QuizCollectionCreate,
    QuizPracticeAttemptCreate,
    QuizPracticeHistoryQuery,
    QuizPracticeSessionCreate,
    QuizQuestionListQuery,
    QuizStatsQuery,
    QuizWrongBookQuery,
)
from app.services.quiz_practice import QuizPracticeService


pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


def _database_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    assert url.startswith("postgresql+asyncpg://")
    return url


@pytest.fixture
async def quiz_practice_env(monkeypatch):
    engine = create_async_engine(_database_url(), pool_size=5, max_overflow=5)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"qp_{uuid4().hex[:12]}"

    @asynccontextmanager
    async def test_db_ctx():
        async with factory() as session:
            yield session

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
        service=QuizPracticeService(),
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
    sort_order: int = 0,
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
            sort_order=sort_order,
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
    status: str = "published",
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
            question = QuizQuestion(
                category_id=category.id,
                question_type="single_choice",
                status=status,
                question_text=question_text,
                normalized_question_text=normalized,
                question_text_hash=question_text_digest(normalized),
                options={"A": "Correct", "B": "Wrong", "C": "Other"},
                correct_answer="A",
                explanation=f"Explanation {index}",
                image_urls=[f"https://cdn.example.com/{suffix}-{index}.png"] if index == 0 else [],
                ever_published=status != "draft",
                published_at=now if status != "draft" else None,
                disabled_at=now if status == "disabled" else None,
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


def _normal_session(category_id: int, count: int = 10) -> QuizPracticeSessionCreate:
    return QuizPracticeSessionCreate(
        mode="normal",
        category_id=category_id,
        question_count=count,
    )


def _attempt(
    session_question_id: int,
    key: str,
    answer: str = "A",
) -> QuizPracticeAttemptCreate:
    return QuizPracticeAttemptCreate(
        session_question_id=session_question_id,
        idempotency_key=key,
        user_answer=answer,
    )


async def test_public_tree_and_logged_in_question_list_use_effective_subtree(
    quiz_practice_env,
) -> None:
    env = quiz_practice_env
    root = await _create_category(env, "root", sort_order=2)
    child = await _create_category(env, "child", parent=root, sort_order=2)
    await _create_category(env, "empty", parent=root, sort_order=1)
    disabled = await _create_category(env, "disabled", parent=root, status="disabled")
    await _create_questions(env, root, 1, suffix="root")
    await _create_questions(env, child, 2, suffix="child")
    await _create_questions(env, disabled, 2, suffix="disabled")
    await _create_questions(env, child, 1, suffix="draft", status="draft")

    tree = await env.service.list_categories()
    root_node = next(node for node in tree if node.id == root.id)
    assert root_node.question_count == 3
    assert [node.id for node in root_node.children] == [child.id]
    assert root_node.children[0].question_count == 2

    result = await env.service.list_questions(
        env.user.id,
        QuizQuestionListQuery(category_id=root.id, page=1, page_size=100),
    )
    assert result.total == 3
    assert {item.category_id for item in result.items} == {root.id, child.id}
    for item in result.items:
        payload = item.model_dump()
        assert "image_urls" in payload
        assert "correct_answer" not in payload
        assert "explanation" not in payload


async def test_unique_session_unanswered_first_selection_and_fixed_order(
    quiz_practice_env,
) -> None:
    env = quiz_practice_env
    category = await _create_category(env, "selection")
    questions = await _create_questions(env, category, 12, suffix="selection")

    first = await env.service.create_session(
        env.user.id,
        _normal_session(category.id),
    )
    repeated = await env.service.create_session(
        env.user.id,
        QuizPracticeSessionCreate(mode="wrong"),
    )
    assert repeated.id == first.id
    assert [item.id for item in repeated.questions] == [
        item.id for item in first.questions
    ]
    assert [item.position for item in first.questions] == list(range(1, 11))
    assert len({item.id for item in first.questions}) == 10

    for item in first.questions:
        await env.service.submit_attempt(
            env.user.id,
            first.id,
            _attempt(item.session_question_id, f"first-{item.position:03d}"),
        )
    completed = await env.service.get_session(env.user.id, first.id)
    assert completed.status == "completed"

    second = await env.service.create_session(
        env.user.id,
        _normal_session(category.id),
    )
    all_ids = {question.id for question in questions}
    first_ids = {item.id for item in first.questions}
    unseen_ids = all_ids - first_ids
    second_ids = {item.id for item in second.questions}
    assert len(unseen_ids) == 2
    assert unseen_ids <= second_ids
    assert len(second_ids) == 10


async def test_snapshot_idempotency_reanswers_history_stats_and_abandonment(
    quiz_practice_env,
) -> None:
    env = quiz_practice_env
    category = await _create_category(env, "snapshot")
    await _create_questions(env, category, 2, suffix="snapshot")
    session = await env.service.create_session(
        env.user.id,
        _normal_session(category.id),
    )
    target = session.questions[0]
    original_text = target.question_text
    original_explanation = f"Explanation {0 if original_text.endswith('_0') else 1}"

    async with env.factory() as db:
        question = await db.get(QuizQuestion, target.id)
        assert question is not None
        changed_text = f"{question.question_text}_changed"
        normalized = normalize_question_text(changed_text)
        question.question_text = changed_text
        question.normalized_question_text = normalized
        question.question_text_hash = question_text_digest(normalized)
        question.correct_answer = "B"
        question.explanation = "Changed explanation"
        question.status = "disabled"
        question.disabled_at = datetime.now(timezone.utc)
        question.lock_version += 1
        await db.commit()

    first = await env.service.submit_attempt(
        env.user.id,
        session.id,
        _attempt(target.session_question_id, "snapshot-0001", "A"),
    )
    retry = await env.service.submit_attempt(
        env.user.id,
        session.id,
        _attempt(target.session_question_id, "snapshot-0001", "A"),
    )
    assert retry.attempt_id == first.attempt_id
    assert first.is_correct is True
    assert first.correct_answer == "A"
    assert first.explanation == original_explanation

    with pytest.raises(ConflictException, match="不同答案"):
        await env.service.submit_attempt(
            env.user.id,
            session.id,
            _attempt(target.session_question_id, "snapshot-0001", "B"),
        )

    second = await env.service.submit_attempt(
        env.user.id,
        session.id,
        _attempt(target.session_question_id, "snapshot-0002", "B"),
    )
    assert second.attempt_id != first.attempt_id
    assert second.attempt_no == 2
    assert second.is_correct is False

    current = await env.service.get_session(env.user.id, session.id)
    current_target = next(item for item in current.questions if item.id == target.id)
    assert current_target.question_text == original_text
    assert current_target.attempt_count == 2

    history = await env.service.get_history(
        env.user.id,
        QuizPracticeHistoryQuery(category_id=category.id, page=1, page_size=20),
    )
    assert history.total == 2
    assert {item.question_text for item in history.items} == {original_text}
    assert {item.attempt_no for item in history.items} == {1, 2}
    assert {item.current_question_status for item in history.items} == {"disabled"}

    local_day = QuizPracticeService._local_date(first.submitted_at)
    filtered = await env.service.get_history(
        env.user.id,
        QuizPracticeHistoryQuery(
            category_id=category.id,
            question_type="single_choice",
            is_correct=True,
            date_from=local_day,
            date_to=local_day,
            page=1,
            page_size=20,
        ),
    )
    assert filtered.total == 1
    assert filtered.items[0].attempt_no == 1

    stats = await env.service.get_stats(env.user.id)
    assert stats.practice.total_attempts == 2
    assert stats.practice.first_attempts == 1
    assert stats.practice.first_correct_attempts == 1
    # The retry flipped the only question to wrong under the latest-attempt rule.
    assert stats.practice.latest_accuracy == Decimal("0.0")
    assert stats.practice.answered_questions == 1
    assert stats.practice.today_questions == 2

    scoped_stats = await env.service.get_stats(
        env.user.id,
        QuizStatsQuery(scope_type="library", scope_id=category.id),
    )
    assert scoped_stats.practice.total_attempts == 0
    assert scoped_stats.practice.first_attempts == 0
    assert scoped_stats.practice.answered_questions == 0
    assert scoped_stats.practice.accuracy == 0
    assert scoped_stats.practice.latest_accuracy == Decimal("0.0")

    abandoned = await env.service.abandon_session(env.user.id, session.id)
    assert abandoned.status == "abandoned"
    assert await env.service.get_current_session(env.user.id) is None
    retained = await env.service.get_history(
        env.user.id,
        QuizPracticeHistoryQuery(page=1, page_size=20),
    )
    assert retained.total == 2

    with pytest.raises(ConflictException):
        await env.service.submit_attempt(
            env.user.id,
            session.id,
            _attempt(target.session_question_id, "snapshot-0003", "A"),
        )


async def test_wrong_book_counts_and_three_correct_streak(
    quiz_practice_env,
) -> None:
    env = quiz_practice_env
    category = await _create_category(env, "wrong-streak")
    await _create_questions(env, category, 2, suffix="wrong-streak")
    normal = await env.service.create_session(
        env.user.id,
        _normal_session(category.id),
    )
    target = normal.questions[0]

    await env.service.submit_attempt(
        env.user.id,
        normal.id,
        _attempt(target.session_question_id, "wrong-0001", "B"),
    )
    await env.service.submit_attempt(
        env.user.id,
        normal.id,
        _attempt(target.session_question_id, "wrong-0002", "A"),
    )
    wrong_book = await env.service.list_wrong_book(
        env.user.id,
        QuizWrongBookQuery(page=1, page_size=20),
    )
    assert wrong_book.total == 1
    assert wrong_book.items[0].question_id == target.id
    assert wrong_book.items[0].wrong_count == 1
    await env.service.abandon_session(env.user.id, normal.id)

    exam_snapshot: QuizPracticeSessionQuestion | None = None
    for index in (1, 2):
        wrong_session = await env.service.create_session(
            env.user.id,
            QuizPracticeSessionCreate(mode="wrong"),
        )
        assert wrong_session.actual_count == 1
        assert wrong_session.questions[0].id == target.id
        exam_snapshot = wrong_session.questions[0]
        await env.service.submit_attempt(
            env.user.id,
            wrong_session.id,
            _attempt(
                wrong_session.questions[0].session_question_id,
                f"correct-{index:04d}",
                "A",
            ),
        )
        await env.service.abandon_session(env.user.id, wrong_session.id)
        still_active = await env.service.list_wrong_book(
            env.user.id,
            QuizWrongBookQuery(page=1, page_size=20),
        )
        assert still_active.total == 1
        async with env.factory() as db:
            item = (
                await db.execute(
                    select(QuizWrongItem).where(
                        QuizWrongItem.user_id == env.user.id,
                        QuizWrongItem.question_id == target.id,
                    )
                )
            ).scalar_one()
            assert item.status == "active"
            assert item.wrong_count == 1
            assert item.consecutive_correct_count == index

    assert exam_snapshot is not None
    async with env.factory() as db:
        stats_row = (
            await db.execute(
                select(QuizUserStats)
                .where(QuizUserStats.user_id == env.user.id)
                .with_for_update()
            )
        ).scalar_one()
        await env.service.apply_settled_exam_wrong_book(
            db,
            user_id=env.user.id,
            snapshot=exam_snapshot,
            is_correct=False,
            settled_at=datetime.now(timezone.utc),
            stats=stats_row,
        )
        await db.commit()

    async with env.factory() as db:
        item = (
            await db.execute(
                select(QuizWrongItem).where(
                    QuizWrongItem.user_id == env.user.id,
                    QuizWrongItem.question_id == target.id,
                )
            )
        ).scalar_one()
        assert item.status == "active"
        assert item.wrong_count == 2
        assert item.consecutive_correct_count == 2

    wrong_again_session = await env.service.create_session(
        env.user.id,
        QuizPracticeSessionCreate(mode="wrong"),
    )
    await env.service.submit_attempt(
        env.user.id,
        wrong_again_session.id,
        _attempt(
            wrong_again_session.questions[0].session_question_id,
            "wrong-cycle-2",
            "B",
        ),
    )
    await env.service.abandon_session(env.user.id, wrong_again_session.id)
    async with env.factory() as db:
        item = (
            await db.execute(
                select(QuizWrongItem).where(
                    QuizWrongItem.user_id == env.user.id,
                    QuizWrongItem.question_id == target.id,
                )
            )
        ).scalar_one()
        assert item.wrong_count == 3
        assert item.consecutive_correct_count == 0

    for index in range(3):
        wrong_session = await env.service.create_session(
            env.user.id,
            QuizPracticeSessionCreate(mode="wrong"),
        )
        assert wrong_session.questions[0].id == target.id
        await env.service.submit_attempt(
            env.user.id,
            wrong_session.id,
            _attempt(
                wrong_session.questions[0].session_question_id,
                f"mastery-{index:02d}",
                "A",
            ),
        )
        await env.service.abandon_session(env.user.id, wrong_session.id)

    cleared_book = await env.service.list_wrong_book(
        env.user.id,
        QuizWrongBookQuery(page=1, page_size=20),
    )
    assert cleared_book.total == 0
    async with env.factory() as db:
        item = (
            await db.execute(
                select(QuizWrongItem).where(
                    QuizWrongItem.user_id == env.user.id,
                    QuizWrongItem.question_id == target.id,
                )
            )
        ).scalar_one()
        assert item.status == "cleared"
        assert item.cleared_at is not None
        assert item.wrong_count == 0
        assert item.consecutive_correct_count == 0

    stats = await env.service.get_stats(env.user.id)
    assert stats.practice.total_attempts == 9
    assert stats.practice.first_attempts == 9
    assert stats.practice.first_correct_attempts == 5
    assert stats.practice.answered_questions == 1
    assert stats.practice.active_wrong_count == 0


async def test_wrong_practice_uses_latest_twenty_in_fixed_order(
    quiz_practice_env,
) -> None:
    env = quiz_practice_env
    category = await _create_category(env, "wrong-order")
    questions = await _create_questions(env, category, 21, suffix="wrong-order")
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    async with env.factory() as db:
        for index, question in enumerate(questions):
            occurred_at = base + timedelta(minutes=index)
            db.add(
                QuizWrongItem(
                    user_id=env.user.id,
                    question_id=question.id,
                    status="active",
                    first_wrong_at=occurred_at,
                    latest_wrong_at=occurred_at,
                    latest_wrong_snapshot={
                        "question_id": question.id,
                        "category_id": category.id,
                        "category_path": [{"id": category.id, "name": category.name}],
                        "question_type": question.question_type,
                        "question_text": question.question_text,
                        "options": question.options,
                    },
                )
            )
        await db.commit()

    session = await env.service.create_session(
        env.user.id,
        QuizPracticeSessionCreate(mode="wrong"),
    )
    expected = [question.id for question in reversed(questions[1:])]
    assert session.actual_count == 20
    assert [item.id for item in session.questions] == expected


async def test_collection_is_idempotent_and_disabled_question_is_retained(
    quiz_practice_env,
) -> None:
    env = quiz_practice_env
    category = await _create_category(env, "collection")
    question = (await _create_questions(env, category, 1, suffix="collection"))[0]

    first = await env.service.add_collection(
        env.user.id,
        QuizCollectionCreate(question_id=question.id),
    )
    second = await env.service.add_collection(
        env.user.id,
        QuizCollectionCreate(question_id=question.id),
    )
    assert first.is_active is True
    assert second.is_active is True

    await env.service.remove_collection(env.user.id, question.id)
    readded = await env.service.add_collection(
        env.user.id,
        QuizCollectionCreate(question_id=question.id),
    )
    assert readded.is_active is True
    async with env.factory() as db:
        readded_row = (
            await db.execute(
                select(QuizCollection).where(
                    QuizCollection.user_id == env.user.id,
                    QuizCollection.question_id == question.id,
                )
            )
        ).scalar_one()
        assert readded_row.removed_at is not None

    async with env.factory() as db:
        persisted = await db.get(QuizQuestion, question.id)
        assert persisted is not None
        persisted.status = "disabled"
        persisted.disabled_at = datetime.now(timezone.utc)
        await db.commit()

    listed = await env.service.list_collections(
        env.user.id,
        QuizWrongBookQuery(page=1, page_size=20),
    )
    assert listed.total == 1
    assert listed.items[0].question_status == "disabled"
    payload = listed.items[0].question.model_dump()
    assert "correct_answer" not in payload
    assert "explanation" not in payload

    await env.service.remove_collection(env.user.id, question.id)
    await env.service.remove_collection(env.user.id, question.id)
    stats = await env.service.get_stats(env.user.id)
    assert stats.practice.active_collection_count == 0
    async with env.factory() as db:
        item = (
            await db.execute(
                select(QuizCollection).where(
                    QuizCollection.user_id == env.user.id,
                    QuizCollection.question_id == question.id,
                )
            )
        ).scalar_one()
        assert item.is_active is False
        assert item.removed_at is not None


async def test_shanghai_checkin_boundary_consecutive_days_and_immediate_stats(
    quiz_practice_env,
    monkeypatch,
) -> None:
    env = quiz_practice_env
    category = await _create_category(env, "checkin")
    await _create_questions(env, category, 1, suffix="checkin")
    clock = {"now": datetime(2026, 8, 6, 15, 59, tzinfo=timezone.utc)}
    monkeypatch.setattr(
        QuizPracticeService,
        "_now",
        staticmethod(lambda: clock["now"]),
    )

    first = await env.service.create_session(env.user.id, _normal_session(category.id))
    await env.service.submit_attempt(
        env.user.id,
        first.id,
        _attempt(first.questions[0].session_question_id, "checkin-0001"),
    )

    clock["now"] = datetime(2026, 8, 6, 16, 1, tzinfo=timezone.utc)
    second = await env.service.create_session(env.user.id, _normal_session(category.id))
    await env.service.submit_attempt(
        env.user.id,
        second.id,
        _attempt(second.questions[0].session_question_id, "checkin-0002"),
    )

    status = await env.service.get_checkin_status(env.user.id)
    assert status.checkin_date == date(2026, 8, 7)
    assert status.checked_in is True
    assert status.questions_completed == 1
    assert status.consecutive_days == 2

    calendar = await env.service.get_checkin_calendar(
        env.user.id,
        QuizCheckinCalendarQuery(
            date_from=date(2026, 8, 6),
            date_to=date(2026, 8, 7),
        ),
    )
    assert [item.checkin_date for item in calendar] == [
        date(2026, 8, 6),
        date(2026, 8, 7),
    ]
    assert [item.consecutive_days for item in calendar] == [1, 2]

    stats = await env.service.get_stats(env.user.id)
    assert stats.practice.total_attempts == 2
    assert stats.practice.first_attempts == 2
    assert stats.practice.first_correct_attempts == 2
    assert stats.practice.answered_questions == 1
    assert stats.practice.checkin_days == 2
    assert stats.practice.consecutive_days == 2
    assert stats.practice.today_questions == 1

    async with env.factory() as db:
        checkins = (
            await db.execute(
                select(QuizCheckin)
                .where(QuizCheckin.user_id == env.user.id)
                .order_by(QuizCheckin.checkin_date)
            )
        ).scalars().all()
        assert len(checkins) == 2


async def test_http_auth_validation_ownership_and_answer_visibility(
    quiz_practice_env,
    monkeypatch,
) -> None:
    env = quiz_practice_env
    category = await _create_category(env, "http")
    await _create_questions(env, category, 1, suffix="http")

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
            public = await client.get("/api/quiz/categories")
            assert public.status_code == 200, public.text

            no_current = await client.get(
                "/api/quiz/practice-sessions/current",
                headers=user_headers,
            )
            assert no_current.status_code == 200, no_current.text
            assert no_current.json()["data"] is None

            unauthenticated = await client.get("/api/quiz/questions")
            assert unauthenticated.status_code == 401
            assert unauthenticated.json()["code"] == 40100

            questions = await client.get(
                f"/api/quiz/questions?category_id={category.id}",
                headers=user_headers,
            )
            assert questions.status_code == 200, questions.text
            question_payload = questions.json()["data"]["items"][0]
            assert "correct_answer" not in question_payload
            assert "explanation" not in question_payload

            invalid = await client.post(
                "/api/quiz/practice-sessions",
                headers=user_headers,
                json={
                    "mode": "normal",
                    "category_id": category.id,
                    "question_count": 9,
                },
            )
            assert invalid.status_code == 422
            assert invalid.json()["code"] == 40001

            created = await client.post(
                "/api/quiz/practice-sessions",
                headers=user_headers,
                json={
                    "mode": "normal",
                    "category_id": category.id,
                    "question_count": 10,
                },
            )
            assert created.status_code == 200, created.text
            session = created.json()["data"]
            pending_question = session["questions"][0]
            assert pending_question["latest_result"] is None
            assert "correct_answer" not in pending_question
            assert "explanation" not in pending_question

            current = await client.get(
                "/api/quiz/practice-sessions/current",
                headers=user_headers,
            )
            assert current.status_code == 200, current.text
            assert current.json()["data"]["id"] == session["id"]

            hidden = await client.get(
                f"/api/quiz/practice-sessions/{session['id']}",
                headers=other_headers,
            )
            assert hidden.status_code == 404
            assert hidden.json()["code"] == 40300

            submitted = await client.post(
                f"/api/quiz/practice-sessions/{session['id']}/attempts",
                headers=user_headers,
                json={
                    "session_question_id": pending_question["session_question_id"],
                    "idempotency_key": "http-attempt-0001",
                    "user_answer": "B",
                },
            )
            assert submitted.status_code == 200, submitted.text
            result = submitted.json()["data"]
            assert result["is_correct"] is False
            assert result["correct_answer"] == "A"
            assert result["explanation"]

            completed_current = await client.get(
                "/api/quiz/practice-sessions/current",
                headers=user_headers,
            )
            assert completed_current.status_code == 200, completed_current.text
            assert completed_current.json()["data"] is None

            history = await client.get(
                "/api/quiz/practice-history",
                headers=user_headers,
                params={
                    "category_id": category.id,
                    "question_type": "single_choice",
                    "is_correct": "false",
                    "page": 1,
                    "page_size": 20,
                },
            )
            assert history.status_code == 200, history.text
            history_items = history.json()["data"]["items"]
            assert len(history_items) == 1
            assert history_items[0]["attempt_id"] == result["attempt_id"]
            assert history_items[0]["correct_answer"] == "A"
            assert history_items[0]["explanation"]

            wrong_book = await client.get(
                "/api/quiz/wrong-book",
                headers=user_headers,
            )
            assert wrong_book.status_code == 200, wrong_book.text
            wrong_question = wrong_book.json()["data"]["items"][0]["question"]
            assert "correct_answer" not in wrong_question
            assert "explanation" not in wrong_question

            added_collection = await client.post(
                "/api/quiz/collections",
                headers=user_headers,
                json={"question_id": question_payload["id"]},
            )
            assert added_collection.status_code == 200, added_collection.text
            assert added_collection.json()["data"]["is_active"] is True

            collections = await client.get(
                "/api/quiz/collections",
                headers=user_headers,
            )
            assert collections.status_code == 200, collections.text
            collection_items = collections.json()["data"]["items"]
            assert len(collection_items) == 1
            collection_question = collection_items[0]["question"]
            assert "correct_answer" not in collection_question
            assert "explanation" not in collection_question

            removed_collection = await client.delete(
                f"/api/quiz/collections/{question_payload['id']}",
                headers=user_headers,
            )
            assert removed_collection.status_code == 200, removed_collection.text
            assert removed_collection.json()["data"]["is_active"] is False

            checkin = await client.get("/api/quiz/checkin", headers=user_headers)
            assert checkin.status_code == 200, checkin.text
            checkin_data = checkin.json()["data"]
            assert checkin_data["checked_in"] is True
            assert checkin_data["questions_completed"] == 1

            calendar = await client.get(
                "/api/quiz/checkin/calendar",
                headers=user_headers,
                params={
                    "date_from": checkin_data["checkin_date"],
                    "date_to": checkin_data["checkin_date"],
                },
            )
            assert calendar.status_code == 200, calendar.text
            assert [item["checkin_date"] for item in calendar.json()["data"]] == [
                checkin_data["checkin_date"]
            ]

            stats = await client.get("/api/quiz/stats", headers=user_headers)
            assert stats.status_code == 200, stats.text
            stats_data = stats.json()["data"]
            assert stats_data["practice"]["total_attempts"] == 1
            assert stats_data["practice"]["active_wrong_count"] == 1
            assert stats_data["practice"]["checkin_days"] == 1

            second = await client.post(
                "/api/quiz/practice-sessions",
                headers=user_headers,
                json={
                    "mode": "normal",
                    "category_id": category.id,
                    "question_count": 10,
                },
            )
            assert second.status_code == 200, second.text
            second_session = second.json()["data"]
            abandoned = await client.post(
                f"/api/quiz/practice-sessions/{second_session['id']}/abandon",
                headers=user_headers,
            )
            assert abandoned.status_code == 200, abandoned.text
            assert abandoned.json()["data"]["status"] == "abandoned"

            abandoned_detail = await client.get(
                f"/api/quiz/practice-sessions/{second_session['id']}",
                headers=user_headers,
            )
            assert abandoned_detail.status_code == 200, abandoned_detail.text
            assert abandoned_detail.json()["data"]["status"] == "abandoned"
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
