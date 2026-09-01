"""User-facing quiz browsing and practice workflows for the frozen contract.

This service uses only the rebuilt quiz tables and keeps all practice side
effects in the same transaction as the immutable attempt.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, func, or_, select, union
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from app.adapter.database import get_db_ctx
from app.services.quiz_image_upload import sign_quiz_media_map, sign_quiz_media_urls
from app.domain.community.src.index import (
    QuizCategory,
    QuizCheckin,
    QuizCollection,
    QuizExam,
    QuizExamAnswer,
    QuizExamQuestion,
    QuizPracticeAttempt,
    QuizPracticeSession,
    QuizPracticeSessionQuestion,
    QuizQuestion,
    QuizKnowledgePoint,
    QuizLibrary,
    QuizLibraryEntitlement,
    QuizModule,
    QuizQuestionRevisionStats,
    QuizUserStats,
    QuizWrongItem,
)
from app.domain.community.src.rule.quiz import (
    QuizCategoryStatus,
    QuizQuestionStatus,
    QuizQuestionType,
    QuizPracticeMode,
    QuizPracticeSessionStatus,
    QuizWrongStatus,
    answers_match,
    normalize_submitted_answer,
)
from app.domain.user.src.index import User
from app.port.config import settings
from app.port.exceptions import (
    BusinessException,
    ConflictException,
    NotFoundException,
    ValidationException,
)
from app.schemas.common import PaginatedData
from app.schemas.quiz_contract import (
    QuizCategoryNode,
    QuizCategoryPathItem,
    QuizCheckinCalendarQuery,
    QuizCheckinDay,
    QuizCheckinStatusResponse,
    QuizCollectionCreate,
    QuizCollectionItem,
    QuizCollectionMutationResponse,
    QuizExamStats,
    QuizPracticeAbandonResponse,
    QuizPracticeAnswerSave,
    QuizPracticeAnswerSaved,
    QuizPracticeAttemptCreate,
    QuizPracticeAttemptResult,
    QuizPracticeHistoryItem,
    QuizPracticeHistoryQuery,
    QuizPracticeQuestionState,
    QuizPracticeSessionCreate,
    QuizPracticeSessionResponse,
    QuizPracticeStats,
    QuizPublicQuestion,
    QuizQuestionListQuery,
    QuizStatsResponse,
    QuizStatsQuery,
    QuizWrongBookItem,
    QuizWrongBookQuery,
)


_ACTIVE = QuizCategoryStatus.ACTIVE.value
_PUBLISHED = QuizQuestionStatus.PUBLISHED.value
_PRACTICE_IN_PROGRESS = QuizPracticeSessionStatus.IN_PROGRESS.value
_PRACTICE_COMPLETED = QuizPracticeSessionStatus.COMPLETED.value
_PRACTICE_ABANDONED = QuizPracticeSessionStatus.ABANDONED.value
_WRONG_ACTIVE = QuizWrongStatus.ACTIVE.value
_WRONG_CLEARED = QuizWrongStatus.CLEARED.value
_WRONG_CLEAR_CORRECT_STREAK = 3
_SETTLED_EXAMS = ("completed", "timed_out")


class QuizPracticeService:
    """Implementation of QB-13 through QB-22.

    Every write method obtains the user row lock before any other row lock.
    This gives session creation, collection mutations and practice attempts a
    consistent lock order and prevents duplicate active sessions/stat rows.
    """

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _local_date(value: datetime | None = None) -> date:
        current = value or QuizPracticeService._now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(ZoneInfo(settings.APP_TIMEZONE)).date()

    @staticmethod
    def _utc_range(
        start: date | None,
        end: date | None,
    ) -> tuple[datetime | None, datetime | None]:
        zone = ZoneInfo(settings.APP_TIMEZONE)
        start_at = (
            datetime.combine(start, time.min, tzinfo=zone).astimezone(timezone.utc)
            if start is not None
            else None
        )
        end_at = (
            datetime.combine(
                end + timedelta(days=1),
                time.min,
                tzinfo=zone,
            ).astimezone(timezone.utc)
            if end is not None
            else None
        )
        return start_at, end_at

    @staticmethod
    async def _lock_user(db, user_id: int) -> User:
        result = await db.execute(
            select(User).where(User.id == user_id, User.is_active.is_(True)).with_for_update()
        )
        user = result.scalar_one_or_none()
        if user is None:
            raise NotFoundException("用户")
        return user

    @staticmethod
    async def _require_user(db, user_id: int) -> User:
        user = await db.get(User, user_id)
        if user is None or not user.is_active:
            raise NotFoundException("用户")
        return user

    @staticmethod
    async def _load_categories(db) -> list[QuizCategory]:
        result = await db.execute(
            select(QuizCategory).order_by(
                QuizCategory.parent_id.asc().nullsfirst(),
                QuizCategory.sort_order.asc(),
                QuizCategory.id.asc(),
            )
        )
        return list(result.scalars().all())

    @staticmethod
    def _category_maps(categories: list[QuizCategory]) -> tuple[
        dict[int, QuizCategory], dict[int | None, list[QuizCategory]]
    ]:
        by_id = {int(item.id): item for item in categories}
        children: dict[int | None, list[QuizCategory]] = defaultdict(list)
        for item in categories:
            children[item.parent_id].append(item)
        for values in children.values():
            values.sort(key=lambda item: (item.sort_order, item.id))
        return by_id, children

    @staticmethod
    def _is_effectively_active(category_id: int, by_id: dict[int, QuizCategory]) -> bool:
        current_id: int | None = category_id
        seen: set[int] = set()
        while current_id is not None:
            if current_id in seen:
                return False
            seen.add(current_id)
            category = by_id.get(current_id)
            if category is None or category.status != _ACTIVE:
                return False
            current_id = category.parent_id
        return True

    @classmethod
    def _effective_category_ids(
        cls,
        categories: list[QuizCategory],
        root_id: int | None = None,
    ) -> tuple[dict[int, QuizCategory], set[int]]:
        by_id, _ = cls._category_maps(categories)
        if root_id is not None and root_id not in by_id:
            raise NotFoundException("题库分类")
        if root_id is not None and not cls._is_effectively_active(root_id, by_id):
            raise BusinessException("题库分类或其祖先分类已停用")

        selected: set[int] = set()
        for category_id in by_id:
            if not cls._is_effectively_active(category_id, by_id):
                continue
            if root_id is None:
                selected.add(category_id)
                continue
            current_id: int | None = category_id
            seen: set[int] = set()
            while current_id is not None and current_id not in seen:
                if current_id == root_id:
                    selected.add(category_id)
                    break
                seen.add(current_id)
                parent = by_id.get(current_id)
                current_id = parent.parent_id if parent is not None else None
        return by_id, selected

    @classmethod
    def _category_subtree_ids(
        cls,
        categories: list[QuizCategory],
        root_id: int,
    ) -> set[int]:
        by_id, children = cls._category_maps(categories)
        if root_id not in by_id:
            raise NotFoundException("题库分类")
        selected: set[int] = set()
        queue = [root_id]
        while queue:
            current_id = queue.pop(0)
            if current_id in selected:
                continue
            selected.add(current_id)
            queue.extend(int(child.id) for child in children.get(current_id, []))
        return selected

    @staticmethod
    def _category_path(
        category_id: int,
        by_id: dict[int, QuizCategory],
    ) -> list[dict[str, object]]:
        path: list[dict[str, object]] = []
        current_id: int | None = category_id
        seen: set[int] = set()
        while current_id is not None and current_id not in seen:
            seen.add(current_id)
            category = by_id.get(current_id)
            if category is None:
                break
            path.append({"id": int(category.id), "name": category.name})
            current_id = category.parent_id
        path.reverse()
        return path

    @classmethod
    def _question_snapshot(
        cls,
        question: QuizQuestion,
        by_id: dict[int, QuizCategory],
    ) -> dict[str, object]:
        return {
            "question_id": int(question.id),
            "category_id": (
                int(question.category_id)
                if question.category_id is not None
                else None
            ),
            "category_path": (
                cls._category_path(question.category_id, by_id)
                if question.category_id is not None
                else []
            ),
            "question_type": str(question.question_type),
            "question_text": question.question_text,
            "options": dict(question.options or {}),
            "option_image_urls": dict(getattr(question, "option_image_urls", None) or {}),
            "correct_answer": question.correct_answer,
            "explanation": question.explanation or "",
            "image_urls": list(question.image_urls or []),
            "question_lock_version": int(question.lock_version),
        }

    @staticmethod
    def _public_question_from_snapshot(snapshot: dict[str, object]) -> QuizPublicQuestion:
        category_id = snapshot.get("category_id")
        return QuizPublicQuestion(
            id=int(snapshot["question_id"]),
            category_id=int(category_id) if category_id is not None else None,
            library_id=(
                int(snapshot["library_id"])
                if snapshot.get("library_id") is not None
                else None
            ),
            knowledge_point_id=(
                int(snapshot["knowledge_point_id"])
                if snapshot.get("knowledge_point_id") is not None
                else None
            ),
            question_revision_id=(
                int(snapshot["question_revision_id"])
                if snapshot.get("question_revision_id") is not None
                else None
            ),
            question_type=str(snapshot["question_type"]),
            question_text=str(snapshot["question_text"]),
            options=dict(snapshot.get("options") or {}),
            option_image_urls=sign_quiz_media_map(snapshot.get("option_image_urls") or {}),
            image_urls=sign_quiz_media_urls(list(snapshot.get("image_urls") or [])),
        )

    @staticmethod
    def _public_question(question: QuizQuestion) -> QuizPublicQuestion:
        return QuizPublicQuestion(
            id=int(question.id),
            category_id=(
                int(question.category_id) if question.category_id is not None else None
            ),
            library_id=(
                int(question.library_id) if question.library_id is not None else None
            ),
            knowledge_point_id=(
                int(question.knowledge_point_id)
                if question.knowledge_point_id is not None
                else None
            ),
            question_revision_id=(
                int(question.current_revision_id)
                if question.current_revision_id is not None
                else None
            ),
            question_type=str(question.question_type),
            question_text=question.question_text,
            options=dict(question.options or {}),
            option_image_urls=sign_quiz_media_map(getattr(question, "option_image_urls", None) or {}),
            image_urls=sign_quiz_media_urls(list(question.image_urls or [])),
        )

    @classmethod
    def _category_nodes(
        cls,
        categories: list[QuizCategory],
        direct_counts: dict[int, int],
    ) -> list[QuizCategoryNode]:
        by_id, children = cls._category_maps(categories)
        memo: dict[int, int] = {}
        visiting: set[int] = set()

        def total(category_id: int) -> int:
            if category_id in memo:
                return memo[category_id]
            if category_id in visiting:
                return 0
            visiting.add(category_id)
            value = direct_counts.get(category_id, 0)
            for child in children.get(category_id, []):
                if cls._is_effectively_active(child.id, by_id):
                    value += total(int(child.id))
            visiting.remove(category_id)
            memo[category_id] = value
            return value

        def build(category: QuizCategory) -> QuizCategoryNode | None:
            if not cls._is_effectively_active(category.id, by_id):
                return None
            count = total(int(category.id))
            if count <= 0:
                return None
            child_nodes = [
                node
                for child in children.get(category.id, [])
                if (node := build(child)) is not None
            ]
            return QuizCategoryNode(
                id=int(category.id),
                name=category.name,
                parent_id=category.parent_id,
                depth=int(category.depth),
                description=category.description,
                sort_order=int(category.sort_order),
                question_count=count,
                children=child_nodes,
            )

        roots = [
            item
            for item in categories
            if item.parent_id is None or item.parent_id not in by_id
        ]
        output: list[QuizCategoryNode] = []
        for root in roots:
            node = build(root)
            if node is not None:
                output.append(node)
        output.sort(key=lambda item: (item.sort_order, item.id))
        return output

    async def list_categories(self) -> list[QuizCategoryNode]:
        async with get_db_ctx() as db:
            categories = await self._load_categories(db)
            _, effective_ids = self._effective_category_ids(categories)
            if not effective_ids:
                return []
            result = await db.execute(
                select(QuizQuestion.category_id, func.count())
                .where(
                    QuizQuestion.status == _PUBLISHED,
                    QuizQuestion.category_id.in_(effective_ids),
                )
                .group_by(QuizQuestion.category_id)
            )
            direct_counts = {int(category_id): int(count) for category_id, count in result.all()}
            return self._category_nodes(categories, direct_counts)

    async def list_questions(
        self,
        user_id: int,
        query: QuizQuestionListQuery,
    ) -> PaginatedData[QuizPublicQuestion]:
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            categories = await self._load_categories(db)
            _, scope_ids = self._effective_category_ids(categories, query.category_id)
            if not scope_ids:
                return PaginatedData(items=[], total=0, page=query.page, page_size=query.page_size)
            stmt = select(QuizQuestion).where(
                QuizQuestion.status == _PUBLISHED,
                QuizQuestion.category_id.in_(scope_ids),
            )
            if query.question_type is not None:
                stmt = stmt.where(QuizQuestion.question_type == str(query.question_type))
            total = (
                await db.execute(select(func.count()).select_from(stmt.subquery()))
            ).scalar() or 0
            rows = (
                await db.execute(
                    stmt.order_by(QuizQuestion.id.asc())
                    .offset((query.page - 1) * query.page_size)
                    .limit(query.page_size)
                )
            ).scalars().all()
            items = [self._public_question(question) for question in rows]
            return PaginatedData(
                items=items,
                total=int(total),
                page=query.page,
                page_size=query.page_size,
            )

    @staticmethod
    async def _answered_question_subquery(db, user_id: int):
        practice = (
            select(QuizPracticeSessionQuestion.question_id.label("question_id"))
            .join(
                QuizPracticeAttempt,
                and_(
                    QuizPracticeAttempt.session_id == QuizPracticeSessionQuestion.session_id,
                    QuizPracticeAttempt.session_question_id == QuizPracticeSessionQuestion.id,
                ),
            )
            .where(QuizPracticeAttempt.user_id == user_id)
        )
        exam = (
            select(QuizExamQuestion.question_id.label("question_id"))
            .join(
                QuizExamAnswer,
                and_(
                    QuizExamAnswer.exam_id == QuizExamQuestion.exam_id,
                    QuizExamAnswer.exam_question_id == QuizExamQuestion.id,
                ),
            )
            .join(QuizExam, QuizExam.id == QuizExamQuestion.exam_id)
            .where(QuizExam.user_id == user_id, QuizExam.status.in_(_SETTLED_EXAMS))
        )
        return union(practice, exam).subquery("answered_questions")

    async def _select_normal_questions(
        self,
        db,
        user_id: int,
        scope_ids: set[int],
        requested_count: int,
    ) -> list[QuizQuestion]:
        answered = await self._answered_question_subquery(db, user_id)
        base = select(QuizQuestion).where(
            QuizQuestion.status == _PUBLISHED,
            QuizQuestion.category_id.in_(scope_ids),
        )
        unseen = list(
            (
                await db.execute(
                    base.where(~QuizQuestion.id.in_(select(answered.c.question_id)))
                    .order_by(func.random())
                    .limit(requested_count)
                )
            )
            .scalars()
            .all()
        )
        if len(unseen) >= requested_count:
            return unseen
        selected_ids = [question.id for question in unseen]
        remaining = requested_count - len(unseen)
        fallback = base.where(QuizQuestion.id.in_(select(answered.c.question_id)))
        if selected_ids:
            fallback = fallback.where(QuizQuestion.id.not_in(selected_ids))
        fallback_rows = list(
            (
                await db.execute(fallback.order_by(func.random()).limit(remaining))
            )
            .scalars()
            .all()
        )
        return unseen + fallback_rows

    async def _select_wrong_questions(
        self,
        db,
        user_id: int,
    ) -> list[tuple[QuizWrongItem, QuizQuestion]]:
        """Select the user's latest active wrong items regardless of hierarchy.

        Wrong-book items are user-scoped: every published question the user
        answered wrong should be selectable for the dedicated practice, no
        matter whether it belongs to a legacy category or a V2 library.
        """
        result = await db.execute(
            select(QuizWrongItem, QuizQuestion)
            .join(QuizQuestion, QuizQuestion.id == QuizWrongItem.question_id)
            .where(
                QuizWrongItem.user_id == user_id,
                QuizWrongItem.status == _WRONG_ACTIVE,
                QuizQuestion.status == _PUBLISHED,
            )
            .order_by(QuizWrongItem.latest_wrong_at.desc(), QuizWrongItem.id.desc())
            .limit(settings.QUIZ_WRONG_MAX_QUESTION_COUNT)
        )
        return list(result.all())

    async def create_session(
        self,
        user_id: int,
        data: QuizPracticeSessionCreate,
    ) -> QuizPracticeSessionResponse:
        async with get_db_ctx() as db:
            await self._lock_user(db, user_id)
            existing = (
                await db.execute(
                    select(QuizPracticeSession)
                    .where(
                        QuizPracticeSession.user_id == user_id,
                        QuizPracticeSession.status == _PRACTICE_IN_PROGRESS,
                    )
                    .order_by(QuizPracticeSession.started_at.desc(), QuizPracticeSession.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if existing is not None:
                return await self._serialize_session(db, existing)

            categories = await self._load_categories(db)
            by_id, scope_ids = self._effective_category_ids(
                categories,
                data.category_id if data.mode is QuizPracticeMode.NORMAL else None,
            )
            if data.mode is QuizPracticeMode.NORMAL:
                assert data.category_id is not None and data.question_count is not None
                requested_count = int(data.question_count)
                questions = await self._select_normal_questions(
                    db, user_id, scope_ids, requested_count
                )
            else:
                requested_count = settings.QUIZ_WRONG_MAX_QUESTION_COUNT
                wrong_rows = await self._select_wrong_questions(db, user_id)
                questions = [question for _, question in wrong_rows]
            if not questions:
                raise BusinessException("当前范围没有可用题目")

            now = self._now()
            session = QuizPracticeSession(
                user_id=user_id,
                mode=str(data.mode),
                category_id=data.category_id,
                requested_count=requested_count,
                actual_count=len(questions),
                status=_PRACTICE_IN_PROGRESS,
                started_at=now,
                lock_version=1,
            )
            db.add(session)
            await db.flush()
            for position, question in enumerate(questions, start=1):
                snapshot = self._question_snapshot(question, by_id)
                db.add(
                    QuizPracticeSessionQuestion(
                        session_id=session.id,
                        question_id=question.id,
                        position=position,
                        category_id=snapshot["category_id"],
                        category_path=snapshot["category_path"],
                        question_type=snapshot["question_type"],
                        question_text=snapshot["question_text"],
                        options=snapshot["options"],
                        option_image_urls=snapshot.get("option_image_urls") or {},
                        correct_answer=snapshot["correct_answer"],
                        explanation=snapshot["explanation"],
                        image_urls=snapshot["image_urls"],
                        question_lock_version=snapshot["question_lock_version"],
                    )
                )
            await self._ensure_stats(db, user_id)
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                # The partial unique index is the final race guard. Return the
                # winner's session instead of exposing a 500 to the client.
                winner = (
                    await db.execute(
                        select(QuizPracticeSession)
                        .where(
                            QuizPracticeSession.user_id == user_id,
                            QuizPracticeSession.status == _PRACTICE_IN_PROGRESS,
                        )
                        .order_by(QuizPracticeSession.id.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if winner is None:
                    raise
                return await self._serialize_session(db, winner)
            return await self._serialize_session(db, session)

    async def get_current_session(self, user_id: int) -> QuizPracticeSessionResponse | None:
        async with get_db_ctx() as db:
            session = (
                await db.execute(
                    select(QuizPracticeSession)
                    .where(
                        QuizPracticeSession.user_id == user_id,
                        QuizPracticeSession.status.in_(("in_progress", "paused")),
                    )
                    .order_by(QuizPracticeSession.started_at.desc(), QuizPracticeSession.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if session is None:
                return None
            return await self._serialize_session(db, session)

    async def get_session(self, user_id: int, session_id: int) -> QuizPracticeSessionResponse:
        async with get_db_ctx() as db:
            session = await self._get_session(db, user_id, session_id)
            return await self._serialize_session(db, session)

    async def _get_session(self, db, user_id: int, session_id: int, *, lock: bool = False):
        stmt = select(QuizPracticeSession).where(
            QuizPracticeSession.id == session_id,
            QuizPracticeSession.user_id == user_id,
        )
        if lock:
            stmt = stmt.with_for_update()
        session = (await db.execute(stmt)).scalar_one_or_none()
        if session is None:
            raise NotFoundException("练习会话")
        return session

    @classmethod
    async def _serialize_session(
        cls,
        db,
        session: QuizPracticeSession,
    ) -> QuizPracticeSessionResponse:
        if session.scope_type is not None:
            from app.services.quiz_v2 import QuizV2Service

            changed = await QuizV2Service()._sync_session_access(db, session)
            if session.status == "expired":
                await db.commit()
                raise QuizV2Service._session_expired()
            if session.status == "terminated":
                await db.commit()
                raise QuizV2Service._session_terminated()
            if changed:
                await db.commit()
            return await QuizV2Service()._serialize_practice_session(db, session)
        snapshots = list(
            (
                await db.execute(
                    select(QuizPracticeSessionQuestion)
                    .where(QuizPracticeSessionQuestion.session_id == session.id)
                    .order_by(QuizPracticeSessionQuestion.position.asc())
                )
            )
            .scalars()
            .all()
        )
        attempts = list(
            (
                await db.execute(
                    select(QuizPracticeAttempt)
                    .where(QuizPracticeAttempt.session_id == session.id)
                    .order_by(QuizPracticeAttempt.submitted_at.asc(), QuizPracticeAttempt.id.asc())
                )
            )
            .scalars()
            .all()
        )
        latest: dict[int, QuizPracticeAttempt] = {}
        counts: dict[int, int] = defaultdict(int)
        for attempt in attempts:
            counts[int(attempt.session_question_id)] += 1
            latest[int(attempt.session_question_id)] = attempt
        questions: list[QuizPracticeQuestionState] = []
        for snapshot in snapshots:
            attempt = latest.get(int(snapshot.id))
            user_answer = (
                attempt.user_answer if attempt is not None else snapshot.user_answer
            )
            result = None
            if attempt is not None:
                result = QuizPracticeAttemptResult(
                    attempt_id=int(attempt.id),
                    attempt_no=int(attempt.attempt_no),
                    user_answer=attempt.user_answer,
                    is_correct=bool(attempt.is_correct),
                    correct_answer=snapshot.correct_answer,
                    explanation=snapshot.explanation,
                    submitted_at=attempt.submitted_at,
                )
            questions.append(
                QuizPracticeQuestionState(
                    id=int(snapshot.question_id),
                    category_id=(
                        int(snapshot.category_id)
                        if snapshot.category_id is not None
                        else None
                    ),
                    question_type=snapshot.question_type,
                    question_text=snapshot.question_text,
                    options=dict(snapshot.options or {}),
                    option_image_urls=sign_quiz_media_map(getattr(snapshot, "option_image_urls", None) or {}),
                    image_urls=sign_quiz_media_urls(list(snapshot.image_urls or [])),
                    session_question_id=int(snapshot.id),
                    position=int(snapshot.position),
                    category_path=[
                        QuizCategoryPathItem.model_validate(item)
                        for item in snapshot.category_path
                    ],
                    # Essay questions are view-only in recite mode: reading
                    # the reference answer IS the completion, so they never
                    # block the current-question pointer or session progress.
                    answered=(
                        user_answer is not None
                        or snapshot.question_type == "essay"
                    ),
                    user_answer=user_answer,
                    answer_lock_version=int(snapshot.answer_lock_version),
                    correct_answer=(
                        snapshot.correct_answer
                        if session.status == _PRACTICE_COMPLETED
                        or snapshot.question_type == "essay"
                        else None
                    ),
                    explanation=(
                        snapshot.explanation
                        if session.status == _PRACTICE_COMPLETED
                        or snapshot.question_type == "essay"
                        else None
                    ),
                    is_correct=(
                        bool(attempt.is_correct)
                        if session.status == _PRACTICE_COMPLETED
                        and attempt is not None
                        else None
                    ),
                    attempt_count=counts.get(int(snapshot.id), 0),
                    latest_result=result,
                )
            )
        answered_count = sum(question.answered for question in questions)
        current = next((question for question in questions if not question.answered), None)
        return QuizPracticeSessionResponse(
            id=int(session.id),
            mode=session.mode,
            category_id=session.category_id,
            requested_count=int(session.requested_count),
            actual_count=int(session.actual_count),
            status=session.status,
            started_at=session.started_at,
            completed_at=session.completed_at,
            abandoned_at=session.abandoned_at,
            answered_count=answered_count,
            remaining_count=max(0, int(session.actual_count) - answered_count),
            current_position=(
                int(current.position)
                if current is not None and session.status == _PRACTICE_IN_PROGRESS
                else None
            ),
            lock_version=int(session.lock_version),
            questions=questions,
        )

    @classmethod
    def _attempt_result(
        cls,
        attempt: QuizPracticeAttempt,
        snapshot: QuizPracticeSessionQuestion,
    ) -> QuizPracticeAttemptResult:
        return QuizPracticeAttemptResult(
            attempt_id=int(attempt.id),
            attempt_no=int(attempt.attempt_no),
            user_answer=attempt.user_answer,
            is_correct=bool(attempt.is_correct),
            correct_answer=snapshot.correct_answer,
            explanation=snapshot.explanation,
            submitted_at=attempt.submitted_at,
        )

    @staticmethod
    def _grade_snapshot_answer(
        snapshot: QuizPracticeSessionQuestion,
        user_answer: object,
    ) -> tuple[str | list[str], bool]:
        try:
            canonical_answer = normalize_submitted_answer(
                snapshot.question_type,
                user_answer,
                options=snapshot.options,
            )
            correct = answers_match(
                snapshot.question_type,
                canonical_answer,
                snapshot.correct_answer,
                options=snapshot.options,
            )
        except ValueError as exc:
            raise ValidationException(str(exc)) from exc
        return canonical_answer, correct

    async def _record_attempt_locked(
        self,
        db,
        *,
        user_id: int,
        session: QuizPracticeSession,
        snapshot: QuizPracticeSessionQuestion,
        user_answer: object,
        idempotency_key: str,
        now: datetime,
        stats: QuizUserStats | None = None,
    ) -> QuizPracticeAttempt:
        """Create one immutable graded attempt inside the caller transaction."""

        canonical_answer, correct = self._grade_snapshot_answer(
            snapshot,
            user_answer,
        )
        attempt_no = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(QuizPracticeAttempt)
                    .where(QuizPracticeAttempt.session_question_id == snapshot.id)
                )
            ).scalar()
            or 0
        ) + 1
        attempt = QuizPracticeAttempt(
            user_id=user_id,
            session_id=session.id,
            session_question_id=snapshot.id,
            idempotency_key=idempotency_key,
            attempt_no=attempt_no,
            is_first_attempt=attempt_no == 1,
            user_answer=canonical_answer,
            is_correct=correct,
            submitted_at=now,
        )
        db.add(attempt)
        await db.flush()
        active_stats = stats or await self._ensure_stats(db, user_id)
        await self._apply_attempt_stats(
            db,
            active_stats,
            session,
            snapshot,
            attempt,
            correct,
            now,
        )
        if attempt.is_first_attempt and snapshot.question_revision_id is not None:
            statement = pg_insert(QuizQuestionRevisionStats).values(
                question_id=snapshot.question_id,
                question_revision_id=snapshot.question_revision_id,
                practice_first_attempts=1,
                practice_first_correct=1 if correct else 0,
                exam_answers=0,
                exam_correct=0,
                aggregated_through=now,
            )
            await db.execute(
                statement.on_conflict_do_update(
                    index_elements=[QuizQuestionRevisionStats.question_revision_id],
                    set_={
                        "practice_first_attempts": QuizQuestionRevisionStats.practice_first_attempts
                        + 1,
                        "practice_first_correct": QuizQuestionRevisionStats.practice_first_correct
                        + (1 if correct else 0),
                        "aggregated_through": now,
                    },
                )
            )
        if attempt.is_first_attempt:
            await self._apply_wrong_book(
                db,
                user_id,
                session,
                snapshot,
                correct,
                now,
                active_stats,
            )
            if session.mode == QuizPracticeMode.WRONG_ONLY.value:
                reviewed = (
                    await db.execute(
                        select(QuizWrongItem)
                        .where(
                            QuizWrongItem.user_id == user_id,
                            QuizWrongItem.question_id == snapshot.question_id,
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if reviewed is not None:
                    reviewed.review_count += 1
                    reviewed.last_reviewed_at = now
        return attempt

    async def submit_attempt(
        self,
        user_id: int,
        session_id: int,
        data: QuizPracticeAttemptCreate,
    ) -> QuizPracticeAttemptResult:
        async with get_db_ctx() as db:
            await self._lock_user(db, user_id)
            session = await self._get_session(db, user_id, session_id, lock=True)
            existing = (
                await db.execute(
                    select(QuizPracticeAttempt)
                    .where(
                        QuizPracticeAttempt.user_id == user_id,
                        QuizPracticeAttempt.session_id == session_id,
                        QuizPracticeAttempt.idempotency_key == data.idempotency_key,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if existing is not None:
                snapshot = await db.get(QuizPracticeSessionQuestion, existing.session_question_id)
                if snapshot is None:
                    raise NotFoundException("练习题目")
                if int(existing.session_question_id) != data.session_question_id:
                    raise ConflictException("幂等键已用于其他题目")
                canonical_answer, _ = self._grade_snapshot_answer(
                    snapshot,
                    data.user_answer,
                )
                if canonical_answer != existing.user_answer:
                    raise ConflictException("幂等键已用于不同答案")
                return self._attempt_result(existing, snapshot)
            if session.scope_type is not None:
                from app.services.quiz_v2 import QuizV2Service

                await QuizV2Service()._require_usable_session(db, session)
            if session.status != _PRACTICE_IN_PROGRESS:
                raise ConflictException("当前练习会话已结束，不能继续作答")

            snapshot = (
                await db.execute(
                    select(QuizPracticeSessionQuestion)
                    .where(
                        QuizPracticeSessionQuestion.id == data.session_question_id,
                        QuizPracticeSessionQuestion.session_id == session_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if snapshot is None:
                raise NotFoundException("练习题目")
            now = self._now()
            attempt = await self._record_attempt_locked(
                db,
                user_id=user_id,
                session=session,
                snapshot=snapshot,
                user_answer=data.user_answer,
                idempotency_key=data.idempotency_key,
                now=now,
            )
            if attempt.is_first_attempt:
                first_answered = (
                    await db.execute(
                        select(func.count())
                        .select_from(QuizPracticeAttempt)
                        .where(
                            QuizPracticeAttempt.session_id == session_id,
                            QuizPracticeAttempt.is_first_attempt.is_(True),
                        )
                    )
                ).scalar() or 0
                if int(first_answered) >= int(session.actual_count):
                    session.status = _PRACTICE_COMPLETED
                    session.completed_at = now
                    session.lock_version += 1
            if session.scope_type is not None:
                session.last_answered_at = now
                session.expires_at = now + timedelta(days=7)
            await db.commit()
            return self._attempt_result(attempt, snapshot)

    async def save_answer(
        self,
        user_id: int,
        session_id: int,
        session_question_id: int,
        data: QuizPracticeAnswerSave,
    ) -> QuizPracticeAnswerSaved:
        async with get_db_ctx() as db:
            await self._lock_user(db, user_id)
            session = await self._get_session(db, user_id, session_id, lock=True)
            if session.scope_type is not None:
                from app.services.quiz_v2 import QuizV2Service

                await QuizV2Service()._require_usable_session(db, session)
            if session.status != _PRACTICE_IN_PROGRESS:
                raise ConflictException("当前练习会话已结束，不能保存答案")
            snapshot = (
                await db.execute(
                    select(QuizPracticeSessionQuestion)
                    .where(
                        QuizPracticeSessionQuestion.id == session_question_id,
                        QuizPracticeSessionQuestion.session_id == session_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if snapshot is None:
                raise NotFoundException("练习题目")
            submitted = (
                await db.execute(
                    select(QuizPracticeAttempt.id)
                    .where(
                        QuizPracticeAttempt.session_id == session_id,
                        QuizPracticeAttempt.session_question_id == session_question_id,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if submitted is not None:
                raise ConflictException("该题已经生成正式作答，不能修改答案")
            canonical_answer, _correct = self._grade_snapshot_answer(
                snapshot,
                data.user_answer,
            )
            current_version = int(snapshot.answer_lock_version)
            if data.lock_version != current_version:
                raise ConflictException(
                    f"答案版本冲突，当前版本为 {current_version}"
                )
            now = self._now()
            snapshot.user_answer = canonical_answer
            snapshot.answer_lock_version = current_version + 1
            snapshot.answer_saved_at = now
            if session.scope_type is not None:
                session.last_answered_at = now
                session.expires_at = now + timedelta(days=7)
            await db.commit()
            return QuizPracticeAnswerSaved(
                session_id=int(session.id),
                session_question_id=int(snapshot.id),
                user_answer=snapshot.user_answer,
                lock_version=int(snapshot.answer_lock_version),
                saved_at=snapshot.answer_saved_at,
            )

    async def submit_session(
        self,
        user_id: int,
        session_id: int,
    ) -> QuizPracticeSessionResponse:
        """Grade every saved answer and settle the practice in one transaction."""

        async with get_db_ctx() as db:
            await self._lock_user(db, user_id)
            session = await self._get_session(db, user_id, session_id, lock=True)
            if session.status == _PRACTICE_COMPLETED:
                return await self._serialize_session(db, session)
            if session.scope_type is not None:
                from app.services.quiz_v2 import QuizV2Service

                await QuizV2Service()._require_usable_session(db, session)
            if session.status == _PRACTICE_ABANDONED:
                raise ConflictException("已放弃的练习会话不能交卷")
            if session.status != _PRACTICE_IN_PROGRESS:
                raise ConflictException("当前练习会话已结束，不能交卷")

            snapshots = list(
                (
                    await db.execute(
                        select(QuizPracticeSessionQuestion)
                        .where(QuizPracticeSessionQuestion.session_id == session_id)
                        .order_by(QuizPracticeSessionQuestion.position.asc())
                        .with_for_update()
                    )
                ).scalars()
            )
            attempted_ids = set(
                (
                    await db.execute(
                        select(QuizPracticeAttempt.session_question_id)
                        .where(QuizPracticeAttempt.session_id == session_id)
                        .with_for_update()
                    )
                ).scalars()
            )
            now = self._now()
            answered = [
                snapshot
                for snapshot in snapshots
                if snapshot.user_answer is not None
                and int(snapshot.id) not in attempted_ids
            ]
            stats = await self._ensure_stats(db, user_id) if answered else None
            for snapshot in answered:
                await self._record_attempt_locked(
                    db,
                    user_id=user_id,
                    session=session,
                    snapshot=snapshot,
                    user_answer=snapshot.user_answer,
                    idempotency_key=f"practice-submit:{session_id}:{int(snapshot.id)}",
                    now=now,
                    stats=stats,
                )

            session.status = _PRACTICE_COMPLETED
            session.completed_at = now
            session.abandoned_at = None
            if answered:
                session.last_answered_at = now
            session.lock_version += 1
            await db.commit()
            return await self._serialize_session(db, session)

    async def _ensure_stats(self, db, user_id: int) -> QuizUserStats:
        stats = (
            await db.execute(
                select(QuizUserStats).where(QuizUserStats.user_id == user_id).with_for_update()
            )
        ).scalar_one_or_none()
        if stats is None:
            stats = QuizUserStats(user_id=user_id)
            db.add(stats)
            await db.flush()
        return stats

    @staticmethod
    async def _has_prior_practice_answer(
        db,
        user_id: int,
        question_id: int,
        current_attempt_id: int,
    ) -> bool:
        practice_exists = (
            await db.execute(
                select(QuizPracticeAttempt.id)
                .join(
                    QuizPracticeSessionQuestion,
                    and_(
                        QuizPracticeAttempt.session_id == QuizPracticeSessionQuestion.session_id,
                        QuizPracticeAttempt.session_question_id == QuizPracticeSessionQuestion.id,
                    ),
                )
                .where(
                    QuizPracticeAttempt.user_id == user_id,
                    QuizPracticeSessionQuestion.question_id == question_id,
                    QuizPracticeAttempt.id != current_attempt_id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        return practice_exists is not None

    async def _apply_attempt_stats(
        self,
        db,
        stats: QuizUserStats,
        session: QuizPracticeSession,
        snapshot: QuizPracticeSessionQuestion,
        attempt: QuizPracticeAttempt,
        correct: bool,
        now: datetime,
    ) -> None:
        stats.practice_total_attempts += 1
        if attempt.is_first_attempt:
            stats.practice_first_attempts += 1
            if correct:
                stats.practice_first_correct += 1
            if not await self._has_prior_practice_answer(
                db, attempt.user_id, snapshot.question_id, attempt.id
            ):
                stats.practice_answered_questions += 1
        local_day = self._local_date(now)
        if stats.today_practice_date != local_day:
            stats.today_practice_date = local_day
            stats.today_practice_count = 1
        else:
            stats.today_practice_count += 1
        await self._upsert_checkin(db, stats, attempt.user_id, attempt.id, local_day, now)

    async def _upsert_checkin(
        self,
        db,
        stats: QuizUserStats,
        user_id: int,
        attempt_id: int,
        local_day: date,
        now: datetime,
    ) -> None:
        checkin = (
            await db.execute(
                select(QuizCheckin)
                .where(
                    QuizCheckin.user_id == user_id,
                    QuizCheckin.checkin_date == local_day,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if checkin is not None:
            checkin.questions_completed += 1
            stats.consecutive_days = checkin.consecutive_days
            return
        previous = (
            await db.execute(
                select(QuizCheckin)
                .where(
                    QuizCheckin.user_id == user_id,
                    QuizCheckin.checkin_date < local_day,
                )
                .order_by(QuizCheckin.checkin_date.desc(), QuizCheckin.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        consecutive = (
            int(previous.consecutive_days) + 1
            if previous is not None
            and previous.checkin_date == local_day - timedelta(days=1)
            else 1
        )
        db.add(
            QuizCheckin(
                user_id=user_id,
                checkin_date=local_day,
                questions_completed=1,
                consecutive_days=consecutive,
                first_attempt_id=attempt_id,
            )
        )
        stats.checkin_days += 1
        stats.consecutive_days = consecutive

    async def _apply_wrong_book(
        self,
        db,
        user_id: int,
        session: QuizPracticeSession,
        snapshot: QuizPracticeSessionQuestion,
        correct: bool,
        now: datetime,
        stats: QuizUserStats,
    ) -> None:
        if not correct:
            await self._activate_wrong_item(
                db,
                user_id=user_id,
                question_id=int(snapshot.question_id),
                snapshot=self._wrong_book_snapshot(snapshot),
                occurred_at=now,
                stats=stats,
            )
            return
        item = (
            await db.execute(
                select(QuizWrongItem)
                .where(
                    QuizWrongItem.user_id == user_id,
                    QuizWrongItem.question_id == snapshot.question_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if item is not None and item.status == _WRONG_ACTIVE:
            item.consecutive_correct_count += 1
            if item.consecutive_correct_count < _WRONG_CLEAR_CORRECT_STREAK:
                return
            item.status = _WRONG_CLEARED
            item.cleared_at = now
            item.wrong_count = 0
            item.consecutive_correct_count = 0
            stats.active_wrong_count = max(0, stats.active_wrong_count - 1)

    async def clear_wrong_item(
        self,
        user_id: int,
        question_id: int,
    ) -> bool:
        """Manually remove one question from the user's wrong book."""

        async with get_db_ctx() as db:
            await self._lock_user(db, user_id)
            item = (
                await db.execute(
                    select(QuizWrongItem)
                    .where(
                        QuizWrongItem.user_id == user_id,
                        QuizWrongItem.question_id == question_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if item is None or item.status != _WRONG_ACTIVE:
                return False
            item.status = _WRONG_CLEARED
            item.cleared_at = self._now()
            item.wrong_count = 0
            item.consecutive_correct_count = 0
            stats = await self._ensure_stats(db, user_id)
            stats.active_wrong_count = max(0, stats.active_wrong_count - 1)
            await db.commit()
            return True

    @staticmethod
    def _wrong_book_snapshot(snapshot) -> dict[str, object]:
        return {
            "question_id": int(snapshot.question_id),
            "category_id": (
                int(snapshot.category_id)
                if snapshot.category_id is not None
                else None
            ),
            "category_path": snapshot.category_path,
            "question_type": snapshot.question_type,
            "question_text": snapshot.question_text,
            "options": dict(snapshot.options or {}),
        }

    async def _activate_wrong_item(
        self,
        db,
        *,
        user_id: int,
        question_id: int,
        snapshot: dict[str, object],
        occurred_at: datetime,
        stats: QuizUserStats,
        resets_correct_streak: bool = True,
    ) -> None:
        item = (
            await db.execute(
                select(QuizWrongItem)
                .where(
                    QuizWrongItem.user_id == user_id,
                    QuizWrongItem.question_id == question_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if item is None:
            db.add(
                QuizWrongItem(
                    user_id=user_id,
                    question_id=question_id,
                    status=_WRONG_ACTIVE,
                    first_wrong_at=occurred_at,
                    latest_wrong_at=occurred_at,
                    latest_wrong_snapshot=snapshot,
                    wrong_count=1,
                    consecutive_correct_count=0,
                )
            )
            stats.active_wrong_count += 1
            return
        if item.status == _WRONG_CLEARED:
            item.wrong_count = 0
            item.consecutive_correct_count = 0
            stats.active_wrong_count += 1
        item.status = _WRONG_ACTIVE
        item.wrong_count += 1
        if resets_correct_streak:
            item.consecutive_correct_count = 0
        item.latest_wrong_at = occurred_at
        item.latest_wrong_snapshot = snapshot

    async def apply_settled_exam_wrong_book(
        self,
        db,
        *,
        user_id: int,
        snapshot: QuizExamQuestion,
        is_correct: bool,
        settled_at: datetime,
        stats: QuizUserStats,
    ) -> None:
        """Apply a settled exam result without allowing exam answers to clear errors."""
        if is_correct:
            return
        await self._activate_wrong_item(
            db,
            user_id=user_id,
            question_id=int(snapshot.question_id),
            snapshot=self._wrong_book_snapshot(snapshot),
            occurred_at=settled_at,
            stats=stats,
            resets_correct_streak=False,
        )

    async def abandon_session(
        self,
        user_id: int,
        session_id: int,
    ) -> QuizPracticeAbandonResponse:
        async with get_db_ctx() as db:
            await self._lock_user(db, user_id)
            session = await self._get_session(db, user_id, session_id, lock=True)
            if session.scope_type is not None:
                from app.services.quiz_v2 import QuizV2Service

                await QuizV2Service()._sync_session_access(db, session)
                if session.status == "expired":
                    await db.commit()
                    raise QuizV2Service._session_expired()
                if session.status == "terminated":
                    await db.commit()
                    raise QuizV2Service._session_terminated()
            if session.status == _PRACTICE_ABANDONED:
                return QuizPracticeAbandonResponse(
                    session_id=session.id,
                    status=_PRACTICE_ABANDONED,
                    abandoned_at=session.abandoned_at,
                )
            if session.status == _PRACTICE_COMPLETED:
                raise ConflictException("已完成的练习会话不能放弃")
            if session.status in {"expired", "terminated"}:
                raise ConflictException("已结束的练习会话不能放弃")
            now = self._now()
            session.status = _PRACTICE_ABANDONED
            session.abandoned_at = now
            session.lock_version += 1
            await db.commit()
            return QuizPracticeAbandonResponse(
                session_id=session.id,
                status=_PRACTICE_ABANDONED,
                abandoned_at=now,
            )

    async def get_history(
        self,
        user_id: int,
        query: QuizPracticeHistoryQuery,
    ) -> PaginatedData[QuizPracticeHistoryItem]:
        async with get_db_ctx() as db:
            categories = await self._load_categories(db)
            scope_ids = (
                self._category_subtree_ids(categories, query.category_id)
                if query.category_id is not None
                else set()
            )
            start_at, end_at = self._utc_range(query.date_from, query.date_to)
            stmt = (
                select(QuizPracticeAttempt, QuizPracticeSessionQuestion, QuizQuestion)
                .join(
                    QuizPracticeSessionQuestion,
                    and_(
                        QuizPracticeAttempt.session_id == QuizPracticeSessionQuestion.session_id,
                        QuizPracticeAttempt.session_question_id == QuizPracticeSessionQuestion.id,
                    ),
                )
                .outerjoin(
                    QuizQuestion,
                    QuizQuestion.id == QuizPracticeSessionQuestion.question_id,
                )
                .where(QuizPracticeAttempt.user_id == user_id)
            )
            if query.category_id is not None:
                stmt = stmt.where(QuizPracticeSessionQuestion.category_id.in_(scope_ids))
            if query.question_type is not None:
                stmt = stmt.where(
                    QuizPracticeSessionQuestion.question_type == str(query.question_type)
                )
            if query.is_correct is not None:
                stmt = stmt.where(QuizPracticeAttempt.is_correct.is_(query.is_correct))
            if start_at is not None:
                stmt = stmt.where(QuizPracticeAttempt.submitted_at >= start_at)
            if end_at is not None:
                stmt = stmt.where(QuizPracticeAttempt.submitted_at < end_at)
            total = (
                await db.execute(select(func.count()).select_from(stmt.subquery()))
            ).scalar() or 0
            rows = (
                await db.execute(
                    stmt.order_by(
                        QuizPracticeAttempt.submitted_at.desc(),
                        QuizPracticeAttempt.id.desc(),
                    )
                    .offset((query.page - 1) * query.page_size)
                    .limit(query.page_size)
                )
            ).all()
            items: list[QuizPracticeHistoryItem] = []
            for attempt, snapshot, current_question in rows:
                items.append(
                    QuizPracticeHistoryItem(
                        attempt_id=int(attempt.id),
                        session_id=int(attempt.session_id),
                        session_question_id=int(attempt.session_question_id),
                        question_id=int(snapshot.question_id),
                        category_path=[
                            QuizCategoryPathItem.model_validate(item)
                            for item in snapshot.category_path
                        ],
                        question_type=snapshot.question_type,
                        question_text=snapshot.question_text,
                        options=dict(snapshot.options or {}),
                        user_answer=attempt.user_answer,
                        correct_answer=snapshot.correct_answer,
                        explanation=snapshot.explanation,
                        is_correct=bool(attempt.is_correct),
                        attempt_no=int(attempt.attempt_no),
                        submitted_at=attempt.submitted_at,
                        current_question_status=(
                            current_question.status if current_question is not None else None
                        ),
                    )
                )
            return PaginatedData(
                items=items,
                total=int(total),
                page=query.page,
                page_size=query.page_size,
            )

    async def list_wrong_book(
        self,
        user_id: int,
        query: QuizWrongBookQuery,
    ) -> PaginatedData[QuizWrongBookItem]:
        async with get_db_ctx() as db:
            stmt = (
                select(QuizWrongItem, QuizQuestion)
                .join(QuizQuestion, QuizQuestion.id == QuizWrongItem.question_id)
                .where(
                    QuizWrongItem.user_id == user_id,
                    QuizWrongItem.status == _WRONG_ACTIVE,
                )
            )
            total = (
                await db.execute(select(func.count()).select_from(stmt.subquery()))
            ).scalar() or 0
            rows = (
                await db.execute(
                    stmt.order_by(QuizWrongItem.latest_wrong_at.desc(), QuizWrongItem.id.desc())
                    .offset((query.page - 1) * query.page_size)
                    .limit(query.page_size)
                )
            ).all()
            categories = await self._load_categories(db)
            by_id, _ = self._category_maps(categories)
            items: list[QuizWrongBookItem] = []
            for item, question in rows:
                effective = self._is_effectively_active(question.category_id, by_id)
                if question.status == _PUBLISHED:
                    public = self._public_question(question)
                else:
                    public = self._public_question_from_snapshot(item.latest_wrong_snapshot)
                items.append(
                    QuizWrongBookItem(
                        id=int(item.id),
                        question_id=int(item.question_id),
                        status=item.status,
                        question=public,
                        question_status=question.status,
                        usable_for_practice=question.status == _PUBLISHED and effective,
                        first_wrong_at=item.first_wrong_at,
                        latest_wrong_at=item.latest_wrong_at,
                        wrong_count=int(item.wrong_count),
                    )
                )
            return PaginatedData(
                items=items,
                total=int(total),
                page=query.page,
                page_size=query.page_size,
            )

    async def list_collections(
        self,
        user_id: int,
        query: QuizWrongBookQuery,
    ) -> PaginatedData[QuizCollectionItem]:
        async with get_db_ctx() as db:
            stmt = (
                select(QuizCollection, QuizQuestion)
                .join(QuizQuestion, QuizQuestion.id == QuizCollection.question_id)
                .where(
                    QuizCollection.user_id == user_id,
                    QuizCollection.is_active.is_(True),
                )
            )
            total = (
                await db.execute(select(func.count()).select_from(stmt.subquery()))
            ).scalar() or 0
            rows = (
                await db.execute(
                    stmt.order_by(QuizCollection.collected_at.desc(), QuizCollection.id.desc())
                    .offset((query.page - 1) * query.page_size)
                    .limit(query.page_size)
                )
            ).all()
            items = [
                QuizCollectionItem(
                    id=int(item.id),
                    question_id=int(item.question_id),
                    question=self._public_question(question),
                    question_status=question.status,
                    is_active=bool(item.is_active),
                    collected_at=item.collected_at,
                )
                for item, question in rows
            ]
            return PaginatedData(
                items=items,
                total=int(total),
                page=query.page,
                page_size=query.page_size,
            )

    async def add_collection(
        self,
        user_id: int,
        data: QuizCollectionCreate,
    ) -> QuizCollectionMutationResponse:
        async with get_db_ctx() as db:
            await self._lock_user(db, user_id)
            question = (
                await db.execute(
                    select(QuizQuestion)
                    .where(QuizQuestion.id == data.question_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if question is None:
                raise NotFoundException("题目")
            categories = await self._load_categories(db)
            by_id, _ = self._category_maps(categories)
            if question.status != _PUBLISHED or not self._is_effectively_active(
                question.category_id,
                by_id,
            ):
                raise BusinessException("当前题目不可收藏")
            item = (
                await db.execute(
                    select(QuizCollection)
                    .where(
                        QuizCollection.user_id == user_id,
                        QuizCollection.question_id == data.question_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            now = self._now()
            stats = await self._ensure_stats(db, user_id)
            if item is None:
                item = QuizCollection(
                    user_id=user_id,
                    question_id=data.question_id,
                    is_active=True,
                    collected_at=now,
                )
                db.add(item)
                stats.active_collection_count += 1
                response_updated_at = now
            elif not item.is_active:
                item.is_active = True
                item.collected_at = now
                stats.active_collection_count += 1
                response_updated_at = now
            else:
                # Cache ORM values before commit.  AsyncSession expires
                # server-generated timestamps on commit, and reading them
                # afterwards would attempt implicit async I/O.
                response_updated_at = item.updated_at or now
            await db.commit()
            return QuizCollectionMutationResponse(
                question_id=data.question_id,
                is_active=True,
                updated_at=response_updated_at,
            )

    async def remove_collection(
        self,
        user_id: int,
        question_id: int,
    ) -> QuizCollectionMutationResponse:
        async with get_db_ctx() as db:
            await self._lock_user(db, user_id)
            item = (
                await db.execute(
                    select(QuizCollection)
                    .where(
                        QuizCollection.user_id == user_id,
                        QuizCollection.question_id == question_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            now = self._now()
            if item is None:
                return QuizCollectionMutationResponse(
                    question_id=question_id,
                    is_active=False,
                    updated_at=now,
                )
            if item.is_active:
                item.is_active = False
                item.removed_at = now
                stats = await self._ensure_stats(db, user_id)
                stats.active_collection_count = max(0, stats.active_collection_count - 1)
                response_updated_at = now
                await db.commit()
            else:
                response_updated_at = item.updated_at or now
            return QuizCollectionMutationResponse(
                question_id=question_id,
                is_active=False,
                updated_at=response_updated_at,
            )

    async def get_checkin_status(self, user_id: int) -> QuizCheckinStatusResponse:
        local_day = self._local_date()
        async with get_db_ctx() as db:
            current = (
                await db.execute(
                    select(QuizCheckin).where(
                        QuizCheckin.user_id == user_id,
                        QuizCheckin.checkin_date == local_day,
                    )
                )
            ).scalar_one_or_none()
            if current is not None:
                return QuizCheckinStatusResponse(
                    checkin_date=local_day,
                    checked_in=True,
                    questions_completed=int(current.questions_completed),
                    consecutive_days=int(current.consecutive_days),
                )
            previous = (
                await db.execute(
                    select(QuizCheckin)
                    .where(
                        QuizCheckin.user_id == user_id,
                        QuizCheckin.checkin_date < local_day,
                    )
                    .order_by(QuizCheckin.checkin_date.desc(), QuizCheckin.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            streak = (
                int(previous.consecutive_days)
                if previous is not None
                and previous.checkin_date == local_day - timedelta(days=1)
                else 0
            )
            return QuizCheckinStatusResponse(
                checkin_date=local_day,
                checked_in=False,
                questions_completed=0,
                consecutive_days=streak,
            )

    async def get_checkin_calendar(
        self,
        user_id: int,
        query: QuizCheckinCalendarQuery,
    ) -> list[QuizCheckinDay]:
        async with get_db_ctx() as db:
            rows = (
                await db.execute(
                    select(QuizCheckin)
                    .where(
                        QuizCheckin.user_id == user_id,
                        QuizCheckin.checkin_date >= query.date_from,
                        QuizCheckin.checkin_date <= query.date_to,
                    )
                    .order_by(QuizCheckin.checkin_date.asc())
                )
            ).scalars().all()
            return [
                QuizCheckinDay(
                    checkin_date=row.checkin_date,
                    questions_completed=int(row.questions_completed),
                    consecutive_days=int(row.consecutive_days),
                )
                for row in rows
            ]

    async def _latest_attempt_accuracy(
        self,
        db,
        user_id: int,
        question_filters: list | None = None,
    ) -> Decimal:
        """Accuracy based on each question's most recent practice attempt.

        A question answered in several sessions counts once, with its latest
        attempt deciding correctness. ``question_filters`` narrows the pool to
        a scope (mirroring the published-status rule of scoped stats); when
        omitted every attempt of the user is considered, matching the global
        stats-table behaviour.
        """
        stmt = (
            select(
                QuizPracticeSessionQuestion.question_id.label("question_id"),
                QuizPracticeAttempt.is_correct.label("is_correct"),
                func.row_number()
                .over(
                    partition_by=QuizPracticeSessionQuestion.question_id,
                    order_by=(
                        QuizPracticeAttempt.submitted_at.desc(),
                        QuizPracticeAttempt.id.desc(),
                    ),
                )
                .label("rn"),
            )
            .select_from(QuizPracticeAttempt)
            .join(
                QuizPracticeSessionQuestion,
                QuizPracticeSessionQuestion.id
                == QuizPracticeAttempt.session_question_id,
            )
            .where(QuizPracticeAttempt.user_id == user_id)
        )
        if question_filters:
            stmt = stmt.join(
                QuizQuestion,
                QuizQuestion.id == QuizPracticeSessionQuestion.question_id,
            ).where(*question_filters)
        ranked = stmt.subquery()
        row = (
            await db.execute(
                select(
                    func.count(ranked.c.question_id),
                    func.sum(case((ranked.c.is_correct.is_(True), 1), else_=0)),
                ).where(ranked.c.rn == 1)
            )
        ).one()
        answered = int(row[0] or 0)
        correct = int(row[1] or 0)
        return (
            (Decimal(correct) * Decimal("100") / Decimal(answered)).quantize(
                Decimal("0.1")
            )
            if answered
            else Decimal("0.0")
        )

    async def _scoped_practice_stats(
        self,
        db,
        user_id: int,
        query: QuizStatsQuery,
        stats: QuizUserStats | None,
        local_day: date,
        current_streak: int,
    ) -> QuizPracticeStats:
        assert query.scope_type is not None and query.scope_id is not None
        question_scope = None
        if query.scope_type in {"library", "module", "knowledge_point"}:
            now = self._now()
            visible_library = and_(
                QuizLibrary.v2_enabled.is_(True),
                QuizLibrary.status == "published",
                or_(
                    QuizLibrary.access_mode == "free",
                    and_(
                        QuizLibrary.access_mode == "course_entitlement",
                        select(QuizLibraryEntitlement.id).where(
                            QuizLibraryEntitlement.user_id == user_id,
                            QuizLibraryEntitlement.library_id == QuizLibrary.id,
                            QuizLibraryEntitlement.status == "active",
                            QuizLibraryEntitlement.starts_at <= now,
                            or_(QuizLibraryEntitlement.ends_at.is_(None), QuizLibraryEntitlement.ends_at > now),
                        ).exists(),
                    ),
                ),
            )
            if query.scope_type == "library":
                scope_exists = await db.scalar(select(QuizLibrary.id).where(QuizLibrary.id == query.scope_id, visible_library))
                question_scope = QuizQuestion.library_id == query.scope_id
            elif query.scope_type == "module":
                scope_exists = await db.scalar(select(QuizModule.id).join(QuizLibrary, QuizLibrary.id == QuizModule.library_id).where(
                    QuizModule.id == query.scope_id, QuizModule.status == "active", QuizModule.system_kind == "none", visible_library,
                ))
                question_scope = QuizQuestion.knowledge_point_id.in_(select(QuizKnowledgePoint.id).where(
                    QuizKnowledgePoint.module_id == query.scope_id, QuizKnowledgePoint.status == "active", QuizKnowledgePoint.system_kind == "none",
                ))
            else:
                scope_exists = await db.scalar(select(QuizKnowledgePoint.id).join(QuizLibrary, QuizLibrary.id == QuizKnowledgePoint.library_id).where(
                    QuizKnowledgePoint.id == query.scope_id, QuizKnowledgePoint.status == "active", QuizKnowledgePoint.system_kind == "none", visible_library,
                ))
                question_scope = QuizQuestion.knowledge_point_id == query.scope_id
            # Keep compatibility with legacy callers that use the same scope
            # labels for QuizCategory IDs; fall through to the old resolver
            # when no visible V2 entity matches this ID.
            if scope_exists is None:
                question_scope = None
        categories = await self._load_categories(db)
        by_id, _ = self._category_maps(categories)
        root = by_id.get(query.scope_id) if question_scope is None else None
        if root is None and question_scope is None:
            raise NotFoundException("棰樺簱鑼冨洿")
        expected_depth = {
            "library": 1,
            "module": 2,
            "knowledge_point": 3,
        }[query.scope_type]
        if question_scope is None and int(root.depth) != expected_depth:
            raise ValidationException("棰樺簱鑼冨洿绫诲瀷涓庡垎绫诲眰绾т笉鍖归厤")

        if question_scope is None and not self._is_effectively_active(root.id, by_id):
            scope_ids: set[int] = set()
        elif question_scope is None:
            _, scope_ids = self._effective_category_ids(categories, root.id)

        start_at, end_at = self._utc_range(local_day, local_day)
        base_conditions = (
            QuizPracticeAttempt.user_id == user_id,
            QuizQuestion.status == _PUBLISHED,
            question_scope if question_scope is not None else QuizQuestion.category_id.in_(scope_ids),
        )
        metrics = (
            await db.execute(
                select(
                    func.count(QuizPracticeAttempt.id),
                    func.count(QuizPracticeAttempt.id).filter(
                        QuizPracticeAttempt.is_first_attempt.is_(True)
                    ),
                    func.count(QuizPracticeAttempt.id).filter(
                        QuizPracticeAttempt.is_first_attempt.is_(True),
                        QuizPracticeAttempt.is_correct.is_(True),
                    ),
                    func.count(func.distinct(QuizQuestion.id)),
                    func.count(QuizPracticeAttempt.id).filter(
                        QuizPracticeAttempt.submitted_at >= start_at,
                        QuizPracticeAttempt.submitted_at < end_at,
                    ),
                )
                .select_from(QuizPracticeAttempt)
                .join(
                    QuizPracticeSessionQuestion,
                    and_(
                        QuizPracticeAttempt.session_id
                        == QuizPracticeSessionQuestion.session_id,
                        QuizPracticeAttempt.session_question_id
                        == QuizPracticeSessionQuestion.id,
                    ),
                )
                .join(
                    QuizQuestion,
                    QuizQuestion.id == QuizPracticeSessionQuestion.question_id,
                )
                .where(*base_conditions)
            )
        ).one()
        total_attempts = int(metrics[0] or 0)
        first_attempts = int(metrics[1] or 0)
        first_correct = int(metrics[2] or 0)
        answered_questions = int(metrics[3] or 0)
        today_questions = int(metrics[4] or 0)

        active_wrong_count = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(QuizWrongItem)
                    .join(QuizQuestion, QuizQuestion.id == QuizWrongItem.question_id)
                    .where(
                        QuizWrongItem.user_id == user_id,
                        QuizWrongItem.status == _WRONG_ACTIVE,
                        QuizQuestion.status == _PUBLISHED,
                        question_scope if question_scope is not None else QuizQuestion.category_id.in_(scope_ids),
                    )
                )
            ).scalar()
            or 0
        )
        active_collection_count = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(QuizCollection)
                    .join(QuizQuestion, QuizQuestion.id == QuizCollection.question_id)
                    .where(
                        QuizCollection.user_id == user_id,
                        QuizCollection.is_active.is_(True),
                        QuizQuestion.status == _PUBLISHED,
                        question_scope if question_scope is not None else QuizQuestion.category_id.in_(scope_ids),
                    )
                )
            ).scalar()
            or 0
        )
        accuracy = (
            (Decimal(first_correct) * Decimal("100") / Decimal(first_attempts)).quantize(
                Decimal("0.1")
            )
            if first_attempts
            else Decimal("0.0")
        )
        latest_accuracy = await self._latest_attempt_accuracy(
            db, user_id, list(base_conditions[1:])
        )
        return QuizPracticeStats(
            total_attempts=total_attempts,
            first_attempts=first_attempts,
            first_correct_attempts=first_correct,
            accuracy=accuracy,
            latest_accuracy=latest_accuracy,
            answered_questions=answered_questions,
            active_wrong_count=active_wrong_count,
            active_collection_count=active_collection_count,
            checkin_days=int(stats.checkin_days) if stats is not None else 0,
            consecutive_days=current_streak,
            today_questions=today_questions,
        )

    async def get_stats(
        self,
        user_id: int,
        query: QuizStatsQuery | None = None,
    ) -> QuizStatsResponse:
        query = query or QuizStatsQuery()
        local_day = self._local_date()
        async with get_db_ctx() as db:
            stats = (
                await db.execute(select(QuizUserStats).where(QuizUserStats.user_id == user_id))
            ).scalar_one_or_none()
            if stats is None and query.scope_type is None:
                practice = QuizPracticeStats(
                    total_attempts=0,
                    first_attempts=0,
                    first_correct_attempts=0,
                    accuracy=Decimal("0.0"),
                    latest_accuracy=Decimal("0.0"),
                    answered_questions=0,
                    active_wrong_count=0,
                    active_collection_count=0,
                    checkin_days=0,
                    consecutive_days=0,
                    today_questions=0,
                )
                return QuizStatsResponse(
                    practice=practice,
                    exam=QuizExamStats(
                        completed_exam_count=0,
                        timed_out_exam_count=0,
                        total_questions=0,
                        correct_count=0,
                        wrong_count=0,
                        unanswered_count=0,
                        average_score=None,
                        highest_score=None,
                        latest_score=None,
                    ),
                )
            first_attempts = int(stats.practice_first_attempts) if stats is not None else 0
            first_correct = int(stats.practice_first_correct) if stats is not None else 0
            accuracy = (
                (Decimal(first_correct) * Decimal("100") / Decimal(first_attempts)).quantize(
                    Decimal("0.1")
                )
                if first_attempts
                else Decimal("0.0")
            )
            current_streak = int(stats.consecutive_days) if stats is not None else 0
            if stats is not None and stats.today_practice_date != local_day:
                previous = (
                    await db.execute(
                        select(QuizCheckin)
                        .where(
                            QuizCheckin.user_id == user_id,
                            QuizCheckin.checkin_date == local_day - timedelta(days=1),
                        )
                    )
                ).scalar_one_or_none()
                current_streak = int(previous.consecutive_days) if previous is not None else 0
            practice = (
                await self._scoped_practice_stats(
                    db, user_id, query, stats, local_day, current_streak
                )
                if query.scope_type is not None
                else QuizPracticeStats(
                    total_attempts=int(stats.practice_total_attempts),
                    first_attempts=first_attempts,
                    first_correct_attempts=first_correct,
                    accuracy=accuracy,
                    latest_accuracy=await self._latest_attempt_accuracy(db, user_id),
                    answered_questions=int(stats.practice_answered_questions),
                    active_wrong_count=int(stats.active_wrong_count),
                    active_collection_count=int(stats.active_collection_count),
                    checkin_days=int(stats.checkin_days),
                    consecutive_days=current_streak,
                    today_questions=(
                        int(stats.today_practice_count)
                        if stats.today_practice_date == local_day
                        else 0
                    ),
                )
            )
            exam_total = int(stats.exam_total_questions) if stats is not None else 0
            settled_exam_count = (
                int(stats.completed_exam_count + stats.timed_out_exam_count)
                if stats is not None
                else 0
            )
            average = (
                (
                    Decimal(stats.exam_score_sum) / Decimal(settled_exam_count)
                ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
                if settled_exam_count and stats is not None
                else None
            )
            exam = QuizExamStats(
                completed_exam_count=int(stats.completed_exam_count) if stats is not None else 0,
                timed_out_exam_count=int(stats.timed_out_exam_count) if stats is not None else 0,
                total_questions=exam_total,
                correct_count=int(stats.exam_correct) if stats is not None else 0,
                wrong_count=int(stats.exam_wrong) if stats is not None else 0,
                unanswered_count=int(stats.exam_unanswered) if stats is not None else 0,
                average_score=average,
                highest_score=stats.exam_high_score if stats is not None else None,
                latest_score=stats.exam_latest_score if stats is not None else None,
            )
            return QuizStatsResponse(practice=practice, exam=exam)
