"""Pure contract and grading tests for QB-23 through QB-29.

Database behavior is exercised by ``tests/integration/db/test_quiz_exam.py``
when the explicit PostgreSQL test URLs are supplied.  These tests deliberately
stay database-free so the answer visibility and scoring invariants run in the
ordinary unit-test gate as well.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute
from pydantic import ValidationError

from app.api.quiz import router as quiz_router
from app.port.exceptions import ValidationException
from app.schemas.quiz_contract import (
    QuizExamAbandonedDetail,
    QuizExamAnswerSave,
    QuizExamCreate,
    QuizExamInProgressDetail,
    QuizExamQuestionResult,
    QuizExamScopeSelection,
)
from app.services.quiz_exam import QuizExamService


def _snapshot(
    *,
    question_id: int = 1,
    question_type: str = "single_choice",
    correct_answer: str | list[str] = "A",
) -> SimpleNamespace:
    options = (
        {"A": "正确", "B": "错误"}
        if question_type == "judge"
        else {"A": "一", "B": "二", "C": "三", "D": "四"}
    )
    return SimpleNamespace(
        id=question_id + 1000,
        question_id=question_id,
        category_id=9,
        category_path=[{"id": 9, "name": "分类"}],
        question_type=question_type,
        question_text=f"题目 {question_id}",
        options=options,
        correct_answer=correct_answer,
        explanation="解析",
        image_urls=["https://cdn.example.com/exam-stem.png"],
        question_lock_version=2,
    )


def test_exam_request_is_strictly_bounded_and_duration_is_frozen() -> None:
    assert QuizExamCreate(category_id=1, question_count=10).question_count == 10
    assert QuizExamCreate(category_id=1, question_count=100).question_count == 100
    with pytest.raises(ValidationError):
        QuizExamCreate(category_id=1, question_count=9)
    with pytest.raises(ValidationError):
        QuizExamCreate(category_id=1, question_count=101)
    v2 = QuizExamCreate(
        scope_type="knowledge_point", scope_id=3, question_count=10
    )
    assert v2.category_id is None
    assert str(v2.scope_type) == "knowledge_point"
    with pytest.raises(ValidationError):
        QuizExamCreate(category_id=1, scope_type="library", scope_id=2, question_count=10)
    with pytest.raises(ValidationError):
        QuizExamCreate(scope_type="library", question_count=10)
    multi = QuizExamCreate(
        scopes=[
            QuizExamScopeSelection(scope_type="module", scope_id=7),
            QuizExamScopeSelection(scope_type="knowledge_point", scope_id=9),
        ],
        question_count=15,
    )
    assert multi.category_id is None
    assert multi.scope_type is None
    assert multi.scopes is not None and len(multi.scopes) == 2
    with pytest.raises(ValidationError, match="duplicates"):
        QuizExamCreate(
            scopes=[
                QuizExamScopeSelection(scope_type="module", scope_id=7),
                QuizExamScopeSelection(scope_type="module", scope_id=7),
            ],
            question_count=10,
        )
    with pytest.raises(ValidationError, match="exactly one"):
        QuizExamCreate(
            scope_type="module",
            scope_id=7,
            scopes=[QuizExamScopeSelection(scope_type="module", scope_id=8)],
            question_count=10,
        )

    # The response models make the fixed duration and state-specific fields
    # explicit, preventing accidental leakage through a generic dict response.
    assert "correct_answer" not in QuizExamInProgressDetail.model_fields


def test_exam_answer_shape_is_canonical_but_lock_version_stays_client_owned() -> None:
    first = QuizExamAnswerSave(user_answer=["C", "A", "A"], lock_version=0)
    assert first.user_answer == ["A", "C"]
    assert first.lock_version == 0
    with pytest.raises(ValidationError):
        QuizExamAnswerSave(user_answer={"A": True}, lock_version=0)


def test_grading_is_exact_for_all_question_types_and_score_rounds_half_up() -> None:
    service = QuizExamService()
    single = _snapshot(correct_answer="A")
    assert service._grade_answer(single, "a") == ("A", True)
    assert service._grade_answer(single, "B")[1] is False

    multiple = _snapshot(
        question_type="multiple_choice", correct_answer=["A", "C"]
    )
    assert service._grade_answer(multiple, ["C", "A", "A"]) == (["A", "C"], True)
    assert service._grade_answer(multiple, ["A"])[1] is False
    assert service._grade_answer(multiple, ["A", "B"])[1] is False

    judge = _snapshot(question_type="judge", correct_answer="A")
    assert service._grade_answer(judge, "A")[1] is True
    assert service._grade_answer(judge, "B")[1] is False

    assert service._score(1, 3) == Decimal("33.3")
    assert service._score(2, 3) == Decimal("66.7")
    assert service._score(1, 6) == Decimal("16.7")
    assert service._score(0, 0) == Decimal("0.0")

    with pytest.raises(ValidationException):
        service._grade_answer(single, ["A"])


def test_public_exam_projections_never_expose_answers_before_settlement() -> None:
    snapshot = _snapshot()
    public = QuizExamService._public_question(snapshot)
    payload = public.model_dump()
    assert payload == {
        "id": 1,
        "category_id": 9,
        "library_id": None,
        "knowledge_point_id": None,
        "question_revision_id": None,
        "question_type": "single_choice",
        "question_text": "题目 1",
        "options": {"A": "一", "B": "二", "C": "三", "D": "四"},
        "image_urls": ["https://cdn.example.com/exam-stem.png"],
        "option_image_urls": {},
    }
    assert "correct_answer" not in payload
    assert "explanation" not in payload
    assert "correct_answer" not in QuizExamInProgressDetail.model_fields
    assert "correct_answer" not in QuizExamAbandonedDetail.model_fields
    assert "correct_answer" in QuizExamQuestionResult.model_fields


def test_new_exam_routes_are_authenticated_and_have_explicit_models() -> None:
    routes = {
        (next(iter(route.methods)), f"/api{route.path}"): route
        for route in quiz_router.routes
        if isinstance(route, APIRoute)
        and route.path.startswith("/quiz/exams")
        and route.methods
    }
    expected = {
        ("POST", "/api/quiz/exams"),
        ("GET", "/api/quiz/exams/current"),
        ("GET", "/api/quiz/exams"),
        ("GET", "/api/quiz/exams/{exam_id}"),
        ("PUT", "/api/quiz/exams/{exam_id}/answers/{exam_question_id}"),
        ("POST", "/api/quiz/exams/{exam_id}/submit"),
        ("POST", "/api/quiz/exams/{exam_id}/abandon"),
    }
    assert expected <= set(routes)
    for key in expected:
        route = routes[key]
        assert route.response_model is not None
        dependency_names = {
            dependency.call.__name__
            for dependency in route.dependant.dependencies
            if dependency.call is not None
        }
        assert "get_current_user" in dependency_names


def test_settled_finished_timestamp_prefers_terminal_transition() -> None:
    now = datetime.now(timezone.utc)
    exam = SimpleNamespace(submitted_at=now, timed_out_at=None, abandoned_at=None)
    assert QuizExamService._finished_at(exam) == now
