"""Admin manual review workflow for exams that contain essay questions."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.adapter.database import get_db_ctx
from app.domain.community.src.index import (
    QuizExam,
    QuizExamQuestion,
    QuizExamQuestionReview,
    QuizQuestionRevisionStats,
)
from app.port.exceptions import ConflictException, NotFoundException, ValidationException
from app.schemas.admin_quiz_review_contract import (
    AdminQuizReviewClaimResponse,
    AdminQuizReviewCompleteResponse,
    AdminQuizReviewDetail,
    AdminQuizReviewListItem,
    AdminQuizReviewListQuery,
    AdminQuizReviewQuestion,
    AdminQuizReviewSubmitRequest,
)
from app.schemas.common import PaginatedData
from app.services.quiz_exam import QuizExamService
from app.services.quiz_practice import QuizPracticeService


_ESSAY = "essay"
_VERDICT_RATIOS = {
    "wrong": Decimal(0),
    "partial": Decimal("0.5"),
    "correct": Decimal(1),
}


class AdminQuizReviewService:
    """Claim / verdict / complete / recall lifecycle for essay grading."""

    def __init__(self) -> None:
        self.exam_service = QuizExamService()
        self.practice = QuizPracticeService()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    async def list_reviews(
        self,
        query: AdminQuizReviewListQuery,
    ) -> PaginatedData[AdminQuizReviewListItem]:
        async with get_db_ctx() as db:
            statement = select(QuizExam).where(
                QuizExam.review_status.in_(["pending", "in_progress", "recalled"]),
                QuizExam.status.in_(["completed", "timed_out"]),
            )
            if query.status:
                statement = statement.where(QuizExam.review_status == query.status)
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
                        statement.order_by(QuizExam.id.desc())
                        .offset((query.page - 1) * query.page_size)
                        .limit(query.page_size)
                    )
                )
                .scalars()
                .all()
            )
            items = [
                AdminQuizReviewListItem(
                    exam_id=int(exam.id),
                    user_id=int(exam.user_id),
                    status=exam.status,
                    review_status=exam.review_status,
                    review_locked_by=(
                        int(exam.review_locked_by)
                        if exam.review_locked_by is not None
                        else None
                    ),
                    question_count=int(exam.question_count),
                    submitted_at=exam.submitted_at,
                    timed_out_at=exam.timed_out_at,
                )
                for exam in rows
            ]
            return PaginatedData(
                items=items,
                total=total,
                page=query.page,
                page_size=query.page_size,
            )

    async def _get_reviewable_exam(self, db, exam_id: int, *, lock: bool = False):
        statement = select(QuizExam).where(QuizExam.id == exam_id)
        if lock:
            statement = statement.with_for_update()
        exam = (await db.execute(statement)).scalar_one_or_none()
        if exam is None:
            raise NotFoundException("考试")
        if exam.status not in {"completed", "timed_out"}:
            raise ConflictException("考试尚未交卷，不能评阅")
        if exam.review_status == "none":
            raise ConflictException("该考试没有问答题，无需人工评阅")
        if exam.review_status == "completed":
            raise ConflictException("该考试已评阅完成")
        return exam

    async def _load_active_reviews(self, db, exam_id: int) -> dict[int, object]:
        rows = list(
            (
                await db.execute(
                    select(QuizExamQuestionReview).where(
                        QuizExamQuestionReview.exam_id == exam_id,
                        QuizExamQuestionReview.status == "active",
                    )
                )
            )
            .scalars()
            .all()
        )
        return {int(row.exam_question_id): row for row in rows}

    async def claim_review(
        self,
        exam_id: int,
        *,
        admin_id: int,
    ) -> AdminQuizReviewClaimResponse:
        async with get_db_ctx() as db:
            exam = await self._get_reviewable_exam(db, exam_id, lock=True)
            if (
                exam.review_status == "in_progress"
                and exam.review_locked_by is not None
                and exam.review_locked_by != admin_id
            ):
                raise ConflictException("该考试已被其他管理员领取评阅")
            exam.review_status = "in_progress"
            exam.review_locked_by = admin_id
            exam.review_locked_at = self._now()
            exam.lock_version += 1
            await db.commit()
            return AdminQuizReviewClaimResponse(
                exam_id=exam_id,
                review_status="in_progress",
                review_locked_by=admin_id,
            )

    async def get_review_detail(
        self,
        exam_id: int,
        *,
        admin_id: int,
    ) -> AdminQuizReviewDetail:
        async with get_db_ctx() as db:
            exam = await self._get_reviewable_exam(db, exam_id)
            if exam.review_locked_by != admin_id:
                raise ConflictException("请先领取该考试的评阅任务")
            snapshots = await self.exam_service._load_snapshots(db, exam_id)
            answers = await self.exam_service._load_answers(db, exam_id)
            reviews = await self._load_active_reviews(db, exam_id)
            questions = []
            for snapshot in snapshots:
                if snapshot.question_type != _ESSAY:
                    continue
                answer = answers.get(int(snapshot.id))
                review = reviews.get(int(snapshot.id))
                questions.append(
                    AdminQuizReviewQuestion(
                        exam_question_id=int(snapshot.id),
                        position=int(snapshot.position),
                        question_text=snapshot.question_text,
                        reference_answer=str(snapshot.correct_answer or ""),
                        explanation=snapshot.explanation,
                        image_urls=list(snapshot.image_urls or []),
                        user_answer=(
                            str(answer.user_answer) if answer is not None else None
                        ),
                        answered=(
                            answer is not None and answer.user_answer not in ("", [])
                        ),
                        verdict=(review.verdict if review else None),
                        comment=(review.comment if review else None),
                    )
                )
            return AdminQuizReviewDetail(
                exam_id=exam_id,
                user_id=int(exam.user_id),
                status=exam.status,
                review_status=exam.review_status,
                question_count=int(exam.question_count),
                submitted_at=exam.submitted_at,
                timed_out_at=exam.timed_out_at,
                questions=questions,
            )

    async def submit_verdicts(
        self,
        exam_id: int,
        data: AdminQuizReviewSubmitRequest,
        *,
        admin_id: int,
    ) -> AdminQuizReviewClaimResponse:
        async with get_db_ctx() as db:
            exam = await self._get_reviewable_exam(db, exam_id, lock=True)
            if exam.review_locked_by != admin_id:
                raise ConflictException("请先领取该考试的评阅任务")
            snapshots = {
                int(row.id): row
                for row in await self.exam_service._load_snapshots(db, exam_id)
                if row.question_type == _ESSAY
            }
            reviews = await self._load_active_reviews(db, exam_id)
            for item in data.verdicts:
                if item.exam_question_id not in snapshots:
                    raise ValidationException("评阅题目不属于该考试的问答题")
                existing = reviews.get(item.exam_question_id)
                if existing is not None:
                    existing.verdict = item.verdict
                    existing.comment = item.comment
                    existing.reviewer_id = admin_id
                    existing.lock_version += 1
                else:
                    db.add(
                        QuizExamQuestionReview(
                            exam_id=exam_id,
                            exam_question_id=item.exam_question_id,
                            reviewer_id=admin_id,
                            verdict=item.verdict,
                            comment=item.comment,
                            status="active",
                            lock_version=1,
                        )
                    )
            await db.commit()
            return AdminQuizReviewClaimResponse(
                exam_id=exam_id,
                review_status=exam.review_status,
                review_locked_by=exam.review_locked_by,
            )

    async def _bump_revision_stats(
        self,
        db,
        *,
        snapshot: QuizExamQuestion,
        correct: bool,
        occurred_at: datetime,
    ) -> None:
        if snapshot.question_revision_id is None:
            return
        statement = pg_insert(QuizQuestionRevisionStats).values(
            question_id=snapshot.question_id,
            question_revision_id=snapshot.question_revision_id,
            practice_first_attempts=0,
            practice_first_correct=0,
            exam_answers=1,
            exam_correct=1 if correct else 0,
            aggregated_through=occurred_at,
        )
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=[QuizQuestionRevisionStats.question_revision_id],
                set_={
                    "exam_answers": QuizQuestionRevisionStats.exam_answers + 1,
                    "exam_correct": QuizQuestionRevisionStats.exam_correct
                    + (1 if correct else 0),
                    "aggregated_through": occurred_at,
                },
            )
        )

    async def complete_review(
        self,
        exam_id: int,
        *,
        admin_id: int,
    ) -> AdminQuizReviewCompleteResponse:
        async with get_db_ctx() as db:
            exam = await self._get_reviewable_exam(db, exam_id, lock=True)
            if exam.review_locked_by != admin_id:
                raise ConflictException("请先领取该考试的评阅任务")
            snapshots = await self.exam_service._load_snapshots(db, exam_id, lock=True)
            answers = await self.exam_service._load_answers(db, exam_id, lock=True)
            reviews = await self._load_active_reviews(db, exam_id)
            essay_ids = {
                int(snapshot.id)
                for snapshot in snapshots
                if snapshot.question_type == _ESSAY
            }
            missing = sorted(essay_ids - set(reviews))
            if missing:
                raise ValidationException(
                    f"还有 {len(missing)} 道问答题未评分，不能完成评阅"
                )

            correct_count = 0
            partial_count = 0
            wrong_count = 0
            unanswered_count = 0
            ratio_sum = Decimal(0)
            finalized_at = self._now()
            for snapshot in snapshots:
                answer = answers.get(int(snapshot.id))
                if snapshot.question_type == _ESSAY:
                    review = reviews[int(snapshot.id)]
                    ratio = _VERDICT_RATIOS[review.verdict]
                    empty_answer = answer is None or answer.user_answer in ("", [])
                    if empty_answer:
                        unanswered_count += 1
                        ratio = Decimal(0)
                    elif ratio == 1:
                        correct_count += 1
                    elif ratio > 0:
                        partial_count += 1
                    else:
                        wrong_count += 1
                    if answer is not None:
                        answer.is_correct = ratio == 1
                        answer.score_ratio = ratio
                    ratio_sum += ratio
                    await self._bump_revision_stats(
                        db,
                        snapshot=snapshot,
                        correct=ratio == 1,
                        occurred_at=finalized_at,
                    )
                else:
                    if answer is None:
                        unanswered_count += 1
                        continue
                    ratio = Decimal(answer.score_ratio or 0)
                    ratio_sum += ratio
                    if ratio == 1:
                        correct_count += 1
                    elif ratio > 0:
                        partial_count += 1
                    else:
                        wrong_count += 1

            stats = await self.practice._ensure_stats(db, int(exam.user_id))
            stats.exam_total_questions += len(snapshots)
            stats.exam_correct += correct_count
            stats.exam_partial += partial_count
            stats.exam_wrong += wrong_count
            stats.exam_unanswered += unanswered_count
            score = QuizExamService._score_ratio(ratio_sum, len(snapshots))
            stats.exam_score_sum = Decimal(stats.exam_score_sum or 0) + score
            if stats.exam_high_score is None or score > stats.exam_high_score:
                stats.exam_high_score = score
            stats.exam_latest_score = score
            stats.exam_latest_at = finalized_at
            if exam.status == "completed":
                stats.completed_exam_count += 1
            else:
                stats.timed_out_exam_count += 1

            exam.review_status = "completed"
            exam.correct_count = correct_count
            exam.wrong_count = wrong_count
            exam.unanswered_count = unanswered_count
            exam.score = score
            exam.lock_version += 1
            for snapshot in snapshots:
                if snapshot.question_type != _ESSAY:
                    continue
                answer = answers.get(int(snapshot.id))
                ratio = Decimal(answer.score_ratio or 0) if answer else Decimal(0)
                if ratio < 1:
                    await self.practice.apply_settled_exam_wrong_book(
                        db,
                        user_id=int(exam.user_id),
                        snapshot=snapshot,
                        is_correct=False,
                        settled_at=finalized_at,
                        stats=stats,
                    )
            await db.commit()
            return AdminQuizReviewCompleteResponse(
                exam_id=exam_id,
                review_status="completed",
                score=score,
                correct_count=correct_count,
                partial_count=partial_count,
                wrong_count=wrong_count,
                unanswered_count=unanswered_count,
            )

    async def recall_review(
        self,
        exam_id: int,
        *,
        admin_id: int,
        is_super_admin: bool,
    ) -> AdminQuizReviewClaimResponse:
        async with get_db_ctx() as db:
            exam = (
                await db.execute(
                    select(QuizExam).where(QuizExam.id == exam_id).with_for_update()
                )
            ).scalar_one_or_none()
            if exam is None:
                raise NotFoundException("考试")
            if exam.review_status != "completed":
                raise ConflictException("仅已完成的评阅可以撤回")
            original_reviewer = exam.review_locked_by
            if not is_super_admin and original_reviewer != admin_id:
                raise ConflictException("仅超级管理员或原评阅人可以撤回评分")

            snapshots = await self.exam_service._load_snapshots(db, exam_id)
            answers = await self.exam_service._load_answers(db, exam_id, lock=True)
            reviews = list(
                (
                    await db.execute(
                        select(QuizExamQuestionReview)
                        .where(QuizExamQuestionReview.exam_id == exam_id)
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            score = Decimal(exam.score or 0)
            stats = await self.practice._ensure_stats(db, int(exam.user_id))
            partial = sum(
                1
                for answer in answers.values()
                if answer.score_ratio is not None
                and Decimal(0) < Decimal(answer.score_ratio) < Decimal(1)
            )
            stats.exam_total_questions -= len(snapshots)
            stats.exam_correct -= int(exam.correct_count or 0)
            stats.exam_partial -= partial
            stats.exam_wrong -= int(exam.wrong_count or 0)
            stats.exam_unanswered -= int(exam.unanswered_count or 0)
            stats.exam_score_sum = Decimal(stats.exam_score_sum or 0) - score
            if exam.status == "completed":
                stats.completed_exam_count -= 1
            else:
                stats.timed_out_exam_count -= 1
            # High/latest scores stay: the historical maximum remains true and
            # the next completion refreshes the latest score again.

            exam.review_status = "recalled"
            exam.review_locked_by = None
            exam.review_locked_at = None
            exam.correct_count = None
            exam.wrong_count = None
            exam.unanswered_count = None
            exam.score = None
            exam.lock_version += 1
            now = self._now()
            for review in reviews:
                if review.status == "active":
                    review.status = "superseded"
                    review.superseded_at = now
                    review.lock_version += 1
            for snapshot in snapshots:
                if snapshot.question_type != _ESSAY:
                    continue
                answer = answers.get(int(snapshot.id))
                if answer is not None:
                    answer.is_correct = None
                    answer.score_ratio = None
            await db.commit()
            return AdminQuizReviewClaimResponse(
                exam_id=exam_id,
                review_status="recalled",
                review_locked_by=None,
            )
