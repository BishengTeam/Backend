from __future__ import annotations

from decimal import Decimal

import pytest

from app.schemas.admin_quiz_review_contract import (
    AdminQuizReviewSubmitRequest,
    AdminQuizReviewVerdictItem,
)
from app.schemas.quiz_contract import QuizExamReviewPendingDetail
from app.services.admin_quiz_review import _VERDICT_RATIOS
from app.services.quiz_exam import QuizExamService


def _answer(ratio: Decimal | None):
    from types import SimpleNamespace

    return SimpleNamespace(score_ratio=ratio)


def _snapshot(snapshot_id: int):
    from types import SimpleNamespace

    return SimpleNamespace(id=snapshot_id)


def test_verdict_ratios_are_frozen_three_level_values() -> None:
    assert _VERDICT_RATIOS == {
        "wrong": Decimal(0),
        "partial": Decimal("0.5"),
        "correct": Decimal(1),
    }


def test_score_ratio_sums_partial_credit_before_rounding() -> None:
    assert QuizExamService._score_ratio(
        Decimal("1.5"), 3
    ) == Decimal("50.0")
    assert QuizExamService._score_ratio(
        Decimal("2.333"), 3
    ) == Decimal("77.8")
    assert QuizExamService._score_ratio(Decimal(0), 0) == Decimal("0.0")


def test_partial_count_counts_only_strictly_partial_answers() -> None:
    snapshots = [_snapshot(1), _snapshot(2), _snapshot(3), _snapshot(4), _snapshot(5)]
    answers = {
        1: _answer(Decimal(1)),
        2: _answer(Decimal("0.5")),
        3: _answer(Decimal("0.333")),
        4: _answer(Decimal(0)),
        5: _answer(None),
    }
    assert QuizExamService._partial_count(snapshots, answers) == 2


def test_review_pending_detail_hides_all_score_fields() -> None:
    detail = QuizExamReviewPendingDetail(
        id=7,
        status="completed",
        review_status="pending",
        question_count=10,
        duration_seconds=3600,
        started_at="2026-09-01T10:00:00+00:00",
        deadline_at="2026-09-01T11:00:00+00:00",
        finished_at="2026-09-01T10:40:00+00:00",
    )
    dumped = detail.model_dump()
    assert "score" not in dumped
    assert "correct_count" not in dumped
    assert "questions" not in dumped


def test_verdict_submission_requires_bounded_unique_items() -> None:
    request = AdminQuizReviewSubmitRequest(
        verdicts=[
            AdminQuizReviewVerdictItem(exam_question_id=1, verdict="correct"),
            AdminQuizReviewVerdictItem(
                exam_question_id=2,
                verdict="partial",
                comment="要点不全",
            ),
        ]
    )
    assert len(request.verdicts) == 2

    with pytest.raises(ValueError):
        AdminQuizReviewVerdictItem(exam_question_id=1, verdict="almost")
    with pytest.raises(ValueError):
        AdminQuizReviewVerdictItem(
            exam_question_id=1, verdict="wrong", comment="x" * 513
        )
