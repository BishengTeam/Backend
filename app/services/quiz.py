from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_ctx
from app.core.exceptions import NotFoundException, ValidationException
from app.models.quiz import QuizCategory, QuizCheckin, QuizQuestion, QuizRecord
from app.models.user import User
from app.schemas.common import PaginatedData
from app.utils.quiz_helpers import (
    answer_to_storage,
    checkin_payload,
    normalize_answer,
    normalize_page,
    normalize_question_type,
    question_payload,
    read_field,
    record_question_payload,
    require_positive_int,
    today,
)

if TYPE_CHECKING:
    from app.schemas.quiz import (
        QuizCheckinRequest,
        QuizQuestionQuery,
        QuizSubmitRequest,
        QuizToggleRequest,
    )


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
        page: int | None = None,
        page_size: int | None = None,
    ) -> PaginatedData[dict[str, Any]]:
        category_id = read_field(query, "category_id", category_id)
        question_type = normalize_question_type(read_field(query, "question_type", question_type))
        page, page_size = normalize_page(read_field(query, "page", page), read_field(query, "page_size", page_size))

        if category_id is not None:
            require_positive_int(category_id, "category_id")

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
            items=[question_payload(question, include_correct_answer=False) for question in questions],
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
        user_id = require_positive_int(user_id, "user_id")
        question_id = require_positive_int(read_field(data, "question_id", question_id), "question_id")
        answer_for_storage = answer_to_storage(read_field(data, "user_answer", user_answer))

        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            question = await self._get_question(db, question_id)
            is_correct = normalize_answer(answer_for_storage, question.question_type) == normalize_answer(
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
                **record_question_payload(record, question, include_correct_answer=True),
                "correct_answer": question.correct_answer,
                "explanation": question.explanation,
            }

    async def list_wrong_book(
        self,
        user_id: int,
        *,
        page: int | None = None,
        page_size: int | None = None,
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
        page: int | None = None,
        page_size: int | None = None,
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
        user_id = require_positive_int(user_id, "user_id")
        today_ = today()
        yesterday = today_ - timedelta(days=1)
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            today_record = await self._get_checkin(db, user_id, today_)
            if today_record is not None:
                return checkin_payload(
                    today_record,
                    target_date=today_,
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
            return checkin_payload(
                None,
                target_date=today_,
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
        user_id = require_positive_int(user_id, "user_id")
        completed = read_field(data, "questions_completed", questions_completed)
        completed = 0 if completed is None else completed
        if not isinstance(completed, int) or completed < 0:
            raise ValidationException("questions_completed 必须为非负整数")

        today_ = today()
        yesterday = today_ - timedelta(days=1)
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            today_record = await self._get_checkin(db, user_id, today_)
            if today_record is not None:
                return checkin_payload(
                    today_record,
                    target_date=today_,
                    checked_in=True,
                    consecutive_days=today_record.consecutive_days,
                )

            yesterday_record = await self._get_checkin(db, user_id, yesterday)
            consecutive_days = (yesterday_record.consecutive_days + 1) if yesterday_record else 1
            record = QuizCheckin(
                user_id=user_id,
                checkin_date=today_,
                questions_completed=completed,
                consecutive_days=consecutive_days,
            )
            db.add(record)
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                record = await self._get_checkin(db, user_id, today_)
                if record is None:
                    raise
            await db.refresh(record)
            return checkin_payload(
                record,
                target_date=today_,
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
        user_id = require_positive_int(user_id, "user_id")
        page, page_size = normalize_page(page, page_size)
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
                record_question_payload(record, question, include_correct_answer=False)
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
        user_id = require_positive_int(user_id, "user_id")
        question_id = require_positive_int(read_field(data, "question_id", question_id), "question_id")

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
            return record_question_payload(record, question, include_correct_answer=False)

    async def _unset_record_flag(
        self,
        *,
        user_id: int,
        record_id: int | None,
        question_id: int | None,
        flag_field: str,
    ) -> dict[str, Any]:
        user_id = require_positive_int(user_id, "user_id")
        if record_id is None and question_id is None:
            raise ValidationException("record_id 或 question_id 不能为空")

        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            stmt = select(QuizRecord, QuizQuestion).join(
                QuizQuestion,
                QuizQuestion.id == QuizRecord.question_id,
            )
            if record_id is not None:
                record_id = require_positive_int(record_id, "record_id")
                stmt = stmt.where(QuizRecord.user_id == user_id, QuizRecord.id == record_id)
            else:
                question_id = require_positive_int(question_id, "question_id")
                stmt = stmt.where(QuizRecord.user_id == user_id, QuizRecord.question_id == question_id)

            row = (await db.execute(stmt.limit(1))).first()
            if row is None:
                raise NotFoundException("答题记录")

            record, question = row
            setattr(record, flag_field, False)
            await db.commit()
            await db.refresh(record)
            return record_question_payload(record, question, include_correct_answer=False)

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
