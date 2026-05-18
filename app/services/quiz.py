from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_ctx
from app.core.exceptions import NotFoundException, ValidationException
from app.models.quiz import QuizCategory, QuizCheckin, QuizQuestion, QuizRecord
from app.models.user import User
from app.schemas.common import PaginatedData

if TYPE_CHECKING:
    from app.schemas.quiz import (
        QuizCheckinRequest,
        QuizQuestionQuery,
        QuizSubmitRequest,
        QuizToggleRequest,
    )


DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
QUESTION_TYPES = {"single_choice", "multiple_choice", "judge"}
LOCAL_TZ = ZoneInfo("Asia/Shanghai")
JUDGE_TRUE_VALUES = {"1", "true", "t", "yes", "y", "对", "正确", "是", "√", "✓"}
JUDGE_FALSE_VALUES = {"0", "false", "f", "no", "n", "错", "错误", "否", "×", "x"}


def _read_field(data: Any, name: str, default: Any = None) -> Any:
    if data is None:
        return default
    if isinstance(data, dict):
        return data.get(name, default)
    return getattr(data, name, default)


def _require_positive_int(value: int | None, field: str) -> int:
    if value is None:
        raise ValidationException(f"{field} 不能为空")
    if not isinstance(value, int) or value <= 0:
        raise ValidationException(f"{field} 必须为正整数")
    return value


def _normalize_page(page: int | None, page_size: int | None) -> tuple[int, int]:
    page = page or DEFAULT_PAGE
    page_size = page_size or DEFAULT_PAGE_SIZE
    if not isinstance(page, int) or page <= 0:
        raise ValidationException("page 必须为正整数")
    if not isinstance(page_size, int) or page_size <= 0 or page_size > MAX_PAGE_SIZE:
        raise ValidationException(f"page_size 必须在 1-{MAX_PAGE_SIZE} 之间")
    return page, page_size


def _normalize_question_type(question_type: str | None) -> str | None:
    if question_type is None:
        return None
    value = question_type.strip()
    if value and value not in QUESTION_TYPES:
        raise ValidationException("question_type 不合法")
    return value or None


def _answer_to_storage(answer: Any) -> str:
    if answer is None:
        raise ValidationException("user_answer 不能为空")
    if isinstance(answer, (list, tuple, set)):
        value = ",".join(str(item).strip() for item in answer if str(item).strip())
    else:
        value = str(answer).strip()
    if not value:
        raise ValidationException("user_answer 不能为空")
    return value


def _split_multi_answer(answer: str) -> list[str]:
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


def _normalize_answer(answer: Any, question_type: str) -> str:
    value = _answer_to_storage(answer)
    if question_type == "multiple_choice":
        return ",".join(_split_multi_answer(value))
    if question_type == "judge":
        lowered = value.strip().lower()
        if lowered in JUDGE_TRUE_VALUES:
            return "TRUE"
        if lowered in JUDGE_FALSE_VALUES:
            return "FALSE"
    return value.strip().upper()


def _today() -> date:
    return datetime.now(LOCAL_TZ).date()


def _question_payload(question: QuizQuestion, *, include_correct_answer: bool = False) -> dict[str, Any]:
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


def _record_question_payload(
    record: QuizRecord,
    question: QuizQuestion,
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
        "question": _question_payload(question, include_correct_answer=False),
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }
    if include_correct_answer:
        payload["correct_answer"] = question.correct_answer
        payload["explanation"] = question.explanation
    return payload


def _checkin_payload(
    record: QuizCheckin | None,
    *,
    today: date,
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
            "today_checked_in": checked_in,
            "last_checkin_date": record.checkin_date,
        }
    return {
        "id": None,
        "checkin_date": today,
        "questions_completed": 0,
        "consecutive_days": consecutive_days,
        "checked_in": False,
        "today_checked_in": False,
        "last_checkin_date": last_checkin_date,
    }


