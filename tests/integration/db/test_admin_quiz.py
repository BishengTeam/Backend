"""PostgreSQL integration tests for Admin Quiz module.

Covers batch-delete and JSON import endpoints.
"""

import os
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


def _require_database_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    assert url.startswith("postgresql+asyncpg://"), (
        f"TEST_DATABASE_URL must use asyncpg driver, got: {url[:30]}..."
    )
    return url


@pytest.fixture
async def session_factory():
    """Create a session factory bound to the test database."""
    url = _require_database_url()
    engine = create_async_engine(url, pool_size=5, max_overflow=10)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
async def test_prefix(session_factory):
    """Generate unique prefix and clean up matching quiz data before + after."""
    prefix = f"aq_{uuid4().hex[:12]}"
    await _cleanup_quiz_data(session_factory, prefix)
    try:
        yield prefix
    finally:
        await _cleanup_quiz_data(session_factory, prefix)


@pytest.fixture
async def patched_service(monkeypatch, session_factory):
    """Return AdminQuizService with get_db_ctx patched to use the test database."""

    @asynccontextmanager
    async def _test_db_ctx():
        async with session_factory() as session:
            yield session

    monkeypatch.setattr("app.services.admin_quiz.get_db_ctx", _test_db_ctx)

    from app.services.admin_quiz import AdminQuizService
    return AdminQuizService()


async def _cleanup_quiz_data(session_factory, prefix: str) -> None:
    """Delete quiz categories and questions matching the prefix."""
    pattern = f"{prefix}%"
    async with session_factory() as db:
        await db.execute(
            text(
                "DELETE FROM quiz_question "
                "WHERE category_id IN ("
                "  SELECT id FROM quiz_category WHERE name LIKE :p"
                ")"
            ),
            {"p": pattern},
        )
        await db.execute(
            text("DELETE FROM quiz_category WHERE name LIKE :p"),
            {"p": pattern},
        )
        await db.commit()


async def _create_category(session_factory, name: str) -> int:
    """Insert a quiz category and return its id."""
    async with session_factory() as db:
        from app.models.quiz import QuizCategory
        cat = QuizCategory(name=name)
        db.add(cat)
        await db.commit()
        await db.refresh(cat)
        return cat.id


async def _create_question(session_factory, category_id: int, *, text: str, qtype: str = "single_choice",
                           answer: str = "A", options: dict | None = None) -> int:
    """Insert a quiz question and return its id."""
    async with session_factory() as db:
        from app.models.quiz import QuizQuestion
        q = QuizQuestion(
            category_id=category_id,
            question_type=qtype,
            question_text=text,
            options=options or {"A": "选项A", "B": "选项B", "C": "选项C", "D": "选项D"},
            correct_answer=answer,
        )
        db.add(q)
        await db.commit()
        await db.refresh(q)
        return q.id


# ──────────────────────────────────────────────
# 1. POST /admin/quiz/questions/batch-delete
# ──────────────────────────────────────────────


async def test_batch_delete_questions(session_factory, test_prefix, patched_service):
    """Batch-delete deletes questions with no quiz records and returns the count."""
    # Create category + 3 questions
    cat_id = await _create_category(session_factory, test_prefix)
    qid1 = await _create_question(session_factory, cat_id, text=f"{test_prefix}-q1")
    qid2 = await _create_question(session_factory, cat_id, text=f"{test_prefix}-q2")
    qid3 = await _create_question(session_factory, cat_id, text=f"{test_prefix}-q3")

    # Batch-delete all 3
    deleted = await patched_service.batch_delete_questions([qid1, qid2, qid3])
    assert deleted == 3, f"Expected 3 deleted, got {deleted}"

    # Verify questions are gone
    async with session_factory() as db:
        from app.models.quiz import QuizQuestion
        result = await db.execute(
            select(QuizQuestion).where(QuizQuestion.id.in_([qid1, qid2, qid3]))
        )
        remaining = result.scalars().all()
        assert len(remaining) == 0, f"Expected 0 questions remaining, got {len(remaining)}"


# ──────────────────────────────────────────────
# 2. POST /admin/quiz/import/json
# ──────────────────────────────────────────────


async def test_import_questions_json(session_factory, test_prefix, patched_service):
    """JSON import creates questions and returns created/skipped/errors counts."""
    # Create category
    cat_id = await _create_category(session_factory, test_prefix)

    from app.schemas.admin_quiz import AdminQuizImportJsonRequest, AdminQuizQuestionItem

    request = AdminQuizImportJsonRequest(
        category_id=cat_id,
        questions=[
            AdminQuizQuestionItem(
                question_type="single_choice",
                question_text=f"{test_prefix}-import-1",
                options={"A": "正确", "B": "错误"},
                correct_answer="A",
            ),
            AdminQuizQuestionItem(
                question_type="judge",
                question_text=f"{test_prefix}-import-2",
                options={"A": "对", "B": "错"},
                correct_answer="A",
            ),
        ],
    )

    result = await patched_service.import_questions_json(request)

    assert "created" in result, f"Missing 'created' key in {result}"
    assert "skipped" in result, f"Missing 'skipped' key in {result}"
    assert "errors" in result, f"Missing 'errors' key in {result}"
    assert result["created"] >= 1, f"Expected at least 1 created, got {result}"
    assert result["skipped"] == 0, f"Expected 0 skipped, got {result}"
    assert len(result["errors"]) == 0, f"Expected 0 errors, got {result['errors']}"

    # Verify questions exist in DB
    async with session_factory() as db:
        from app.models.quiz import QuizQuestion
        result_set = await db.execute(
            select(QuizQuestion).where(QuizQuestion.category_id == cat_id)
        )
        questions = result_set.scalars().all()
        assert len(questions) == 2, f"Expected 2 questions in DB, got {len(questions)}"
