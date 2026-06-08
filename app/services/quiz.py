from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import Integer, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapter.database import get_db_ctx
from app.port.exceptions import NotFoundException, ValidationException
from app.domain.community.src.index import QuizCategory, QuizCheckin, QuizExam, QuizQuestion, QuizRecord
from app.domain.user.src.index import User
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

            rows = (await db.execute(
                select(QuizQuestion.category_id, func.count()).group_by(QuizQuestion.category_id)
            )).all()
            direct = defaultdict(int, rows)
            cat_question_count: dict[int, int] = {}
            for c in categories: cat_question_count[c.id] = direct.get(c.id, 0)
            for c in categories:
                if c.parent_id and c.parent_id in cat_question_count:
                    cat_question_count[c.parent_id] += cat_question_count[c.id]

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
                "question_count": cat_question_count.get(category.id, 0),
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
        ).scalar_one_or_none()    # ── 练习统计 ──
    async def get_stats(self, user_id: int) -> dict[str, Any]:
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            today_date = today()
            total_answers = (await db.execute(select(func.count()).select_from(QuizRecord).where(QuizRecord.user_id == user_id))).scalar() or 0
            correct_answers = (await db.execute(select(func.count()).select_from(QuizRecord).where(QuizRecord.user_id == user_id, QuizRecord.is_correct == True))).scalar() or 0
            answered_questions = (await db.execute(select(func.count(func.distinct(QuizRecord.question_id))).where(QuizRecord.user_id == user_id))).scalar() or 0
            total_questions = (await db.execute(select(func.count()).select_from(QuizQuestion))).scalar() or 0
            wrong_count = (await db.execute(select(func.count()).select_from(QuizRecord).where(QuizRecord.user_id == user_id, QuizRecord.is_wrong == True))).scalar() or 0
            collected_count = (await db.execute(select(func.count()).select_from(QuizRecord).where(QuizRecord.user_id == user_id, QuizRecord.is_collected == True))).scalar() or 0
            today_row = (await db.execute(select(func.count(), func.sum(func.cast(QuizRecord.is_correct, Integer))).where(QuizRecord.user_id == user_id, func.date(QuizRecord.updated_at) == today_date))).first()
            today_answers = today_row[0] if today_row else 0
            today_correct = today_row[1] if today_row and today_row[1] else 0
            checkin_status = await self.get_checkin_status(user_id)
            streak_days = checkin_status.get("consecutive_days", 0)
            total_checkin_days = (await db.execute(select(func.count()).select_from(QuizCheckin).where(QuizCheckin.user_id == user_id))).scalar() or 0
        return {"total_answers": total_answers, "correct_answers": correct_answers, "accuracy": round(correct_answers / total_answers * 100, 1) if total_answers > 0 else 0.0, "total_questions": total_questions, "answered_questions": answered_questions, "completion_rate": round(answered_questions / total_questions * 100, 1) if total_questions > 0 else 0.0, "streak_days": streak_days, "total_checkin_days": total_checkin_days, "wrong_count": wrong_count, "collected_count": collected_count, "today_answers": today_answers, "today_correct": today_correct}

    # ── 模拟考试 ──
    async def start_exam(self, user_id: int, body) -> dict[str, Any]:
        import random
        from datetime import datetime as dt, timezone as tz
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            base = select(QuizQuestion.id)
            if body.category_id is not None: base = base.where(QuizQuestion.category_id == body.category_id)
            if body.question_type is not None: base = base.where(QuizQuestion.question_type == body.question_type)
            rows = (await db.execute(base)).all()
            ids = [r[0] for r in rows]
            if len(ids) < body.question_count: raise ValidationException(f"题库仅 {len(ids)} 题")
            sampled = random.sample(ids, body.question_count)
            now = dt.now(tz.utc)
            exam = QuizExam(user_id=user_id, question_ids=sampled, total=body.question_count, duration_seconds=body.duration_minutes * 60, started_at=now)
            db.add(exam); await db.commit(); await db.refresh(exam)
            questions = (await db.execute(select(QuizQuestion).where(QuizQuestion.id.in_(sampled)))).scalars().all()
        return {"exam_id": exam.id, "questions": [question_payload(q) for q in questions], "total": body.question_count, "duration_seconds": body.duration_minutes * 60, "started_at": now.isoformat()}

    async def submit_exam(self, user_id: int, body) -> dict[str, Any]:
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            exam = await db.get(QuizExam, body.exam_id)
            if exam is None or exam.user_id != user_id: raise NotFoundException("考试记录")
            if exam.status != "in_progress": raise ValidationException("该考试已提交")
            questions = (await db.execute(select(QuizQuestion).where(QuizQuestion.id.in_(exam.question_ids)))).scalars().all()
            q_map = {q.id: q for q in questions}
            answer_dict = {str(a.question_id): a.user_answer for a in body.answers}
            details = []; correct = wrong = 0
            for qid in exam.question_ids:
                q = q_map.get(qid)
                if q is None: continue
                ua = answer_dict.get(str(qid), "")
                if not ua: continue
                is_correct = normalize_answer(ua, q.question_type) == normalize_answer(q.correct_answer, q.question_type)
                if is_correct: correct += 1
                else: wrong += 1
                details.append({"record_id": 0, "question_id": qid, "user_answer": ua, "is_correct": is_correct, "is_wrong": not is_correct, "correct_answer": q.correct_answer, "explanation": q.explanation})
            for a in body.answers:
                q = q_map.get(a.question_id)
                if q is None: continue
                is_correct = normalize_answer(a.user_answer, q.question_type) == normalize_answer(q.correct_answer, q.question_type)
                await self._upsert_record(db, user_id=user_id, question_id=a.question_id, values={"user_answer": a.user_answer, "is_correct": is_correct, "is_wrong": not is_correct})
            total = correct + wrong
            exam.answers = answer_dict; exam.correct_count = correct; exam.wrong_count = wrong
            exam.score = round(correct / exam.total * 100, 1) if exam.total > 0 else 0
            exam.elapsed_seconds = body.elapsed_seconds; exam.submitted_at = datetime.now(timezone.utc); exam.status = "completed"
            await db.commit()
        return {"exam_id": exam.id, "total": exam.total, "correct_count": correct, "wrong_count": wrong, "unanswered_count": exam.total - len(body.answers), "score": exam.score, "accuracy": round(correct / total * 100, 1) if total > 0 else 0, "elapsed_seconds": body.elapsed_seconds, "details": details}

    async def list_exams(self, user_id: int, page: int, page_size: int) -> PaginatedData[dict[str, Any]]:
        page, page_size = normalize_page(page, page_size)
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            base = select(QuizExam).where(QuizExam.user_id == user_id, QuizExam.status != "in_progress")
            total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
            rows = (await db.execute(base.order_by(QuizExam.id.desc()).offset((page - 1) * page_size).limit(page_size))).scalars().all()
        return PaginatedData[dict[str, Any]](items=[{"id": e.id, "total": e.total, "correct_count": e.correct_count, "score": e.score, "elapsed_seconds": e.elapsed_seconds, "duration_seconds": e.duration_seconds, "started_at": e.started_at.isoformat() if e.started_at else "", "status": e.status} for e in rows], total=total, page=page, page_size=page_size)

    async def get_exam(self, user_id: int, exam_id: int) -> dict[str, Any]:
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            exam = await db.get(QuizExam, exam_id)
            if exam is None or exam.user_id != user_id: raise NotFoundException("考试记录")
            questions = (await db.execute(select(QuizQuestion).where(QuizQuestion.id.in_(exam.question_ids)))).scalars().all()
            q_map = {q.id: q for q in questions}
            answers = exam.answers or {}
            details = []; correct = 0
            for qid in exam.question_ids:
                q = q_map.get(qid)
                if q is None: continue
                ua = answers.get(str(qid), "")
                is_correct = False
                if ua: is_correct = normalize_answer(ua, q.question_type) == normalize_answer(q.correct_answer, q.question_type)
                if is_correct: correct += 1
                details.append({"record_id": 0, "question_id": qid, "user_answer": ua, "is_correct": is_correct, "is_wrong": bool(ua) and not is_correct, "correct_answer": q.correct_answer, "explanation": q.explanation})
        total = len(exam.question_ids)
        return {"id": exam.id, "total": exam.total, "correct_count": correct, "wrong_count": total - correct, "score": round(correct / total * 100, 1) if total > 0 else 0, "accuracy": round(correct / total * 100, 1) if total > 0 else 0, "elapsed_seconds": exam.elapsed_seconds, "duration_seconds": exam.duration_seconds, "started_at": exam.started_at.isoformat() if exam.started_at else "", "submitted_at": exam.submitted_at.isoformat() if exam.submitted_at else None, "status": exam.status, "details": details}

    async def get_current_exam(self, user_id: int) -> dict[str, Any] | None:
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            exam = (await db.execute(select(QuizExam).where(QuizExam.user_id == user_id, QuizExam.status == "in_progress").order_by(QuizExam.id.desc()).limit(1))).scalar_one_or_none()
            if exam is None: return None
            questions = (await db.execute(select(QuizQuestion).where(QuizQuestion.id.in_(exam.question_ids)))).scalars().all()
        return {"exam_id": exam.id, "questions": [question_payload(q) for q in questions], "answers": exam.answers or {}, "total": exam.total, "duration_seconds": exam.duration_seconds, "started_at": exam.started_at.isoformat() if exam.started_at else ""}

    # ── 分类进度 ──
    async def get_progress(self, user_id: int, category_id: int | None = None) -> list[dict[str, Any]]:
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            base = select(QuizCategory).where(QuizCategory.parent_id.is_(None))
            if category_id is not None: base = base.where(QuizCategory.id == category_id)
            parents = (await db.execute(base)).scalars().all()
            results = []
            for p in parents:
                sub_cats = (await db.execute(select(QuizCategory).where(QuizCategory.parent_id == p.id))).scalars().all()
                sub_ids = [p.id] + [c.id for c in sub_cats]
                total = (await db.execute(select(func.count()).select_from(QuizQuestion).where(QuizQuestion.category_id.in_(sub_ids)))).scalar() or 0
                answered = (await db.execute(select(func.count(func.distinct(QuizRecord.question_id))).where(QuizRecord.user_id == user_id, QuizRecord.question_id.in_(select(QuizQuestion.id).where(QuizQuestion.category_id.in_(sub_ids)))))).scalar() or 0
                correct = (await db.execute(select(func.count()).where(QuizRecord.user_id == user_id, QuizRecord.is_correct == True, QuizRecord.question_id.in_(select(QuizQuestion.id).where(QuizQuestion.category_id.in_(sub_ids)))))).scalar() or 0
                results.append({"category_id": p.id, "category_name": p.name, "total": total, "answered": answered, "correct": correct, "accuracy": round(correct / answered * 100, 1) if answered > 0 else 0.0})
            return results

    # ── 近期记录 ──
    async def get_recent(self, user_id: int, limit: int = 10) -> list[dict[str, Any]]:
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            rows = (await db.execute(select(QuizRecord, QuizQuestion).join(QuizQuestion, QuizRecord.question_id == QuizQuestion.id).where(QuizRecord.user_id == user_id).order_by(QuizRecord.updated_at.desc()).limit(limit))).all()
            return [{"id": r.id, "question_id": q.id, "question_text": q.question_text, "question_type": q.question_type, "user_answer": r.user_answer, "is_correct": r.is_correct, "updated_at": r.updated_at.isoformat() if r.updated_at else None} for r, q in rows]
