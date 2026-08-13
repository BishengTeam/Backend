"""Deprecated pre-QB-13 quiz service kept only for source-level migrations.

No API router imports this module. New callers must use ``QuizPracticeService``
or ``QuizExamService``; the old single-question, manual wrong-book/check-in,
and single ``exam`` endpoints were removed in QB-31. This file is retained
temporarily because downstream migration scripts and historical tests may
still import its symbols, but it is not part of the runtime contract.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import Integer, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapter.database import get_db_ctx
from app.port.exceptions import NotFoundException, ValidationException
from app.domain.community.src.index import (
    QuizCategory,
    QuizCheckin,
    QuizCollection,
    QuizExam,
    QuizPracticeAttempt,
    QuizPracticeSessionQuestion,
    QuizPracticeSession,
    QuizQuestion,
    QuizWrongItem,
)
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
            now = datetime.now(timezone.utc)

            session = QuizPracticeSession(
                user_id=user_id, mode='normal', category_id=question.category_id,
                requested_count=1, actual_count=1, status='completed',
                started_at=now, completed_at=now,
            )
            db.add(session)
            await db.flush()

            sq = QuizPracticeSessionQuestion(
                session_id=session.id, question_id=question_id, position=1,
                category_id=question.category_id,
                category_path=[{"id": question.category_id, "name": ""}],
                question_type=question.question_type,
                question_text=question.question_text,
                options=question.options or {},
                correct_answer=question.correct_answer,
                explanation=question.explanation or "",
                question_lock_version=question.lock_version,
            )
            db.add(sq)
            await db.flush()

            attempt = QuizPracticeAttempt(
                user_id=user_id, session_id=session.id, session_question_id=sq.id,
                idempotency_key=f"{user_id}_{question_id}_{int(now.timestamp())}",
                attempt_no=1, is_first_attempt=True,
                user_answer=answer_for_storage, is_correct=is_correct,
                submitted_at=now,
            )
            db.add(attempt)

            if not is_correct:
                await self._sync_wrong_item(db, user_id, question_id, question, now)

            await db.commit()
            return {
                **question_payload(question, include_correct_answer=True),
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
        user_id = require_positive_int(user_id, "user_id")
        page, page_size = normalize_page(page, page_size)
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            base = (
                select(QuizWrongItem, QuizQuestion)
                .join(QuizQuestion, QuizWrongItem.question_id == QuizQuestion.id)
                .where(QuizWrongItem.user_id == user_id, QuizWrongItem.status == 'active')
            )
            total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
            rows = (await db.execute(base.order_by(QuizWrongItem.latest_wrong_at.desc()).offset((page - 1) * page_size).limit(page_size))).all()
        return PaginatedData[dict[str, Any]](
            items=[question_payload(q, include_correct_answer=False) for _, q in rows],
            total=total, page=page, page_size=page_size,
        )

    async def add_wrong_question(
        self,
        user_id: int,
        data: QuizToggleRequest | None = None,
        *,
        question_id: int | None = None,
    ) -> dict[str, Any]:
        question_id = require_positive_int(read_field(data, "question_id", question_id), "question_id")
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            question = await self._get_question(db, question_id)
            now = datetime.now(timezone.utc)
            existing = (await db.execute(
                select(QuizWrongItem).where(QuizWrongItem.user_id == user_id, QuizWrongItem.question_id == question_id)
            )).scalar_one_or_none()
            if existing:
                existing.status = 'active'
                existing.latest_wrong_at = now
                existing.cleared_at = None
                existing.latest_wrong_snapshot = question_payload(question, include_correct_answer=True)
            else:
                db.add(QuizWrongItem(user_id=user_id, question_id=question_id, first_wrong_at=now,
                                      latest_wrong_at=now, latest_wrong_snapshot=question_payload(question, include_correct_answer=True)))
            await db.commit()
            return question_payload(question, include_correct_answer=False)

    async def remove_wrong_question(
        self,
        user_id: int,
        record_id: int | None = None,
        *,
        question_id: int | None = None,
    ) -> dict[str, Any]:
        question_id = require_positive_int(question_id, "question_id")
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            item = (await db.execute(
                select(QuizWrongItem).where(QuizWrongItem.user_id == user_id, QuizWrongItem.question_id == question_id, QuizWrongItem.status == 'active')
            )).scalar_one_or_none()
            if item is None:
                raise NotFoundException("错题记录")
            item.status = 'cleared'
            item.cleared_at = datetime.now(timezone.utc)
            await db.commit()
            return {"question_id": item.question_id, "is_wrong": False}

    async def list_collections(
        self,
        user_id: int,
        *,
        page: int | None = None,
        page_size: int | None = None,
    ) -> PaginatedData[dict[str, Any]]:
        user_id = require_positive_int(user_id, "user_id")
        page, page_size = normalize_page(page, page_size)
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            base = (
                select(QuizCollection, QuizQuestion)
                .join(QuizQuestion, QuizCollection.question_id == QuizQuestion.id)
                .where(QuizCollection.user_id == user_id, QuizCollection.is_active == True)
            )
            total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
            rows = (await db.execute(base.order_by(QuizCollection.collected_at.desc()).offset((page - 1) * page_size).limit(page_size))).all()
        return PaginatedData[dict[str, Any]](
            items=[question_payload(q, include_correct_answer=False) for _, q in rows],
            total=total, page=page, page_size=page_size,
        )

    async def add_collection(
        self,
        user_id: int,
        data: QuizToggleRequest | None = None,
        *,
        question_id: int | None = None,
    ) -> dict[str, Any]:
        question_id = require_positive_int(read_field(data, "question_id", question_id), "question_id")
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            question = await self._get_question(db, question_id)
            now = datetime.now(timezone.utc)
            existing = (await db.execute(
                select(QuizCollection).where(QuizCollection.user_id == user_id, QuizCollection.question_id == question_id)
            )).scalar_one_or_none()
            if existing:
                existing.is_active = True
                existing.collected_at = now
                existing.removed_at = None
            else:
                db.add(QuizCollection(user_id=user_id, question_id=question_id, collected_at=now))
            await db.commit()
            return question_payload(question, include_correct_answer=False)

    async def remove_collection(
        self,
        user_id: int,
        record_id: int | None = None,
        *,
        question_id: int | None = None,
    ) -> dict[str, Any]:
        question_id = require_positive_int(question_id, "question_id")
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            item = (await db.execute(
                select(QuizCollection).where(QuizCollection.user_id == user_id, QuizCollection.question_id == question_id, QuizCollection.is_active == True)
            )).scalar_one_or_none()
            if item is None:
                raise NotFoundException("收藏记录")
            item.is_active = False
            item.removed_at = datetime.now(timezone.utc)
            await db.commit()
            return {"question_id": item.question_id, "is_collected": False}

    async def get_checkin_status(self, user_id: int) -> dict[str, Any]:
        user_id = require_positive_int(user_id, "user_id")
        today_ = today()
        yesterday = today_ - timedelta(days=1)
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            today_record = await self._get_checkin(db, user_id, today_)

            if today_record is not None:
                # 自动统计当天实际答题数量，同步更新 questions_completed 为最新值
                completed = (
                    await db.execute(
                        select(func.count()).select_from(QuizPracticeAttempt).where(
                            QuizPracticeAttempt.user_id == user_id,
                            func.date(QuizPracticeAttempt.created_at) == today_,
                        )
                    )
                ).scalar() or 0
                if today_record.questions_completed != completed:
                    today_record.questions_completed = completed
                    await db.commit()
                    await db.refresh(today_record)
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

        today_ = today()
        yesterday = today_ - timedelta(days=1)
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            today_record = await self._get_checkin(db, user_id, today_)

            # 自动统计当天实际答题数量（已签到和新建都需要）
            completed = (
                await db.execute(
                    select(func.count()).select_from(QuizPracticeAttempt).where(
                        QuizPracticeAttempt.user_id == user_id,
                        func.date(QuizPracticeAttempt.created_at) == today_,
                    )
                )
            ).scalar() or 0

            if today_record is not None:
                # 已签到时同步更新 questions_completed 为最新值
                if today_record.questions_completed != completed:
                    today_record.questions_completed = completed
                    await db.commit()
                    await db.refresh(today_record)
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

    async def get_checkin_calendar(self, user_id: int, days: int = 30) -> list[dict[str, Any]]:
        """获取近 N 天的签到日历记录（含已签到和未签到的每一天）。"""
        user_id = require_positive_int(user_id, "user_id")
        if days < 1:
            days = 30
        start_date = today() - timedelta(days=days - 1)
        end_date = today()

        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            records = (
                await db.execute(
                    select(QuizCheckin)
                    .where(
                        QuizCheckin.user_id == user_id,
                        QuizCheckin.checkin_date >= start_date,
                    )
                    .order_by(QuizCheckin.checkin_date.asc())
                )
            ).scalars().all()

            # 查询今日实时答题数量（用于覆盖可能过时的 questions_completed）
            today_count = (
                await db.execute(
                    select(func.count()).select_from(QuizPracticeAttempt).where(
                        QuizPracticeAttempt.user_id == user_id,
                        func.date(QuizPracticeAttempt.created_at) == end_date,
                    )
                )
            ).scalar() or 0

        records_by_date = {r.checkin_date: r for r in records}
        result: list[dict[str, Any]] = []
        current = start_date
        while current <= end_date:
            record = records_by_date.get(current)
            if record is not None:
                result.append(checkin_payload(
                    record, target_date=current,
                    checked_in=True, consecutive_days=record.consecutive_days,
                ))
            else:
                result.append(checkin_payload(
                    None, target_date=current,
                    checked_in=False, consecutive_days=0,
                ))
            current += timedelta(days=1)

        # 今日已签到记录使用实时答题数量
        today_entry = result[-1] if result else None
        if today_entry and today_entry.get("checked_in"):
            today_entry["questions_completed"] = today_count

        return result

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

    async def _sync_wrong_item(self, db: AsyncSession, user_id: int, question_id: int, question: QuizQuestion, now: datetime) -> None:
        existing = (await db.execute(
            select(QuizWrongItem).where(QuizWrongItem.user_id == user_id, QuizWrongItem.question_id == question_id)
        )).scalar_one_or_none()
        if existing:
            existing.status = 'active'
            existing.latest_wrong_at = now
            existing.cleared_at = None
            existing.latest_wrong_snapshot = question_payload(question, include_correct_answer=True)
        else:
            db.add(QuizWrongItem(user_id=user_id, question_id=question_id, first_wrong_at=now,
                                  latest_wrong_at=now, latest_wrong_snapshot=question_payload(question, include_correct_answer=True)))

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
            total_answers = (await db.execute(select(func.count()).select_from(QuizPracticeAttempt).where(QuizPracticeAttempt.user_id == user_id))).scalar() or 0
            correct_answers = (await db.execute(select(func.count()).select_from(QuizPracticeAttempt).where(QuizPracticeAttempt.user_id == user_id, QuizPracticeAttempt.is_correct == True))).scalar() or 0
            answered_questions = (await db.execute(
                select(func.count(func.distinct(QuizPracticeSessionQuestion.question_id)))
                .select_from(QuizPracticeAttempt)
                .join(QuizPracticeSessionQuestion, QuizPracticeAttempt.session_question_id == QuizPracticeSessionQuestion.id)
                .where(QuizPracticeAttempt.user_id == user_id)
            )).scalar() or 0
            total_questions = (await db.execute(select(func.count()).select_from(QuizQuestion))).scalar() or 0
            wrong_count = (await db.execute(select(func.count()).select_from(QuizWrongItem).where(QuizWrongItem.user_id == user_id, QuizWrongItem.status == 'active'))).scalar() or 0
            collected_count = (await db.execute(select(func.count()).select_from(QuizCollection).where(QuizCollection.user_id == user_id, QuizCollection.is_active == True))).scalar() or 0
            today_row = (await db.execute(select(func.count(), func.sum(func.cast(QuizPracticeAttempt.is_correct, Integer))).where(QuizPracticeAttempt.user_id == user_id, func.date(QuizPracticeAttempt.submitted_at) == today_date))).first()
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
                if not is_correct:
                    await self._sync_wrong_item(db, user_id, a.question_id, q, datetime.now(timezone.utc))
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
                answered = (await db.execute(
                    select(func.count(func.distinct(QuizPracticeSessionQuestion.question_id)))
                    .select_from(QuizPracticeAttempt)
                    .join(QuizPracticeSessionQuestion, QuizPracticeAttempt.session_question_id == QuizPracticeSessionQuestion.id)
                    .where(QuizPracticeAttempt.user_id == user_id, QuizPracticeSessionQuestion.question_id.in_(select(QuizQuestion.id).where(QuizQuestion.category_id.in_(sub_ids))))
                )).scalar() or 0
                correct = (await db.execute(
                    select(func.count(func.distinct(QuizPracticeSessionQuestion.question_id)))
                    .select_from(QuizPracticeAttempt)
                    .join(QuizPracticeSessionQuestion, QuizPracticeAttempt.session_question_id == QuizPracticeSessionQuestion.id)
                    .where(QuizPracticeAttempt.user_id == user_id, QuizPracticeAttempt.is_correct == True, QuizPracticeSessionQuestion.question_id.in_(select(QuizQuestion.id).where(QuizQuestion.category_id.in_(sub_ids))))
                )).scalar() or 0
                results.append({"category_id": p.id, "category_name": p.name, "total": total, "answered": answered, "correct": correct, "accuracy": round(correct / answered * 100, 1) if answered > 0 else 0.0})
            return results

    # ── 近期记录 ──
    async def get_recent(self, user_id: int, limit: int = 10) -> list[dict[str, Any]]:
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            rows = (await db.execute(
                select(QuizPracticeAttempt, QuizQuestion)
                .join(QuizPracticeSessionQuestion, QuizPracticeAttempt.session_question_id == QuizPracticeSessionQuestion.id)
                .join(QuizQuestion, QuizPracticeSessionQuestion.question_id == QuizQuestion.id)
                .where(QuizPracticeAttempt.user_id == user_id)
                .order_by(QuizPracticeAttempt.updated_at.desc())
                .limit(limit)
            )).all()
            return [{"id": r.id, "question_id": q.id, "question_text": q.question_text, "question_type": q.question_type, "user_answer": r.user_answer, "is_correct": r.is_correct, "updated_at": r.updated_at.isoformat() if r.updated_at else None} for r, q in rows]
