from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.main import app
from app.schemas.quiz_contract import (
    QuizCollectionItem,
    QuizPracticeAttemptCreate,
    QuizPracticeQuestionState,
    QuizPracticeSessionCreate,
    QuizPublicQuestion,
    QuizWrongBookItem,
)
from app.services.quiz_practice import QuizPracticeService


def _category(
    category_id: int,
    *,
    parent_id: int | None = None,
    status: str = "active",
    sort_order: int = 0,
    name: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=category_id,
        name=name or f"category-{category_id}",
        parent_id=parent_id,
        depth=1 if parent_id is None else 2,
        description=None,
        status=status,
        sort_order=sort_order,
    )


def test_category_tree_hides_disabled_and_empty_branches_and_counts_descendants() -> None:
    root = _category(1, sort_order=2)
    populated_child = _category(2, parent_id=1, sort_order=2)
    empty_child = _category(3, parent_id=1, sort_order=1)
    disabled_child = _category(4, parent_id=1, status="disabled")
    disabled_descendant = _category(5, parent_id=4)
    second_root = _category(6, sort_order=1)

    nodes = QuizPracticeService._category_nodes(
        [
            root,
            populated_child,
            empty_child,
            disabled_child,
            disabled_descendant,
            second_root,
        ],
        {1: 1, 2: 2, 4: 4, 5: 3, 6: 1},
    )

    assert [node.id for node in nodes] == [6, 1]
    assert nodes[0].question_count == 1
    assert nodes[1].question_count == 3
    assert [child.id for child in nodes[1].children] == [2]
    assert nodes[1].children[0].question_count == 2


def test_effective_category_scope_includes_active_descendants_only() -> None:
    categories = [
        _category(1),
        _category(2, parent_id=1),
        _category(3, parent_id=2),
        _category(4, parent_id=1, status="disabled"),
        _category(5, parent_id=4),
    ]

    _, selected = QuizPracticeService._effective_category_ids(categories, root_id=1)

    assert selected == {1, 2, 3}


def test_public_question_projection_never_contains_answer_or_explanation() -> None:
    category = _category(1, name="Networking")
    question = SimpleNamespace(
        id=9,
        category_id=1,
        question_type="single_choice",
        question_text="Which option is correct?",
        options={"A": "One", "B": "Two", "C": "Three"},
        correct_answer="A",
        explanation="Because A is correct.",
        lock_version=3,
    )

    snapshot = QuizPracticeService._question_snapshot(question, {1: category})
    public = QuizPracticeService._public_question_from_snapshot(snapshot)
    payload = public.model_dump()

    assert payload == {
        "id": 9,
        "category_id": 1,
        "library_id": None,
        "knowledge_point_id": None,
        "question_revision_id": None,
        "question_type": "single_choice",
        "question_text": "Which option is correct?",
        "options": {"A": "One", "B": "Two", "C": "Three"},
    }
    assert "correct_answer" not in QuizPublicQuestion.model_fields
    assert "explanation" not in QuizPublicQuestion.model_fields
    assert "correct_answer" not in QuizWrongBookItem.model_fields
    assert "explanation" not in QuizCollectionItem.model_fields


def test_pending_practice_question_omits_answer_key_fields_from_wire_payload() -> None:
    pending = QuizPracticeQuestionState(
        id=9,
        category_id=1,
        question_type="single_choice",
        question_text="Which option is correct?",
        options={"A": "One", "B": "Two", "C": "Three"},
        session_question_id=19,
        position=1,
        category_path=[],
        answered=False,
        attempt_count=0,
    )

    payload = pending.model_dump()

    assert payload["latest_result"] is None
    assert "correct_answer" not in payload
    assert "explanation" not in payload
    assert "is_correct" not in payload

    settled_data = pending.model_dump()
    settled_data.update(
        answered=True,
        user_answer="A",
        correct_answer="A",
        explanation="Because A is correct.",
        is_correct=True,
    )
    settled = QuizPracticeQuestionState(**settled_data)
    settled_payload = settled.model_dump()
    assert settled_payload["correct_answer"] == "A"
    assert settled_payload["explanation"] == "Because A is correct."
    assert settled_payload["is_correct"] is True


def test_practice_request_rules_and_answer_canonicalization() -> None:
    normal = QuizPracticeSessionCreate(
        mode="normal",
        category_id=1,
        question_count=10,
    )
    wrong = QuizPracticeSessionCreate(mode="wrong")
    answer = QuizPracticeAttemptCreate(
        session_question_id=1,
        idempotency_key="request-0001",
        user_answer=["C", "A", "A"],
    )

    assert normal.question_count == 10
    assert wrong.category_id is None
    assert wrong.question_count == 20
    assert answer.user_answer == ["A", "C"]

    with pytest.raises(ValidationError):
        QuizPracticeSessionCreate(mode="normal", category_id=1)
    with pytest.raises(ValidationError):
        QuizPracticeSessionCreate(mode="normal", category_id=1, question_count=9)
    with pytest.raises(ValidationError):
        QuizPracticeSessionCreate(mode="wrong", category_id=1)


def test_shanghai_day_boundaries_and_history_utc_range() -> None:
    before_midnight = datetime(2026, 8, 6, 15, 59, tzinfo=timezone.utc)
    at_midnight = datetime(2026, 8, 6, 16, 0, tzinfo=timezone.utc)

    assert QuizPracticeService._local_date(before_midnight) == date(2026, 8, 6)
    assert QuizPracticeService._local_date(at_midnight) == date(2026, 8, 7)

    start_at, end_at = QuizPracticeService._utc_range(
        date(2026, 8, 7),
        date(2026, 8, 7),
    )
    assert start_at == datetime(2026, 8, 6, 16, 0, tzinfo=timezone.utc)
    assert end_at == datetime(2026, 8, 7, 16, 0, tzinfo=timezone.utc)


def test_question_list_requires_login_while_category_tree_is_public() -> None:
    routes = {route.path: route for route in app.routes if hasattr(route, "dependant")}

    category_dependencies = {
        dependency.call.__name__
        for dependency in routes["/api/quiz/categories"].dependant.dependencies
        if dependency.call is not None
    }
    question_dependencies = {
        dependency.call.__name__
        for dependency in routes["/api/quiz/questions"].dependant.dependencies
        if dependency.call is not None
    }

    assert "get_current_user" not in category_dependencies
    assert "get_current_user" in question_dependencies
