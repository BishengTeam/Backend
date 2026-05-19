from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.exceptions import ValidationException

DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
QUESTION_TYPES = {"single_choice", "multiple_choice", "judge"}
JUDGE_TRUE_VALUES = {"1", "true", "t", "yes", "y", "对", "正确", "是", "√", "✓"}
JUDGE_FALSE_VALUES = {"0", "false", "f", "no", "n", "错", "错误", "否", "×", "x"}


def read_field(data: Any, name: str, default: Any = None) -> Any:
    if data is None:
        return default
    if isinstance(data, dict):
        return data.get(name, default)
    return getattr(data, name, default)


def require_positive_int(value: int | None, field: str) -> int:
    if value is None:
        raise ValidationException(f"{field} 不能为空")
    if not isinstance(value, int) or value <= 0:
        raise ValidationException(f"{field} 必须为正整数")
    return value


def normalize_page(page: int | None, page_size: int | None) -> tuple[int, int]:
    page = page or DEFAULT_PAGE
    page_size = page_size or DEFAULT_PAGE_SIZE
    if not isinstance(page, int) or page <= 0:
        raise ValidationException("page 必须为正整数")
    if not isinstance(page_size, int) or page_size <= 0 or page_size > MAX_PAGE_SIZE:
        raise ValidationException(f"page_size 必须在 1-{MAX_PAGE_SIZE} 之间")
    return page, page_size


def normalize_question_type(question_type: str | None) -> str | None:
    if question_type is None:
        return None
    value = question_type.strip()
    if value and value not in QUESTION_TYPES:
        raise ValidationException("question_type 不合法")
    return value or None


def answer_to_storage(answer: Any) -> str:
    if answer is None:
        raise ValidationException("user_answer 不能为空")
    if isinstance(answer, (list, tuple, set)):
        value = ",".join(str(item).strip() for item in answer if str(item).strip())
    else:
        value = str(answer).strip()
    if not value:
        raise ValidationException("user_answer 不能为空")
    return value


def split_multi_answer(answer: str) -> list[str]:
    value = answer.strip().replace("，", ",").replace("；", ",").replace("、", ",")
    for separator in ("|", "/", ";"):
        value = value.replace(separator, ",")
    if "," in value:
        parts = value.split(",")
    elif " " in value:
        parts = value.split()
    elif len(value) > 1 and value.isascii() and value.isalpha():
        parts = list(value)
    else:
        parts = [value]
    return sorted(part.strip().upper() for part in parts if part.strip())


def normalize_answer(answer: Any, question_type: str) -> str:
    value = answer_to_storage(answer)
    if question_type == "multiple_choice":
        return ",".join(split_multi_answer(value))
    if question_type == "judge":
        lowered = value.strip().lower()
        if lowered in JUDGE_TRUE_VALUES:
            return "TRUE"
        if lowered in JUDGE_FALSE_VALUES:
            return "FALSE"
    return value.strip().upper()


def today() -> date:
    return datetime.now(ZoneInfo(settings.APP_TIMEZONE)).date()


def question_payload(question: Any, *, include_correct_answer: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": question.id,
        "category_id": question.category_id,
        "question_type": question.question_type,
        "question_text": question.question_text,
        "options": question.options,
        "explanation": question.explanation,
    }
    if include_correct_answer:
        payload["correct_answer"] = question.correct_answer
    return payload


def record_question_payload(
    record: Any,
    question: Any,
    *,
    include_correct_answer: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": record.id,
        "record_id": record.id,
        "question_id": question.id,
        "user_answer": record.user_answer,
        "is_correct": record.is_correct,
        "is_wrong": bool(record.is_wrong),
        "is_collected": bool(record.is_collected),
        "question": question_payload(question, include_correct_answer=False),
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }
    if include_correct_answer:
        payload["correct_answer"] = question.correct_answer
        payload["explanation"] = question.explanation
    return payload


def checkin_payload(
    record: Any | None,
    *,
    target_date: date,
    checked_in: bool,
    consecutive_days: int,
    last_checkin_date: date | None = None,
) -> dict[str, Any]:
    if record is not None:
        return {
            "id": record.id,
            "checkin_date": record.checkin_date,
            "questions_completed": record.questions_completed,
            "consecutive_days": record.consecutive_days,
            "checked_in": checked_in,
            "last_checkin_date": record.checkin_date,
        }
    return {
        "id": None,
        "checkin_date": target_date,
        "questions_completed": 0,
        "consecutive_days": consecutive_days,
        "checked_in": False,
        "last_checkin_date": last_checkin_date,
    }