class QuizService:
    async def list_categories(self) -> list[dict[str, Any]]:
        async with get_db_ctx() as db:
            categories = (
                await db.execute(
                    select(QuizCategory).order_by(
                        QuizCategory.parent_id.asc().nullsfirst(),
                        QuizCategory.id.asc(),
                    )
                )
            ).scalars().all()

        categories_by_id = {category.id: category for category in categories}
        children_by_parent: dict[int | None, list[QuizCategory]] = defaultdict(list)
        for category in categories:
            children_by_parent[category.parent_id].append(category)

        visited: set[int] = set()

        def build(category: QuizCategory, ancestors: set[int]) -> dict[str, Any] | None:
            if category.id in ancestors:
                return None
            visited.add(category.id)
            children = []
            for child in children_by_parent.get(category.id, []):
                child_payload = build(child, ancestors | {category.id})
                if child_payload is not None:
                    children.append(child_payload)
            return {
                "id": category.id,
                "name": category.name,
                "parent_id": category.parent_id,
                "description": category.description,
                "children": children,
            }

        roots = [
            category
            for category in categories
            if category.parent_id is None or category.parent_id not in categories_by_id
        ]
        tree: list[dict[str, Any]] = []
        for category in roots:
            payload = build(category, set())
            if payload is not None:
                tree.append(payload)
        for category in categories:
            if category.id not in visited:
                payload = build(category, set())
                if payload is not None:
                    tree.append(payload)
        return tree

    async def list_questions(
        self,
        query: QuizQuestionQuery | None = None,
        *,
        category_id: int | None = None,
        question_type: str | None = None,
        page: int | None = DEFAULT_PAGE,
        page_size: int | None = DEFAULT_PAGE_SIZE,
    ) -> PaginatedData[dict[str, Any]]:
        category_id = _read_field(query, "category_id", category_id)
        question_type = _normalize_question_type(_read_field(query, "question_type", question_type))
        page, page_size = _normalize_page(_read_field(query, "page", page), _read_field(query, "page_size", page_size))

        if category_id is not None:
            _require_positive_int(category_id, "category_id")

        stmt = select(QuizQuestion)
        if category_id is not None:
            stmt = stmt.where(QuizQuestion.category_id == category_id)
        if question_type is not None:
            stmt = stmt.where(QuizQuestion.question_type == question_type)

        async with get_db_ctx() as db:
            total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
            questions = (
                await db.execute(
                    stmt.order_by(QuizQuestion.id.asc()).offset((page - 1) * page_size).limit(page_size)
                )
            ).scalars().all()

        return PaginatedData[dict[str, Any]](
            items=[_question_payload(question, include_correct_answer=False) for question in questions],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def submit_answer(
        self,
        user_id: int,
        data: QuizSubmitRequest | None = None,
        *,
        question_id: int | None = None,
        user_answer: Any = None,
    ) -> dict[str, Any]:
        user_id = _require_positive_int(user_id, "user_id")
        question_id = _require_positive_int(_read_field(data, "question_id", question_id), "question_id")
        answer_for_storage = _answer_to_storage(_read_field(data, "user_answer", user_answer))

        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            question = await self._get_question(db, question_id)
            is_correct = _normalize_answer(answer_for_storage, question.question_type) == _normalize_answer(
                question.correct_answer,
                question.question_type,
            )
            record = await self._upsert_record(
                db,
                user_id=user_id,
                question_id=question_id,
                values={
                    "user_answer": answer_for_storage,
                    "is_correct": is_correct,
                    "is_wrong": not is_correct,
                },
            )
            return {
                **_record_question_payload(record, question, include_correct_answer=True),
                "correct_answer": question.correct_answer,
                "explanation": question.explanation,
            }

    async def list_wrong_book(
        self,
        user_id: int,
        *,
        page: int | None = DEFAULT_PAGE,
        page_size: int | None = DEFAULT_PAGE_SIZE,
    ) -> PaginatedData[dict[str, Any]]:
        return await self._list_record_questions(
            user_id=user_id,
            flag_field="is_wrong",
            page=page,
            page_size=page_size,
        )

    async def add_wrong_question(
        self,
        user_id: int,
        data: QuizToggleRequest | None = None,
        *,
        question_id: int | None = None,
    ) -> dict[str, Any]:
        return await self._set_record_flag(
            user_id=user_id,
            data=data,
            question_id=question_id,
            flag_field="is_wrong",
            flag_value=True,
            create_defaults={"is_correct": False},
        )

    async def remove_wrong_question(
        self,
        user_id: int,
        record_id: int | None = None,
        *,
        question_id: int | None = None,
    ) -> dict[str, Any]:
        return await self._unset_record_flag(
            user_id=user_id,
            record_id=record_id,
            question_id=question_id,
            flag_field="is_wrong",
        )

    async def list_collections(
        self,
        user_id: int,
        *,
        page: int | None = DEFAULT_PAGE,
        page_size: int | None = DEFAULT_PAGE_SIZE,
    ) -> PaginatedData[dict[str, Any]]:
        return await self._list_record_questions(
            user_id=user_id,
            flag_field="is_collected",
            page=page,
            page_size=page_size,
        )

    async def add_collection(
        self,
        user_id: int,
        data: QuizToggleRequest | None = None,
        *,
        question_id: int | None = None,
    ) -> dict[str, Any]:
        return await self._set_record_flag(
            user_id=user_id,
            data=data,
            question_id=question_id,
            flag_field="is_collected",
            flag_value=True,
            create_defaults={},
        )

    async def remove_collection(
        self,
        user_id: int,
        record_id: int | None = None,
        *,
        question_id: int | None = None,
    ) -> dict[str, Any]:
        return await self._unset_record_flag(
            user_id=user_id,
            record_id=record_id,
            question_id=question_id,
            flag_field="is_collected",
        )

    async def get_checkin_status(self, user_id: int) -> dict[str, Any]:
        user_id = _require_positive_int(user_id, "user_id")
        today = _today()
        yesterday = today - timedelta(days=1)
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            today_record = await self._get_checkin(db, user_id, today)
            if today_record is not None:
                return _checkin_payload(
                    today_record,
                    today=today,
                    checked_in=True,
                    consecutive_days=today_record.consecutive_days,
                )

            latest_record = (
                await db.execute(
                    select(QuizCheckin)
                    .where(QuizCheckin.user_id == user_id)
                    .order_by(QuizCheckin.checkin_date.desc(), QuizCheckin.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            consecutive_days = (
                latest_record.consecutive_days
                if latest_record is not None and latest_record.checkin_date == yesterday
                else 0
            )
            return _checkin_payload(
                None,
                today=today,
                checked_in=False,
                consecutive_days=consecutive_days,
                last_checkin_date=latest_record.checkin_date if latest_record else None,
            )

    async def checkin(
        self,
        user_id: int,
        data: QuizCheckinRequest | None = None,
        *,
        questions_completed: int | None = None,
    ) -> dict[str, Any]:
        user_id = _require_positive_int(user_id, "user_id")
        completed = _read_field(data, "questions_completed", questions_completed)
        completed = 0 if completed is None else completed
        if not isinstance(completed, int) or completed < 0:
            raise ValidationException("questions_completed 必须为非负整数")

        today = _today()
        yesterday = today - timedelta(days=1)
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            today_record = await self._get_checkin(db, user_id, today)
            if today_record is not None:
                return _checkin_payload(
                    today_record,
                    today=today,
                    checked_in=True,
                    consecutive_days=today_record.consecutive_days,
                )

            yesterday_record = await self._get_checkin(db, user_id, yesterday)
            consecutive_days = (yesterday_record.consecutive_days + 1) if yesterday_record else 1
            record = QuizCheckin(
                user_id=user_id,
                checkin_date=today,
                questions_completed=completed,
                consecutive_days=consecutive_days,
            )
            db.add(record)
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                record = await self._get_checkin(db, user_id, today)
                if record is None:
                    raise
            await db.refresh(record)
            return _checkin_payload(
                record,
                today=today,
                checked_in=True,
                consecutive_days=record.consecutive_days,
            )

    async def _require_user(self, db: AsyncSession, user_id: int) -> User:
        user = await db.get(User, user_id)
        if user is None or not user.is_active:
            raise NotFoundException("用户")
        return user

    async def _get_question(self, db: AsyncSession, question_id: int) -> QuizQuestion:
        question = await db.get(QuizQuestion, question_id)
        if question is None:
            raise NotFoundException("题目")
        return question

    async def _get_record(self, db: AsyncSession, user_id: int, question_id: int) -> QuizRecord | None:
        return (
            await db.execute(
                select(QuizRecord)
                .where(
                    QuizRecord.user_id == user_id,
                    QuizRecord.question_id == question_id,
                )
                .order_by(QuizRecord.id.asc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _upsert_record(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        question_id: int,
        values: dict[str, Any],
    ) -> QuizRecord:
        record = await self._get_record(db, user_id, question_id)
        if record is None:
            create_values = {
                "user_id": user_id,
                "question_id": question_id,
                "user_answer": None,
                "is_correct": None,
                "is_collected": False,
                "is_wrong": False,
            }
            create_values.update(values)
            record = QuizRecord(**create_values)
            db.add(record)
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                record = await self._get_record(db, user_id, question_id)
                if record is None:
                    raise
                for key, value in values.items():
                    setattr(record, key, value)
                await db.commit()
        else:
            for key, value in values.items():
                setattr(record, key, value)
            await db.commit()
        await db.refresh(record)
        return record

    async def _list_record_questions(
        self,
        *,
        user_id: int,
        flag_field: str,
        page: int | None,
        page_size: int | None,
    ) -> PaginatedData[dict[str, Any]]:
        user_id = _require_positive_int(user_id, "user_id")
        page, page_size = _normalize_page(page, page_size)
        flag_column = getattr(QuizRecord, flag_field)

        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            filters = (QuizRecord.user_id == user_id, flag_column.is_(True))
            total = (
                await db.execute(select(func.count()).select_from(QuizRecord).where(*filters))
            ).scalar() or 0
            rows = (
                await db.execute(
                    select(QuizRecord, QuizQuestion)
                    .join(QuizQuestion, QuizQuestion.id == QuizRecord.question_id)
                    .where(*filters)
                    .order_by(QuizRecord.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).all()

        return PaginatedData[dict[str, Any]](
            items=[
                _record_question_payload(record, question, include_correct_answer=False)
                for record, question in rows
            ],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def _set_record_flag(
        self,
        *,
        user_id: int,
        data: QuizToggleRequest | None,
        question_id: int | None,
        flag_field: str,
        flag_value: bool,
        create_defaults: dict[str, Any],
    ) -> dict[str, Any]:
        user_id = _require_positive_int(user_id, "user_id")
        question_id = _require_positive_int(_read_field(data, "question_id", question_id), "question_id")

        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            question = await self._get_question(db, question_id)
            values = {flag_field: flag_value, **create_defaults}
            record = await self._upsert_record(
                db,
                user_id=user_id,
                question_id=question_id,
                values=values,
            )
            return _record_question_payload(record, question, include_correct_answer=False)

    async def _unset_record_flag(
        self,
        *,
        user_id: int,
        record_id: int | None,
        question_id: int | None,
        flag_field: str,
    ) -> dict[str, Any]:
        user_id = _require_positive_int(user_id, "user_id")
        if record_id is None and question_id is None:
            raise ValidationException("record_id 或 question_id 不能为空")

        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            stmt = select(QuizRecord, QuizQuestion).join(
                QuizQuestion,
                QuizQuestion.id == QuizRecord.question_id,
            )
            if record_id is not None:
                record_id = _require_positive_int(record_id, "record_id")
                stmt = stmt.where(QuizRecord.user_id == user_id, QuizRecord.id == record_id)
            else:
                question_id = _require_positive_int(question_id, "question_id")
                stmt = stmt.where(QuizRecord.user_id == user_id, QuizRecord.question_id == question_id)

            row = (await db.execute(stmt.limit(1))).first()
            if row is None:
                raise NotFoundException("答题记录")

            record, question = row
            setattr(record, flag_field, False)
            await db.commit()
            await db.refresh(record)
            return _record_question_payload(record, question, include_correct_answer=False)

    async def _get_checkin(self, db: AsyncSession, user_id: int, checkin_date: date) -> QuizCheckin | None:
        return (
            await db.execute(
                select(QuizCheckin)
                .where(
                    QuizCheckin.user_id == user_id,
                    QuizCheckin.checkin_date == checkin_date,
                )
                .order_by(QuizCheckin.id.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
