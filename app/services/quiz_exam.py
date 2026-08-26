"""Frozen user-facing simulated-exam workflow (QB-23 through QB-29)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from app.adapter.database import get_db_ctx
from app.domain.community.src.index import (
    QuizExam,
    QuizExamAnswer,
    QuizExamQuestion,
    QuizKnowledgePoint,
    QuizLibrary,
    QuizModule,
    QuizQuestionRevision,
    QuizQuestionRevisionStats,
    QuizQuestion,
)
from app.domain.community.src.rule.quiz import (
    QuizExamStatus,
    QuizQuestionStatus,
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
    QuizCategoryPathItem,
    QuizExamAbandonedDetail,
    QuizExamAbandonedQuestion,
    QuizExamActionResponse,
    QuizExamAnswerSaved,
    QuizExamCreate,
    QuizManualExamCreate,
    QuizExamDetailResponse,
    QuizExamInProgressDetail,
    QuizExamListItem,
    QuizExamListQuery,
    QuizExamQuestionResult,
    QuizExamQuestionState,
    QuizExamSettledDetail,
    QuizExamAnswerSave,
    QuizPublicQuestion,
)
from app.services.quiz_practice import QuizPracticeService
from app.services.quiz_v2 import QuizV2Service


_IN_PROGRESS = QuizExamStatus.IN_PROGRESS.value
_COMPLETED = QuizExamStatus.COMPLETED.value
_TIMED_OUT = QuizExamStatus.TIMED_OUT.value
_ABANDONED = QuizExamStatus.ABANDONED.value
_PUBLISHED = QuizQuestionStatus.PUBLISHED.value
_DURATION_SECONDS = settings.QUIZ_EXAM_DURATION_SECONDS
_MIN_QUESTION_COUNT = settings.QUIZ_MIN_QUESTION_COUNT
_MAX_QUESTION_COUNT = settings.QUIZ_MAX_QUESTION_COUNT

logger = logging.getLogger(__name__)


class QuizExamService:
    """Transactional implementation of the new plural ``/exams`` API."""

    def __init__(self) -> None:
        self.practice = QuizPracticeService()
        self.v2 = QuizV2Service()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _snapshot_dict(snapshot: QuizExamQuestion) -> dict[str, object]:
        return {
            "question_id": int(snapshot.question_id),
            "question_revision_id": (
                int(getattr(snapshot, "question_revision_id"))
                if getattr(snapshot, "question_revision_id", None) is not None
                else None
            ),
            "category_id": (
                int(snapshot.category_id) if snapshot.category_id is not None else None
            ),
            "category_path": list(snapshot.category_path or []),
            "question_type": str(snapshot.question_type),
            "question_text": snapshot.question_text,
            "options": dict(sorted((snapshot.options or {}).items())),
            "correct_answer": snapshot.correct_answer,
            "explanation": snapshot.explanation,
            "image_urls": list(snapshot.image_urls or []),
            "question_lock_version": int(snapshot.question_lock_version),
        }

    @staticmethod
    def _public_question(snapshot: QuizExamQuestion) -> QuizPublicQuestion:
        path = list(snapshot.category_path or [])
        library_id = next(
            (int(item["id"]) for item in path if item.get("kind") == "library"),
            None,
        )
        point_id = next(
            (
                int(item["id"])
                for item in reversed(path)
                if item.get("kind") == "knowledge_point"
            ),
            None,
        )
        return QuizPublicQuestion(
            id=int(snapshot.question_id),
            category_id=(
                int(snapshot.category_id) if snapshot.category_id is not None else None
            ),
            library_id=library_id,
            knowledge_point_id=point_id,
            question_revision_id=(
                int(getattr(snapshot, "question_revision_id"))
                if getattr(snapshot, "question_revision_id", None) is not None
                else None
            ),
            question_type=snapshot.question_type,
            question_text=snapshot.question_text,
            options=dict(sorted((snapshot.options or {}).items())),
            option_image_urls=dict(getattr(snapshot, "option_image_urls", None) or {}),
            image_urls=list(snapshot.image_urls or []),
        )

    @staticmethod
    def _path(snapshot: QuizExamQuestion) -> list[QuizCategoryPathItem]:
        return [
            QuizCategoryPathItem.model_validate(item)
            for item in (snapshot.category_path or [])
        ]

    @staticmethod
    def _finished_at(exam: QuizExam) -> datetime | None:
        return exam.submitted_at or exam.timed_out_at or exam.abandoned_at

    @staticmethod
    def _scope_fields(exam: QuizExam) -> dict[str, object]:
        return {
            "category_id": (
                int(exam.category_id) if exam.category_id is not None else None
            ),
            "library_id": (
                int(exam.library_id) if exam.library_id is not None else None
            ),
            "scope_type": exam.scope_type,
            "scope_id": int(exam.scope_id) if exam.scope_id is not None else None,
        }

    async def _lock_user_any(self, db, user_id: int) -> User:
        result = await db.execute(
            select(User).where(User.id == user_id).with_for_update()
        )
        user = result.scalar_one_or_none()
        if user is None:
            raise NotFoundException("用户")
        return user

    async def _get_exam(
        self,
        db,
        user_id: int,
        exam_id: int,
        *,
        lock: bool = False,
    ) -> QuizExam:
        statement = select(QuizExam).where(
            QuizExam.id == exam_id,
            QuizExam.user_id == user_id,
        )
        if lock:
            statement = statement.with_for_update()
        exam = (await db.execute(statement)).scalar_one_or_none()
        if exam is None:
            raise NotFoundException("考试")
        return exam

    async def _load_snapshots(self, db, exam_id: int, *, lock: bool = False):
        statement = (
            select(QuizExamQuestion)
            .where(QuizExamQuestion.exam_id == exam_id)
            .order_by(QuizExamQuestion.position.asc())
        )
        if lock:
            statement = statement.with_for_update()
        return list((await db.execute(statement)).scalars().all())

    async def _load_answers(self, db, exam_id: int, *, lock: bool = False):
        statement = select(QuizExamAnswer).where(QuizExamAnswer.exam_id == exam_id)
        if lock:
            statement = statement.with_for_update()
        rows = list((await db.execute(statement)).scalars().all())
        return {int(row.exam_question_id): row for row in rows}

    @staticmethod
    def _normalize_answer(snapshot: QuizExamQuestion, answer: object):
        try:
            normalized = normalize_submitted_answer(
                snapshot.question_type,
                answer,
                options=snapshot.options,
            )
        except ValueError as exc:
            raise ValidationException(str(exc)) from exc
        return normalized

    @classmethod
    def _grade_answer(
        cls,
        snapshot: QuizExamQuestion,
        answer: object,
    ) -> tuple[str | list[str], bool]:
        normalized = cls._normalize_answer(snapshot, answer)
        try:
            correct = answers_match(
                snapshot.question_type,
                normalized,
                snapshot.correct_answer,
                options=snapshot.options,
            )
        except ValueError as exc:
            raise ValidationException(str(exc)) from exc
        return normalized, correct

    @staticmethod
    def _score(correct_count: int, question_count: int) -> Decimal:
        if question_count <= 0:
            return Decimal("0.0")
        return (
            Decimal(correct_count * 100) / Decimal(question_count)
        ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

    async def _serialize_exam(
        self,
        db,
        exam: QuizExam,
        *,
        server_time: datetime | None = None,
    ) -> QuizExamDetailResponse:
        snapshots = await self._load_snapshots(db, exam.id)
        answers = await self._load_answers(db, exam.id)
        if exam.status == _IN_PROGRESS:
            questions = [
                QuizExamQuestionState(
                    **self._public_question(snapshot).model_dump(),
                    exam_question_id=int(snapshot.id),
                    position=int(snapshot.position),
                    category_path=self._path(snapshot),
                    user_answer=(
                        answers[int(snapshot.id)].user_answer
                        if int(snapshot.id) in answers
                        else None
                    ),
                    answer_lock_version=(
                        int(answers[int(snapshot.id)].lock_version)
                        if int(snapshot.id) in answers
                        else None
                    ),
                )
                for snapshot in snapshots
            ]
            return QuizExamInProgressDetail(
                id=int(exam.id),
                status=_IN_PROGRESS,
                **self._scope_fields(exam),
                question_count=int(exam.question_count),
                duration_seconds=_DURATION_SECONDS,
                started_at=exam.started_at,
                deadline_at=exam.deadline_at,
                server_time=server_time or self._now(),
                questions=questions,
            )

        if exam.status == _ABANDONED:
            questions = [
                QuizExamAbandonedQuestion(
                    **self._public_question(snapshot).model_dump(),
                    exam_question_id=int(snapshot.id),
                    position=int(snapshot.position),
                    answered=int(snapshot.id) in answers,
                )
                for snapshot in snapshots
            ]
            return QuizExamAbandonedDetail(
                id=int(exam.id),
                status=_ABANDONED,
                **self._scope_fields(exam),
                question_count=int(exam.question_count),
                duration_seconds=_DURATION_SECONDS,
                started_at=exam.started_at,
                deadline_at=exam.deadline_at,
                abandoned_at=exam.abandoned_at,
                questions=questions,
            )

        questions = []
        for snapshot in snapshots:
            answer = answers.get(int(snapshot.id))
            user_answer = None
            is_correct = False
            if answer is not None:
                user_answer = answer.user_answer
                if answer.is_correct is not None:
                    is_correct = bool(answer.is_correct)
                else:
                    _, is_correct = self._grade_answer(snapshot, user_answer)
            questions.append(
                QuizExamQuestionResult(
                    **self._public_question(snapshot).model_dump(),
                    exam_question_id=int(snapshot.id),
                    position=int(snapshot.position),
                    user_answer=user_answer,
                    correct_answer=snapshot.correct_answer,
                    explanation=snapshot.explanation,
                    is_correct=is_correct,
                )
            )
        return QuizExamSettledDetail(
            id=int(exam.id),
            status=exam.status,
            **self._scope_fields(exam),
            question_count=int(exam.question_count),
            duration_seconds=_DURATION_SECONDS,
            started_at=exam.started_at,
            deadline_at=exam.deadline_at,
            finished_at=self._finished_at(exam),
            correct_count=int(exam.correct_count or 0),
            wrong_count=int(exam.wrong_count or 0),
            unanswered_count=int(exam.unanswered_count or 0),
            score=Decimal(exam.score or 0).quantize(Decimal("0.1")),
            questions=questions,
        )

    async def _settle_locked(
        self,
        db,
        exam: QuizExam,
        *,
        status: str,
        settled_at: datetime,
    ) -> QuizExam:
        if exam.status in {_COMPLETED, _TIMED_OUT}:
            return exam
        if exam.status == _ABANDONED:
            raise ConflictException("已放弃的考试不能结算")
        if settled_at.tzinfo is None:
            settled_at = settled_at.replace(tzinfo=timezone.utc)
        snapshots = await self._load_snapshots(db, exam.id, lock=True)
        answers = await self._load_answers(db, exam.id, lock=True)
        correct_count = 0
        wrong_count = 0
        unanswered_count = 0
        for snapshot in snapshots:
            answer = answers.get(int(snapshot.id))
            if answer is None:
                unanswered_count += 1
                continue
            normalized, correct = self._grade_answer(snapshot, answer.user_answer)
            answer.user_answer = normalized
            answer.is_correct = correct
            if correct:
                correct_count += 1
            else:
                wrong_count += 1
            if snapshot.question_revision_id is not None:
                statement = pg_insert(QuizQuestionRevisionStats).values(
                    question_id=snapshot.question_id,
                    question_revision_id=snapshot.question_revision_id,
                    practice_first_attempts=0,
                    practice_first_correct=0,
                    exam_answers=1,
                    exam_correct=1 if correct else 0,
                    aggregated_through=settled_at,
                )
                await db.execute(
                    statement.on_conflict_do_update(
                        index_elements=[
                            QuizQuestionRevisionStats.question_revision_id
                        ],
                        set_={
                            "exam_answers": QuizQuestionRevisionStats.exam_answers
                            + 1,
                            "exam_correct": QuizQuestionRevisionStats.exam_correct
                            + (1 if correct else 0),
                            "aggregated_through": settled_at,
                        },
                    )
                )

        stats = await self.practice._ensure_stats(db, int(exam.user_id))
        stats.exam_total_questions += len(snapshots)
        stats.exam_correct += correct_count
        stats.exam_wrong += wrong_count
        stats.exam_unanswered += unanswered_count
        score = self._score(correct_count, len(snapshots))
        stats.exam_score_sum = Decimal(stats.exam_score_sum or 0) + score
        if stats.exam_high_score is None or score > stats.exam_high_score:
            stats.exam_high_score = score
        stats.exam_latest_score = score
        stats.exam_latest_at = settled_at
        if status == _COMPLETED:
            stats.completed_exam_count += 1
            exam.submitted_at = settled_at
            exam.timed_out_at = None
        else:
            stats.timed_out_exam_count += 1
            exam.timed_out_at = settled_at
            exam.submitted_at = None

        # Move the exam to one complete, constraint-valid terminal state
        # before wrong-book synchronization.  That synchronization executes
        # queries and can therefore trigger autoflush; a partially updated
        # lifecycle (for example in_progress + submitted_at) is invalid.
        exam.status = status
        exam.correct_count = correct_count
        exam.wrong_count = wrong_count
        exam.unanswered_count = unanswered_count
        exam.score = score
        exam.abandoned_at = None
        exam.lock_version += 1
        for snapshot in snapshots:
            answer = answers.get(int(snapshot.id))
            if answer is not None and answer.is_correct is False:
                await self.practice.apply_settled_exam_wrong_book(
                    db,
                    user_id=int(exam.user_id),
                    snapshot=snapshot,
                    is_correct=False,
                    settled_at=settled_at,
                    stats=stats,
                )
        return exam

    async def create_exam(
        self,
        user_id: int,
        data: QuizExamCreate,
    ) -> QuizExamDetailResponse:
        # Keep the invariant at the service boundary as well as in the
        # request schema.  Background callers and tests can invoke the
        # service directly, so they must not be able to bypass the frozen
        # 10–100 question range.
        if not _MIN_QUESTION_COUNT <= int(data.question_count) <= _MAX_QUESTION_COUNT:
            raise ValidationException(
                f"考试题数必须在 {_MIN_QUESTION_COUNT}-{_MAX_QUESTION_COUNT} 之间"
            )
        async with get_db_ctx() as db:
            await self.practice._lock_user(db, user_id)
            now = self._now()
            existing = (
                await db.execute(
                    select(QuizExam)
                    .where(
                        QuizExam.user_id == user_id,
                        QuizExam.status == _IN_PROGRESS,
                    )
                    .order_by(QuizExam.started_at.desc(), QuizExam.id.desc())
                    .limit(1)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if existing is not None:
                if existing.deadline_at <= now:
                    await self._settle_locked(
                        db,
                        existing,
                        status=_TIMED_OUT,
                        settled_at=now,
                    )
                    await db.commit()
                else:
                    return await self._serialize_exam(db, existing, server_time=now)

            selections: list[tuple[str, int]] = []
            if data.scope_type is not None:
                assert data.scope_id is not None
                selections.append((str(data.scope_type), int(data.scope_id)))
            elif data.scopes is not None:
                selections.extend(
                    (str(item.scope_type), int(item.scope_id)) for item in data.scopes
                )
            is_v2 = bool(selections)
            if is_v2:
                resolved_library = None
                point_ids: list[int] = []
                path_by_point: dict[int, list] = {}
                for scope_type, scope_id in selections:
                    scope_library, scope_point_ids, scope_paths = await self.v2._resolve_scope(
                        db, user_id, scope_type, scope_id
                    )
                    if resolved_library is None:
                        resolved_library = scope_library
                    elif int(resolved_library.id) != int(scope_library.id):
                        raise ValidationException("智能组卷仅支持同一题库内的范围")
                    for point_id in scope_point_ids:
                        if point_id not in path_by_point:
                            point_ids.append(point_id)
                    for path_item in scope_paths:
                        path_by_point.setdefault(
                            int(path_item["point_id"]), path_item["path"]
                        )
                library = resolved_library
                rows = list(
                    (
                        await db.execute(
                            select(QuizQuestion, QuizQuestionRevision)
                            .join(
                                QuizQuestionRevision,
                                QuizQuestionRevision.id
                                == QuizQuestion.current_revision_id,
                            )
                            .where(
                                QuizQuestion.library_id == library.id,
                                QuizQuestion.knowledge_point_id.in_(point_ids),
                                QuizQuestion.status == _PUBLISHED,
                                QuizQuestionRevision.status == "published",
                            )
                            .order_by(func.random())
                            .limit(data.question_count)
                        )
                    ).all()
                )
                available = int(
                    (
                        await db.execute(
                            select(func.count(QuizQuestion.id)).where(
                                QuizQuestion.library_id == library.id,
                                QuizQuestion.knowledge_point_id.in_(point_ids),
                                QuizQuestion.status == _PUBLISHED,
                                QuizQuestion.current_revision_id.is_not(None),
                            )
                        )
                    ).scalar()
                    or 0
                )
                questions = [question for question, _revision in rows]
            else:
                assert data.category_id is not None
                categories = await self.practice._load_categories(db)
                by_id, scope_ids = self.practice._effective_category_ids(
                    categories,
                    data.category_id,
                )
                available = int(
                    (
                        await db.execute(
                            select(func.count(QuizQuestion.id)).where(
                                QuizQuestion.status == _PUBLISHED,
                                QuizQuestion.category_id.in_(scope_ids),
                            )
                        )
                    ).scalar()
                    or 0
                )
                questions = list(
                    (
                        await db.execute(
                            select(QuizQuestion)
                            .where(
                                QuizQuestion.status == _PUBLISHED,
                                QuizQuestion.category_id.in_(scope_ids),
                            )
                            .order_by(func.random())
                            .limit(data.question_count)
                        )
                    )
                    .scalars()
                    .all()
                )
                rows = [(question, None) for question in questions]
            if available < data.question_count or len(questions) < data.question_count:
                label = "当前范围" if is_v2 else "当前分类"
                raise BusinessException(
                    f"{label}最大可选数为 {available}，无法创建 {data.question_count} 题考试"
                )
            exam = QuizExam(
                user_id=user_id,
                category_id=data.category_id,
                library_id=(int(library.id) if is_v2 else None),
                # Multi-scope selections record the coarsest truthful scope
                # (the whole library) because ck_quiz_exam_scope requires a
                # complete V2 scope triple whenever library_id is present.
                scope_type=(
                    str(data.scope_type)
                    if data.scope_type is not None
                    else ("library" if is_v2 else None)
                ),
                scope_id=(
                    int(data.scope_id)
                    if data.scope_id is not None
                    else (int(library.id) if is_v2 else None)
                ),
                question_count=data.question_count,
                duration_seconds=_DURATION_SECONDS,
                status=_IN_PROGRESS,
                started_at=now,
                deadline_at=now + timedelta(seconds=_DURATION_SECONDS),
                lock_version=1,
            )
            db.add(exam)
            await db.flush()
            for position, (question, revision) in enumerate(rows, start=1):
                if revision is not None:
                    snapshot = {
                        "category_id": None,
                        "category_path": path_by_point[
                            int(question.knowledge_point_id)
                        ],
                        "question_type": revision.question_type,
                        "question_text": revision.question_text,
                        "options": dict(revision.options or {}),
                        "correct_answer": revision.correct_answer,
                        "explanation": revision.explanation or "",
                        "image_urls": list(revision.image_urls or []),
                        "option_image_urls": dict(getattr(revision, "option_image_urls", None) or {}),
                        "question_lock_version": int(question.lock_version),
                    }
                else:
                    snapshot = self.practice._question_snapshot(question, by_id)
                snapshot["options"] = dict(sorted((snapshot["options"] or {}).items()))
                db.add(
                    QuizExamQuestion(
                        exam_id=exam.id,
                        question_id=question.id,
                        question_revision_id=(revision.id if revision is not None else None),
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
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                winner = (
                    await db.execute(
                        select(QuizExam)
                        .where(
                            QuizExam.user_id == user_id,
                            QuizExam.status == _IN_PROGRESS,
                        )
                        .order_by(QuizExam.id.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if winner is None:
                    raise
                return await self._serialize_exam(db, winner, server_time=self._now())
            return await self._serialize_exam(db, exam, server_time=now)


    async def create_manual_exam(
        self,
        user_id: int,
        data: QuizManualExamCreate,
    ) -> QuizExamDetailResponse:
        """Create an exam from an explicit, validated question selection."""

        async with get_db_ctx() as db:
            await self.practice._lock_user(db, user_id)
            now = self._now()
            existing = (
                await db.execute(
                    select(QuizExam)
                    .where(
                        QuizExam.user_id == user_id,
                        QuizExam.status == _IN_PROGRESS,
                    )
                    .order_by(QuizExam.started_at.desc(), QuizExam.id.desc())
                    .limit(1)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if existing is not None:
                if existing.deadline_at <= now:
                    await self._settle_locked(
                        db,
                        existing,
                        status=_TIMED_OUT,
                        settled_at=now,
                    )
                    await db.commit()
                else:
                    return await self._serialize_exam(db, existing, server_time=now)

            rows = list(
                (
                    await db.execute(
                        select(QuizQuestion, QuizQuestionRevision)
                        .join(
                            QuizQuestionRevision,
                            QuizQuestionRevision.id
                            == QuizQuestion.current_revision_id,
                        )
                        .where(
                            QuizQuestion.id.in_(data.question_ids),
                            QuizQuestion.status == _PUBLISHED,
                            QuizQuestionRevision.status == "published",
                        )
                    )
                ).all()
            )
            by_question = {
                int(question.id): (question, revision)
                for question, revision in rows
            }
            missing = sorted(set(data.question_ids) - set(by_question))
            if missing:
                preview = ",".join(str(item) for item in missing[:5])
                raise BusinessException(
                    f"以下题目不存在或不可用：{preview}"
                    + ("…" if len(missing) > 5 else "")
                )
            library_ids = {
                int(question.library_id)
                for question, _revision in rows
                if question.library_id is not None
            }
            if len(library_ids) != 1 or any(
                question.library_id is None for question, _revision in rows
            ):
                raise ValidationException("手动组卷仅支持同一题库内的题目")
            library_id = library_ids.pop()
            library = await db.get(QuizLibrary, library_id)
            if library is None:
                raise NotFoundException("题库")
            entitled = (
                await db.execute(
                    select(QuizLibrary.id).where(
                        QuizLibrary.id == library_id,
                        self.v2._visible_clause(user_id),
                    )
                )
            ).scalar_one_or_none()
            if entitled is None:
                raise NotFoundException("题库")

            point_ids = sorted(
                {
                    int(question.knowledge_point_id)
                    for question, _revision in rows
                    if question.knowledge_point_id is not None
                }
            )
            point_rows = (
                await db.execute(
                    select(QuizKnowledgePoint, QuizModule)
                    .join(QuizModule, QuizModule.id == QuizKnowledgePoint.module_id)
                    .where(QuizKnowledgePoint.id.in_(point_ids))
                )
            ).all()
            path_by_point = {
                int(point.id): [
                    {"id": int(library.id), "name": library.name, "kind": "library"},
                    {"id": int(module.id), "name": module.name, "kind": "module"},
                    {
                        "id": int(point.id),
                        "name": point.name,
                        "kind": "knowledge_point",
                    },
                ]
                for point, module in point_rows
            }
            if any(
                int(question.knowledge_point_id) not in path_by_point
                for question, _revision in rows
            ):
                raise ValidationException("手动组卷包含不可用的知识点")

            exam = QuizExam(
                user_id=user_id,
                category_id=None,
                library_id=library_id,
                scope_type="library",
                scope_id=library_id,
                question_count=len(data.question_ids),
                duration_seconds=_DURATION_SECONDS,
                status=_IN_PROGRESS,
                started_at=now,
                deadline_at=now + timedelta(seconds=_DURATION_SECONDS),
                lock_version=1,
            )
            db.add(exam)
            await db.flush()
            for position, question_id in enumerate(data.question_ids, start=1):
                question, revision = by_question[int(question_id)]
                snapshot = {
                    "category_id": None,
                    "category_path": path_by_point[int(question.knowledge_point_id)],
                    "question_type": revision.question_type,
                    "question_text": revision.question_text,
                    "options": dict(revision.options or {}),
                    "correct_answer": revision.correct_answer,
                    "explanation": revision.explanation or "",
                    "image_urls": list(revision.image_urls or []),
                    "option_image_urls": dict(
                        getattr(revision, "option_image_urls", None) or {}
                    ),
                    "question_lock_version": int(question.lock_version),
                }
                snapshot["options"] = dict(sorted((snapshot["options"] or {}).items()))
                db.add(
                    QuizExamQuestion(
                        exam_id=exam.id,
                        question_id=question.id,
                        question_revision_id=revision.id,
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
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                winner = (
                    await db.execute(
                        select(QuizExam)
                        .where(
                            QuizExam.user_id == user_id,
                            QuizExam.status == _IN_PROGRESS,
                        )
                        .order_by(QuizExam.id.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if winner is None:
                    raise
                return await self._serialize_exam(db, winner, server_time=self._now())
            return await self._serialize_exam(db, exam, server_time=now)

    async def get_current_exam(self, user_id: int) -> QuizExamDetailResponse | None:
        async with get_db_ctx() as db:
            await self.practice._lock_user(db, user_id)
            now = self._now()
            exam = (
                await db.execute(
                    select(QuizExam)
                    .where(
                        QuizExam.user_id == user_id,
                        QuizExam.status == _IN_PROGRESS,
                    )
                    .order_by(QuizExam.started_at.desc(), QuizExam.id.desc())
                    .limit(1)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if exam is None:
                return None
            if exam.deadline_at <= now:
                await self._settle_locked(
                    db,
                    exam,
                    status=_TIMED_OUT,
                    settled_at=now,
                )
                await db.commit()
                return None
            return await self._serialize_exam(db, exam, server_time=now)

    async def list_exams(
        self,
        user_id: int,
        query: QuizExamListQuery,
    ) -> PaginatedData[QuizExamListItem]:
        async with get_db_ctx() as db:
            await self.practice._lock_user(db, user_id)
            now = self._now()
            active = list(
                (
                    await db.execute(
                        select(QuizExam)
                        .where(
                            QuizExam.user_id == user_id,
                            QuizExam.status == _IN_PROGRESS,
                            QuizExam.deadline_at <= now,
                        )
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            for exam in active:
                await self._settle_locked(
                    db,
                    exam,
                    status=_TIMED_OUT,
                    settled_at=now,
                )
            if active:
                await db.commit()
            statement = select(QuizExam).where(QuizExam.user_id == user_id)
            total = int(
                (
                    await db.execute(
                        select(func.count()).select_from(statement.subquery())
                    )
                ).scalar()
                or 0
            )
            rows = list(
                (
                    await db.execute(
                        statement.order_by(
                            QuizExam.started_at.desc(), QuizExam.id.desc()
                        )
                        .offset((query.page - 1) * query.page_size)
                        .limit(query.page_size)
                    )
                )
                .scalars()
                .all()
            )
            items = [
                QuizExamListItem(
                    id=int(exam.id),
                    **self._scope_fields(exam),
                    question_count=int(exam.question_count),
                    duration_seconds=_DURATION_SECONDS,
                    status=exam.status,
                    started_at=exam.started_at,
                    deadline_at=exam.deadline_at,
                    finished_at=self._finished_at(exam),
                    score=exam.score,
                )
                for exam in rows
            ]
            return PaginatedData(
                items=items,
                total=total,
                page=query.page,
                page_size=query.page_size,
            )

    async def get_exam(
        self,
        user_id: int,
        exam_id: int,
    ) -> QuizExamDetailResponse:
        async with get_db_ctx() as db:
            await self.practice._lock_user(db, user_id)
            exam = await self._get_exam(db, user_id, exam_id, lock=True)
            now = self._now()
            if exam.status == _IN_PROGRESS and exam.deadline_at <= now:
                await self._settle_locked(
                    db,
                    exam,
                    status=_TIMED_OUT,
                    settled_at=now,
                )
                await db.commit()
            return await self._serialize_exam(db, exam, server_time=now)

    async def save_answer(
        self,
        user_id: int,
        exam_id: int,
        exam_question_id: int,
        data: QuizExamAnswerSave,
    ) -> QuizExamAnswerSaved:
        async with get_db_ctx() as db:
            await self.practice._lock_user(db, user_id)
            exam = await self._get_exam(db, user_id, exam_id, lock=True)
            now = self._now()
            if exam.status == _IN_PROGRESS and exam.deadline_at <= now:
                await self._settle_locked(
                    db,
                    exam,
                    status=_TIMED_OUT,
                    settled_at=now,
                )
                await db.commit()
                raise ConflictException("考试已到期，系统已自动结算，不能保存答案")
            if exam.status != _IN_PROGRESS:
                raise ConflictException("当前考试已结束，不能保存答案")
            snapshot = (
                await db.execute(
                    select(QuizExamQuestion)
                    .where(
                        QuizExamQuestion.id == exam_question_id,
                        QuizExamQuestion.exam_id == exam_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if snapshot is None:
                raise NotFoundException("考试题目")
            normalized = self._normalize_answer(snapshot, data.user_answer)
            answer = (
                await db.execute(
                    select(QuizExamAnswer)
                    .where(
                        QuizExamAnswer.exam_id == exam_id,
                        QuizExamAnswer.exam_question_id == exam_question_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            expected_version = int(answer.lock_version) if answer else 0
            if data.lock_version != expected_version:
                raise ConflictException(
                    f"答案版本冲突，当前版本为 {expected_version}"
                )
            if answer is None:
                answer = QuizExamAnswer(
                    exam_id=exam_id,
                    exam_question_id=exam_question_id,
                    user_answer=normalized,
                    is_correct=None,
                    saved_at=now,
                    lock_version=1,
                )
                db.add(answer)
            else:
                answer.user_answer = normalized
                answer.is_correct = None
                answer.saved_at = now
                answer.lock_version += 1
            await db.commit()
            return QuizExamAnswerSaved(
                exam_id=exam_id,
                exam_question_id=exam_question_id,
                user_answer=answer.user_answer,
                lock_version=int(answer.lock_version),
                saved_at=answer.saved_at,
            )

    async def submit_exam(
        self,
        user_id: int,
        exam_id: int,
    ) -> QuizExamActionResponse:
        async with get_db_ctx() as db:
            await self.practice._lock_user(db, user_id)
            exam = await self._get_exam(db, user_id, exam_id, lock=True)
            if exam.status in {_COMPLETED, _TIMED_OUT}:
                return QuizExamActionResponse(
                    exam_id=int(exam.id),
                    status=exam.status,
                    finished_at=self._finished_at(exam),
                    score=exam.score,
                )
            if exam.status == _ABANDONED:
                raise ConflictException("已放弃的考试不能交卷")
            now = self._now()
            status = _TIMED_OUT if exam.deadline_at <= now else _COMPLETED
            await self._settle_locked(
                db,
                exam,
                status=status,
                settled_at=now,
            )
            await db.commit()
            return QuizExamActionResponse(
                exam_id=int(exam.id),
                status=exam.status,
                finished_at=self._finished_at(exam),
                score=exam.score,
            )

    async def abandon_exam(
        self,
        user_id: int,
        exam_id: int,
    ) -> QuizExamActionResponse:
        async with get_db_ctx() as db:
            await self.practice._lock_user(db, user_id)
            exam = await self._get_exam(db, user_id, exam_id, lock=True)
            if exam.status == _ABANDONED:
                return QuizExamActionResponse(
                    exam_id=int(exam.id),
                    status=_ABANDONED,
                    finished_at=exam.abandoned_at,
                    score=None,
                )
            if exam.status in {_COMPLETED, _TIMED_OUT}:
                raise ConflictException("已结算的考试不能放弃")
            now = self._now()
            if exam.deadline_at <= now:
                await self._settle_locked(
                    db,
                    exam,
                    status=_TIMED_OUT,
                    settled_at=now,
                )
                await db.commit()
                raise ConflictException("考试已到期，系统已自动结算，不能放弃")
            exam.status = _ABANDONED
            exam.abandoned_at = now
            exam.submitted_at = None
            exam.timed_out_at = None
            exam.lock_version += 1
            await db.commit()
            return QuizExamActionResponse(
                exam_id=int(exam.id),
                status=_ABANDONED,
                finished_at=now,
                score=None,
            )

    async def settle_expired_exams(
        self,
        *,
        limit: int = 100,
        now: datetime | None = None,
    ) -> int:
        if limit < 1:
            return 0
        cutoff = now or self._now()
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=timezone.utc)
        settled = 0
        async with get_db_ctx() as db:
            candidates = list(
                (
                    await db.execute(
                        select(QuizExam.id, QuizExam.user_id)
                        .where(
                            QuizExam.status == _IN_PROGRESS,
                            QuizExam.deadline_at <= cutoff,
                        )
                        .order_by(QuizExam.deadline_at.asc(), QuizExam.id.asc())
                        .limit(limit)
                    )
                ).all()
            )
            await db.rollback()
            for exam_id, owner_id in candidates:
                try:
                    await self._lock_user_any(db, int(owner_id))
                    exam = (
                        await db.execute(
                            select(QuizExam)
                            .where(
                                QuizExam.id == exam_id,
                                QuizExam.status == _IN_PROGRESS,
                                QuizExam.deadline_at <= cutoff,
                            )
                            .with_for_update(skip_locked=True)
                        )
                    ).scalar_one_or_none()
                    if exam is None:
                        await db.rollback()
                        continue
                    await self._settle_locked(
                        db,
                        exam,
                        status=_TIMED_OUT,
                        settled_at=cutoff,
                    )
                    await db.commit()
                    settled += 1
                except NotFoundException:
                    await db.rollback()
                except Exception:
                    # A malformed row or transient database failure must not
                    # prevent the remaining candidates from being processed.
                    # The failed exam stays in progress for the next worker
                    # tick; this per-row log keeps its id available for alerts.
                    await db.rollback()
                    logger.exception(
                        "quiz exam timeout settlement failed: exam_id=%s",
                        exam_id,
                    )
        return settled
