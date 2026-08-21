"""Frozen normalization and validation rules for the quiz domain."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias


QuizAnswer: TypeAlias = str | list[str]
_OPTION_KEYS = ("A", "B", "C", "D")
_WHITESPACE_RE = re.compile(r"\s+")
JUDGE_OPTIONS: dict[str, str] = {"A": "正确", "B": "错误"}


class QuizQuestionType(StrEnum):
    SINGLE_CHOICE = "single_choice"
    MULTIPLE_CHOICE = "multiple_choice"
    JUDGE = "judge"


class QuizCategoryStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class QuizQuestionStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    DISABLED = "disabled"
    DELETED = "deleted"


class QuizLibraryStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    SUSPENDED = "suspended"
    ARCHIVED = "archived"
    DELETED = "deleted"


class QuizLibraryAccessMode(StrEnum):
    PENDING = "access_mode_pending"
    FREE = "free"
    COURSE_ENTITLEMENT = "course_entitlement"


class QuizContentStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    DELETED = "deleted"


class QuizPracticeMode(StrEnum):
    NORMAL = "normal"
    WRONG = "wrong"
    FULL = "full"
    WRONG_ONLY = "wrong_only"
    LEGACY_LIMITED = "legacy_limited"


class QuizPracticeScopeType(StrEnum):
    LIBRARY = "library"
    MODULE = "module"
    KNOWLEDGE_POINT = "knowledge_point"


class QuizPracticeSessionStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABANDONED = "abandoned"
    EXPIRED = "expired"
    TERMINATED = "terminated"


class QuizWrongStatus(StrEnum):
    ACTIVE = "active"
    CLEARED = "cleared"


class QuizExamStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    TIMED_OUT = "timed_out"
    ABANDONED = "abandoned"


class QuizImportSourceType(StrEnum):
    CSV = "csv"
    JSON = "json"


class QuizImportStatus(StrEnum):
    QUEUED = "queued"
    VALIDATING = "validating"
    VALIDATION_FAILED = "validation_failed"
    IMPORTING = "importing"
    AWAITING_CATEGORY_CONFIRMATION = "awaiting_category_confirmation"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class QuizRuleViolation(ValueError):
    """A field-scoped domain validation error suitable for row-level reports."""

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


@dataclass(frozen=True, slots=True)
class NormalizedQuizQuestion:
    question_type: QuizQuestionType
    question_text: str
    normalized_question_text: str
    question_text_hash: str
    options: dict[str, str] | None
    correct_answer: QuizAnswer | None
    explanation: str | None
    image_urls: list[str]


def _enum_value(value: str | StrEnum, enum_type: type[StrEnum], field: str) -> StrEnum:
    try:
        return enum_type(str(value).strip())
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise QuizRuleViolation(field, f"必须为以下值之一：{allowed}") from exc


def _clean_text(
    value: object,
    *,
    field: str,
    max_length: int,
    required: bool,
) -> str | None:
    if value is None:
        if required:
            raise QuizRuleViolation(field, "不能为空")
        return None
    if not isinstance(value, str):
        raise QuizRuleViolation(field, "必须是字符串")
    cleaned = value.strip()
    if not cleaned:
        if required:
            raise QuizRuleViolation(field, "不能为空")
        return None
    if len(cleaned) > max_length:
        raise QuizRuleViolation(field, f"长度不能超过 {max_length}")
    for char in cleaned:
        if unicodedata.category(char) == "Cc" and char not in {"\n", "\r", "\t"}:
            raise QuizRuleViolation(field, "不能包含控制字符")
    return cleaned


def normalize_category_name(name: object) -> str:
    cleaned = _clean_text(name, field="name", max_length=128, required=True)
    assert cleaned is not None
    return _WHITESPACE_RE.sub(" ", cleaned)


def normalize_question_text(question_text: object) -> str:
    cleaned = _clean_text(
        question_text,
        field="question_text",
        max_length=1024,
        required=True,
    )
    assert cleaned is not None
    return _WHITESPACE_RE.sub(" ", cleaned)


def question_text_digest(normalized_question_text: str) -> str:
    return hashlib.sha256(normalized_question_text.encode("utf-8")).hexdigest()


def normalize_image_urls(value: object = None) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        items = [part for part in re.split(r"[\r\n,，]+", value) if part.strip()]
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        items = list(value)
    else:
        raise QuizRuleViolation("image_urls", "题干图片必须是 URL 数组")

    normalized: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, str):
            raise QuizRuleViolation(f"image_urls.{index}", "图片 URL 必须是字符串")
        url = item.strip()
        if not url:
            continue
        if len(url) > 512:
            raise QuizRuleViolation(f"image_urls.{index}", "图片 URL 长度不能超过 512")
        if not re.match(r"^https?://", url, flags=re.IGNORECASE):
            raise QuizRuleViolation(f"image_urls.{index}", "图片 URL 必须以 http:// 或 https:// 开头")
        if url not in seen:
            normalized.append(url)
            seen.add(url)
    if len(normalized) > 9:
        raise QuizRuleViolation("image_urls", "题干图片最多 9 张")
    return normalized


def _normalize_option_key(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise QuizRuleViolation(field, "选项键必须是字符串")
    key = value.strip().upper()
    if key not in _OPTION_KEYS:
        raise QuizRuleViolation(field, "选项键只能为 A、B、C、D")
    return key


def _normalize_options(
    question_type: QuizQuestionType,
    options: object,
    *,
    require_publishable: bool,
) -> dict[str, str] | None:
    if question_type is QuizQuestionType.JUDGE:
        if options is None:
            return dict(JUDGE_OPTIONS)
        if not isinstance(options, Mapping):
            raise QuizRuleViolation("options", "判断题选项必须是对象")
        normalized = _normalize_options_mapping(options)
        if normalized != JUDGE_OPTIONS:
            raise QuizRuleViolation("options", "判断题固定为 A=正确、B=错误")
        return dict(JUDGE_OPTIONS)

    if options is None:
        if require_publishable:
            raise QuizRuleViolation("options", "发布前必须填写选项")
        return None
    if not isinstance(options, Mapping):
        raise QuizRuleViolation("options", "必须是以 A-D 为键的对象")

    normalized = _normalize_options_mapping(options)
    keys = tuple(normalized)
    expected = _OPTION_KEYS[: len(keys)]
    if keys != expected:
        raise QuizRuleViolation("options", "选项键必须从 A 开始连续排列")

    count = len(normalized)
    if question_type is QuizQuestionType.SINGLE_CHOICE:
        if require_publishable and count not in {3, 4}:
            raise QuizRuleViolation("options", "单选题发布时必须有 3 至 4 个选项")
        if count > 4:
            raise QuizRuleViolation("options", "单选题最多 4 个选项")
    elif question_type is QuizQuestionType.MULTIPLE_CHOICE:
        if require_publishable and count != 4:
            raise QuizRuleViolation("options", "多选题发布时必须有 A-D 四个选项")
        if count > 4:
            raise QuizRuleViolation("options", "多选题最多 4 个选项")
    return normalized


def _normalize_options_mapping(options: Mapping[object, object]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for raw_key, raw_value in options.items():
        key = _normalize_option_key(raw_key, field="options")
        if key in normalized:
            raise QuizRuleViolation("options", f"选项 {key} 重复")
        value = _clean_text(
            raw_value,
            field=f"options.{key}",
            max_length=1024,
            required=True,
        )
        assert value is not None
        normalized[key] = value
    return {key: normalized[key] for key in _OPTION_KEYS if key in normalized}


def _normalize_correct_answer(
    question_type: QuizQuestionType,
    answer: object,
    *,
    options: dict[str, str] | None,
    require_publishable: bool,
) -> QuizAnswer | None:
    if answer is None or answer == "" or answer == []:
        if require_publishable:
            raise QuizRuleViolation("correct_answer", "发布前必须填写标准答案")
        return None

    if question_type in {QuizQuestionType.SINGLE_CHOICE, QuizQuestionType.JUDGE}:
        key = _normalize_option_key(answer, field="correct_answer")
        if options is not None and key not in options:
            raise QuizRuleViolation("correct_answer", "标准答案不在现有选项中")
        return key

    if isinstance(answer, (str, bytes)) or not isinstance(answer, Sequence):
        raise QuizRuleViolation("correct_answer", "多选题标准答案必须是字符串数组")
    keys = sorted(
        {
            _normalize_option_key(item, field="correct_answer")
            for item in answer
        }
    )
    minimum = 2 if require_publishable else 1
    if not minimum <= len(keys) <= 4:
        raise QuizRuleViolation(
            "correct_answer",
            "多选题发布时至少 2 个答案" if require_publishable else "多选题答案不能为空",
        )
    if options is not None and any(key not in options for key in keys):
        raise QuizRuleViolation("correct_answer", "标准答案包含不存在的选项")
    return keys


def normalize_question_payload(
    *,
    question_type: str | QuizQuestionType,
    question_text: object,
    options: object = None,
    correct_answer: object = None,
    explanation: object = None,
    image_urls: object = None,
    require_publishable: bool = False,
) -> NormalizedQuizQuestion:
    normalized_type = _enum_value(
        question_type,
        QuizQuestionType,
        "question_type",
    )
    assert isinstance(normalized_type, QuizQuestionType)
    display_text = _clean_text(
        question_text,
        field="question_text",
        max_length=1024,
        required=True,
    )
    assert display_text is not None
    normalized_text = normalize_question_text(display_text)
    normalized_options = _normalize_options(
        normalized_type,
        options,
        require_publishable=require_publishable,
    )
    normalized_answer = _normalize_correct_answer(
        normalized_type,
        correct_answer,
        options=normalized_options,
        require_publishable=require_publishable,
    )
    normalized_explanation = _clean_text(
        explanation,
        field="explanation",
        max_length=1024,
        required=require_publishable,
    )
    normalized_image_urls = normalize_image_urls(image_urls)
    return NormalizedQuizQuestion(
        question_type=normalized_type,
        question_text=display_text,
        normalized_question_text=normalized_text,
        question_text_hash=question_text_digest(normalized_text),
        options=normalized_options,
        correct_answer=normalized_answer,
        explanation=normalized_explanation,
        image_urls=normalized_image_urls,
    )


def normalize_submitted_answer(
    question_type: str | QuizQuestionType,
    answer: object,
    *,
    options: Mapping[str, str],
) -> QuizAnswer:
    normalized_type = _enum_value(question_type, QuizQuestionType, "question_type")
    assert isinstance(normalized_type, QuizQuestionType)
    normalized_options = _normalize_options_mapping(options)

    if normalized_type in {QuizQuestionType.SINGLE_CHOICE, QuizQuestionType.JUDGE}:
        key = _normalize_option_key(answer, field="user_answer")
        if key not in normalized_options:
            raise QuizRuleViolation("user_answer", "答案不在现有选项中")
        return key

    if isinstance(answer, (str, bytes)) or not isinstance(answer, Sequence):
        raise QuizRuleViolation("user_answer", "多选题答案必须是字符串数组")
    keys = sorted(
        {_normalize_option_key(item, field="user_answer") for item in answer}
    )
    if not 1 <= len(keys) <= 4:
        raise QuizRuleViolation("user_answer", "多选题至少选择 1 个选项")
    if any(key not in normalized_options for key in keys):
        raise QuizRuleViolation("user_answer", "答案包含不存在的选项")
    return keys


def answers_match(
    question_type: str | QuizQuestionType,
    user_answer: object,
    correct_answer: object,
    *,
    options: Mapping[str, str],
) -> bool:
    submitted = normalize_submitted_answer(
        question_type,
        user_answer,
        options=options,
    )
    normalized_type = _enum_value(question_type, QuizQuestionType, "question_type")
    assert isinstance(normalized_type, QuizQuestionType)
    expected = _normalize_correct_answer(
        normalized_type,
        correct_answer,
        options=_normalize_options_mapping(options),
        require_publishable=True,
    )
    return submitted == expected
