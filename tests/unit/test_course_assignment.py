from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.domain.community.src.rule.quiz import (
    OBJECTIVE_QUESTION_TYPES,
    QuizRuleViolation,
    QuizQuestionType,
    normalize_question_payload,
    normalize_submitted_answer,
)
from app.port.exceptions import ValidationException
from app.schemas.course_assignment import CourseAssignmentEssayScoreInput
from app.services.course_assignment import CourseAssignmentService


def _question(question_id: int, question_type: QuizQuestionType):
    return SimpleNamespace(id=question_id, question_type=question_type.value)


def test_objective_question_types_exclude_essay_for_ordinary_quiz_flows() -> None:
    assert set(OBJECTIVE_QUESTION_TYPES) == {
        QuizQuestionType.SINGLE_CHOICE.value,
        QuizQuestionType.MULTIPLE_CHOICE.value,
        QuizQuestionType.JUDGE.value,
    }


def test_essay_question_normalization_and_student_answer_limit() -> None:
    normalized = normalize_question_payload(
        question_type=QuizQuestionType.ESSAY,
        question_text="简述课程作业的评阅流程",
        options=None,
        correct_answer=None,
        reference_answer="按提交、领取、评分、发布成绩的顺序评阅",
        explanation="参考评分标准",
    )
    assert normalized.options is None
    assert normalized.correct_answer is None
    assert normalized.reference_answer == "按提交、领取、评分、发布成绩的顺序评阅"

    answer = normalize_submitted_answer(
        QuizQuestionType.ESSAY,
        "  学生答案  ",
        options={},
    )
    assert answer == "学生答案"
    with pytest.raises(QuizRuleViolation, match="5000"):
        normalize_submitted_answer(
            QuizQuestionType.ESSAY,
            "答" * 5001,
            options={},
        )


def test_assignment_scores_allocate_objective_remainder_to_last_question() -> None:
    questions = [
        _question(index, QuizQuestionType.SINGLE_CHOICE)
        for index in range(1, 8)
    ] + [
        _question(8, QuizQuestionType.ESSAY),
        _question(9, QuizQuestionType.ESSAY),
    ]
    scores = CourseAssignmentService._allocate_scores(
        questions,
        [
            CourseAssignmentEssayScoreInput(question_id=8, score=Decimal("20")),
            CourseAssignmentEssayScoreInput(question_id=9, score=Decimal("20")),
        ],
        {},
    )

    assert [scores[index] for index in range(1, 7)] == [Decimal("8.57")] * 6
    assert scores[7] == Decimal("8.58")
    assert scores[8] == Decimal("20.00")
    assert scores[9] == Decimal("20.00")
    assert sum(scores.values(), Decimal("0")) == Decimal("100.00")


def test_assignment_scores_require_exact_100_for_essay_only_assignment() -> None:
    questions = [_question(1, QuizQuestionType.ESSAY)]
    scores = CourseAssignmentService._allocate_scores(
        questions,
        [CourseAssignmentEssayScoreInput(question_id=1, score=Decimal("100"))],
        {},
    )
    assert scores == {1: Decimal("100.00")}

    with pytest.raises(ValidationException, match="没有客观题"):
        CourseAssignmentService._allocate_scores(
            questions,
            [CourseAssignmentEssayScoreInput(question_id=1, score=Decimal("20"))],
            {},
        )


def test_assignment_essay_scores_reject_invalid_precision_and_total() -> None:
    questions = [
        _question(1, QuizQuestionType.ESSAY),
        _question(2, QuizQuestionType.ESSAY),
        _question(3, QuizQuestionType.JUDGE),
    ]
    with pytest.raises(ValidationError, match="1 decimal place"):
        CourseAssignmentService._allocate_scores(
            questions,
            [
                CourseAssignmentEssayScoreInput(
                    question_id=1,
                    score=Decimal("20.01"),
                ),
                CourseAssignmentEssayScoreInput(question_id=2, score=Decimal("20")),
            ],
            {},
        )
    with pytest.raises(ValidationException, match="不能超过 100"):
        CourseAssignmentService._allocate_scores(
            questions,
            [
                CourseAssignmentEssayScoreInput(question_id=1, score=Decimal("60")),
                CourseAssignmentEssayScoreInput(question_id=2, score=Decimal("60")),
            ],
            {},
        )
