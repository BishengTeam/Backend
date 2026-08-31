"""Course-bound assignment configuration, submission and manual review flows."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapter.database import get_db_ctx
from app.domain.certification.src.index import Course
from app.domain.community.src.index import (
    QuizAdminAuditLog,
    QuizCourseAssignment,
    QuizCourseAssignmentAnswer,
    QuizCourseAssignmentQuestion,
    QuizCourseAssignmentReviewLog,
    QuizCourseAssignmentSubmission,
    QuizCourseAssignmentSubmissionQuestion,
    QuizCourseAssignmentVersion,
    QuizCourseLibraryBinding,
    QuizKnowledgePoint,
    QuizLibrary,
    QuizLibraryEntitlement,
    QuizModule,
    QuizQuestion,
)
from app.domain.community.src.rule.quiz import (
    QuizQuestionType,
    answers_match,
    normalize_submitted_answer,
)
from app.domain.user.src.index import User, UserProfile, UserRealname
from app.port.exceptions import (
    BusinessException,
    ConflictException,
    ForbiddenException,
    NotFoundException,
    ValidationException,
)
from app.schemas.common import PaginatedData
from app.schemas.course_assignment import (
    AdminCourseAssignmentResultQuestion,
    AdminCourseAssignmentSubmissionDetail,
    CourseAssignmentAnswerInput,
    CourseAssignmentAnswerSaved,
    CourseAssignmentCreate,
    CourseAssignmentEssayScoreInput,
    CourseAssignmentQuestionResponse,
    CourseAssignmentResponse,
    CourseAssignmentReviewLogItem,
    CourseAssignmentReviewSave,
    CourseAssignmentReviewSaved,
    CourseAssignmentSubmissionListItem,
    CourseAssignmentSubmissionQuery,
    CourseAssignmentSubmitResponse,
    CourseAssignmentUpdate,
    CourseAssignmentWithdrawResponse,
    UserCourseAssignmentDetail,
    UserCourseAssignmentListItem,
    UserCourseAssignmentQuestion,
    UserCourseAssignmentResultQuestion,
)
from app.services.quiz_image_upload import sign_quiz_media_map, sign_quiz_media_urls


_TOTAL_CENTS = 10000
_CENT = Decimal("0.01")
_SCORE_TOTAL = Decimal("100.00")


class CourseAssignmentService:
    """Admin configuration APIs for course-bound assignments."""

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _locked(db: AsyncSession, model, object_id: int):
        return (
            db.execute(select(model).where(model.id == object_id).with_for_update())
        ).scalar_one_or_none()

    @staticmethod
    def _check_version(row, expected_version: int) -> None:
        if int(row.lock_version) != int(expected_version):
            raise ConflictException("内容已变化，请刷新后重试")

    @staticmethod
    def _audit(
        db: AsyncSession,
        *,
        admin_id: int,
        action: str,
        object_type: str,
        object_id: int | None,
        before: dict[str, object] | None = None,
        after: dict[str, object] | None = None,
        permission: str = "course_assignment_manage",
    ) -> None:
        db.add(
            QuizAdminAuditLog(
                actor_type="admin",
                admin_id=admin_id,
                permission=permission,
                action=action,
                object_type=object_type,
                object_id=object_id,
                result="succeeded",
                changed_fields={"before": before or {}, "after": after or {}},
            )
        )

    @staticmethod
    async def _active_binding(
        db: AsyncSession, course_id: int, library_id: int
    ) -> tuple[Course, QuizLibrary, QuizCourseLibraryBinding]:
        row = (
            await db.execute(
                select(Course, QuizLibrary, QuizCourseLibraryBinding)
                .join(
                    QuizCourseLibraryBinding,
                    and_(
                        QuizCourseLibraryBinding.course_id == Course.id,
                        QuizCourseLibraryBinding.library_id == QuizLibrary.id,
                        QuizCourseLibraryBinding.course_id == course_id,
                        QuizCourseLibraryBinding.library_id == library_id,
                        QuizCourseLibraryBinding.status == "active",
                    ),
                )
                .where(Course.id == course_id, QuizLibrary.id == library_id)
            )
        ).one_or_none()
        if row is None:
            raise NotFoundException("课程题库绑定")
        course, library, binding = row
        if course.status != "published":
            raise BusinessException("课程必须处于已发布状态")
        if library.access_mode != "course_entitlement":
            raise BusinessException("题库必须是课程权益模式")
        if library.status != "published" or not library.v2_enabled:
            raise BusinessException("题库未开放用户入口")
        return course, library, binding

    @staticmethod
    async def _published_questions(
        db: AsyncSession, library_id: int
    ) -> list[QuizQuestion]:
        return list(
            (
                await db.execute(
                    select(QuizQuestion)
                    .join(
                        QuizKnowledgePoint,
                        QuizKnowledgePoint.id == QuizQuestion.knowledge_point_id,
                    )
                    .join(QuizModule, QuizModule.id == QuizKnowledgePoint.module_id)
                    .where(
                        QuizQuestion.library_id == library_id,
                        QuizQuestion.status == "published",
                        QuizQuestion.current_revision_id.is_not(None),
                        QuizKnowledgePoint.status == "active",
                        QuizKnowledgePoint.system_kind == "none",
                        QuizModule.status == "active",
                        QuizModule.system_kind == "none",
                    )
                    .order_by(
                        QuizModule.sort_order.asc(),
                        QuizModule.id.asc(),
                        QuizKnowledgePoint.sort_order.asc(),
                        QuizKnowledgePoint.id.asc(),
                        QuizQuestion.id.asc(),
                    )
                )
            ).scalars()
        )

    @staticmethod
    def _question_snapshot(question: QuizQuestion) -> dict[str, object]:
        return {
            "question_id": int(question.id),
            "library_id": int(question.library_id or 0),
            "knowledge_point_id": (
                int(question.knowledge_point_id)
                if question.knowledge_point_id is not None
                else None
            ),
            "question_revision_id": (
                int(question.current_revision_id)
                if question.current_revision_id is not None
                else None
            ),
            "question_type": str(question.question_type),
            "question_text": question.question_text,
            "options": dict(question.options or {}),
            "option_image_urls": dict(question.option_image_urls or {}),
            "correct_answer": question.correct_answer,
            "reference_answer": question.reference_answer,
            "explanation": question.explanation or "",
            "image_urls": list(question.image_urls or []),
        }

    @staticmethod
    def _config_signature(
        questions: Sequence[QuizQuestion], score_by_id: dict[int, Decimal]
    ) -> list[dict[str, object]]:
        return [
            {
                "question_id": int(question.id),
                "question_type": str(question.question_type),
                "snapshot": CourseAssignmentService._question_snapshot(question),
                "score": f"{score_by_id[int(question.id)]:.2f}",
            }
            for question in questions
        ]

    @staticmethod
    def _allocate_scores(
        questions: Sequence[QuizQuestion],
        essay_scores: Sequence[CourseAssignmentEssayScoreInput] | None,
        previous_scores: dict[int, Decimal],
    ) -> dict[int, Decimal]:
        essays = [
            question
            for question in questions
            if question.question_type == QuizQuestionType.ESSAY.value
        ]
        objectives = [
            question
            for question in questions
            if question.question_type != QuizQuestionType.ESSAY.value
        ]
        if not questions:
            raise ValidationException("题库中没有可用的已发布题目")

        supplied = {int(item.question_id): item.score for item in essay_scores or []}
        if essay_scores is not None:
            supplied_ids = set(supplied)
            essay_ids = {int(question.id) for question in essays}
            if supplied_ids != essay_ids:
                missing = sorted(essay_ids - supplied_ids)
                extra = sorted(supplied_ids - essay_ids)
                details = []
                if missing:
                    details.append(f"缺少问答题 {','.join(map(str, missing))}")
                if extra:
                    details.append(f"非问答题 {','.join(map(str, extra))}")
                raise ValidationException("问答题分值配置无效：" + "；".join(details))
        else:
            supplied = {
                int(question.id): previous_scores.get(int(question.id), Decimal(0))
                for question in essays
            }
            if essays and any(score <= 0 for score in supplied.values()):
                raise ValidationException("请为每道问答题配置大于 0 的分值")

        essay_cents = 0
        for question in essays:
            score = supplied[int(question.id)]
            if score <= 0 or score > 100:
                raise ValidationException("问答题分值必须在 0 到 100 之间")
            cents = int((score * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            if cents % 10 != 0:
                raise ValidationException("问答题分值最多支持 1 位小数")
            essay_cents += cents

        if essay_cents > _TOTAL_CENTS:
            raise ValidationException("问答题总分不能超过 100 分")
        remaining_cents = _TOTAL_CENTS - essay_cents
        if not objectives and remaining_cents != 0:
            raise ValidationException("没有客观题时，问答题总分必须等于 100 分")

        result: dict[int, Decimal] = {}
        for question in essays:
            cents = int(
                (supplied[int(question.id)] * 100)
                .quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            )
            result[int(question.id)] = Decimal(cents) / 100

        if objectives:
            base = remaining_cents // len(objectives)
            remainder = remaining_cents - base * len(objectives)
            for index, question in enumerate(objectives):
                cents = base
                if index == len(objectives) - 1:
                    cents += remainder
                result[int(question.id)] = Decimal(cents) / 100

        if sum((int(value * 100) for value in result.values()), start=0) != _TOTAL_CENTS:
            raise ValidationException("作业总分必须等于 100 分")
        return result

    async def _sync_config(
        db: AsyncSession,
        assignment: QuizCourseAssignment,
        *,
        essay_scores: Sequence[CourseAssignmentEssayScoreInput] | None = None,
        admin_id: int | None = None,
    ) -> bool:
        questions = await self._published_questions(db, int(assignment.library_id))
        existing = list(
            (
                await db.execute(
                    select(QuizCourseAssignmentQuestion).where(
                        QuizCourseAssignmentQuestion.assignment_id == assignment.id
                    )
                )
            ).scalars()
        )
        previous = {
            int(item.question_id): Decimal(item.score) for item in existing
        }
        scores = self._allocate_scores(questions, essay_scores, previous)
        next_signature = self._config_signature(questions, scores)
        current_signature = [
            {
                "question_id": int(item.question_id),
                "question_type": str(item.snapshot.get("question_type")),
                "snapshot": dict(item.snapshot),
                "score": f"{Decimal(item.score):.2f}",
            }
            for item in sorted(existing, key=lambda row: int(row.position))
        ]
        if next_signature == current_signature and assignment.question_count == len(
            questions
        ):
            return False

        await db.execute(
            delete(QuizCourseAssignmentQuestion).where(
                QuizCourseAssignmentQuestion.assignment_id == assignment.id
            )
        )
        await db.flush()

        for position, question in enumerate(questions, start=1):
            db.add(
                QuizCourseAssignmentQuestion(
                    assignment_id=assignment.id,
                    question_id=question.id,
                    position=position,
                    is_essay=question.question_type == QuizQuestionType.ESSAY.value,
                    score=scores[int(question.id)],
                    snapshot=self._question_snapshot(question),
                )
            )

        essays = [
            question
            for question in questions
            if question.question_type == QuizQuestionType.ESSAY.value
        ]
        objectives = [
            question
            for question in questions
            if question.question_type != QuizQuestionType.ESSAY.value
        ]
        essay_total = sum(
            (scores[int(question.id)] for question in essays),
            start=Decimal("0.00"),
        ).quantize(_CENT)
        objective_total = (
            _SCORE_TOTAL - essay_total
        ).quantize(_CENT)
        assignment.version_no = int(assignment.version_no) + 1
        assignment.question_count = len(questions)
        assignment.essay_count = len(essays)
        assignment.objective_count = len(objectives)
        assignment.essay_total_score = essay_total
        assignment.objective_total_score = objective_total
        assignment.total_score = _SCORE_TOTAL
        assignment.updated_by = admin_id
        assignment.lock_version = int(assignment.lock_version) + 1

        current_rows = list(
            (
                await db.execute(
                    select(QuizCourseAssignmentQuestion).where(
                        QuizCourseAssignmentQuestion.assignment_id == assignment.id
                    )
                )
            ).scalars()
        )
        db.add(
            QuizCourseAssignmentVersion(
                assignment_id=assignment.id,
                version_no=assignment.version_no,
                question_count=len(questions),
                essay_count=len(essays),
                objective_count=len(objectives),
                essay_total_score=essay_total,
                objective_total_score=objective_total,
                total_score=_SCORE_TOTAL,
                config_snapshot=[
                    {
                        **dict(row.snapshot),
                        "position": int(row.position),
                        "score": f"{Decimal(row.score):.2f}",
                        "is_essay": bool(row.is_essay),
                    }
                    for row in sorted(current_rows, key=lambda item: item.position)
                ],
                created_by=admin_id,
            )
        )
        return True

    @staticmethod
    async def _course_ids(db: AsyncSession, library_id: int) -> list[int]:
        return sorted(
            int(item)
            for item in await db.scalars(
                select(QuizCourseLibraryBinding.course_id).where(
                    QuizCourseLibraryBinding.library_id == library_id,
                    QuizCourseLibraryBinding.status == "active",
                )
            )
        )

    async def _response(
        self, db: AsyncSession, assignment: QuizCourseAssignment
    ) -> CourseAssignmentResponse:
        library = await db.get(QuizLibrary, assignment.library_id)
        if library is None:
            raise NotFoundException("题库")
        questions = list(
            (
                await db.execute(
                    select(QuizCourseAssignmentQuestion)
                    .where(
                        QuizCourseAssignmentQuestion.assignment_id == assignment.id
                    )
                    .order_by(
                        QuizCourseAssignmentQuestion.position,
                        QuizCourseAssignmentQuestion.id,
                    )
                )
            ).scalars()
        )
        return CourseAssignmentResponse(
            id=int(assignment.id),
            library_id=int(assignment.library_id),
            library_name=library.name,
            library_code=library.library_code,
            course_ids=await self._course_ids(db, int(assignment.library_id)),
            status=assignment.status,  # type: ignore[arg-type]
            version_no=int(assignment.version_no),
            question_count=int(assignment.question_count),
            essay_count=int(assignment.essay_count),
            objective_count=int(assignment.objective_count),
            essay_total_score=Decimal(assignment.essay_total_score).quantize(_CENT),
            objective_total_score=Decimal(assignment.objective_total_score).quantize(
                _CENT
            ),
            total_score=Decimal(assignment.total_score).quantize(_CENT),
            published_at=assignment.published_at,
            disabled_at=assignment.disabled_at,
            lock_version=int(assignment.lock_version),
            questions=[
                CourseAssignmentQuestionResponse(
                    question_id=int(row.question_id),
                    position=int(row.position),
                    question_type=str(row.snapshot["question_type"]),  # type: ignore[arg-type]
                    question_text=str(row.snapshot["question_text"]),
                    options=dict(row.snapshot.get("options") or {}),
                    option_image_urls=sign_quiz_media_map(
                        row.snapshot.get("option_image_urls") or {}
                    ),
                    image_urls=sign_quiz_media_urls(
                        list(row.snapshot.get("image_urls") or [])
                    ),
                    explanation=row.snapshot.get("explanation"),
                    reference_answer=row.snapshot.get("reference_answer"),
                    score=Decimal(row.score).quantize(_CENT),
                    is_essay=bool(row.is_essay),
                )
                for row in questions
            ],
        )

    async def list_assignments(
        self,
        *,
        course_id: int | None = None,
        library_id: int | None = None,
        status: str | None = None,
    ) -> list[CourseAssignmentResponse]:
        async with get_db_ctx() as db:
            stmt = select(QuizCourseAssignment)
            if course_id is not None:
                stmt = stmt.where(
                    QuizCourseAssignment.library_id.in_(
                        select(QuizCourseLibraryBinding.library_id).where(
                            QuizCourseLibraryBinding.course_id == course_id,
                            QuizCourseLibraryBinding.status == "active",
                        )
                    )
                )
            if library_id is not None:
                stmt = stmt.where(QuizCourseAssignment.library_id == library_id)
            if status is not None:
                stmt = stmt.where(QuizCourseAssignment.status == status)
            assignments = list(
                (
                    await db.execute(
                        stmt.order_by(
                            QuizCourseAssignment.updated_at.desc(),
                            QuizCourseAssignment.id.desc(),
                        )
                    )
                ).scalars()
            )
            return [await self._response(db, item) for item in assignments]

    async def get_assignment(self, assignment_id: int) -> CourseAssignmentResponse:
        async with get_db_ctx() as db:
            assignment = await db.get(QuizCourseAssignment, assignment_id)
            if assignment is None:
                raise NotFoundException("课程作业")
            return await self._response(db, assignment)

    async def create_assignment(
        self, data: CourseAssignmentCreate, *, admin_id: int
    ) -> CourseAssignmentResponse:
        async with get_db_ctx() as db:
            await self._active_binding(db, data.course_id, data.library_id)
            existing = (
                await db.execute(
                    select(QuizCourseAssignment)
                    .where(QuizCourseAssignment.library_id == data.library_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if existing is not None:
                raise ConflictException("该题库已存在课程作业配置")
            assignment = QuizCourseAssignment(
                library_id=data.library_id,
                status="draft",
                version_no=0,
                question_count=0,
                essay_count=0,
                objective_count=0,
                essay_total_score=Decimal("0.00"),
                objective_total_score=Decimal("0.00"),
                total_score=_SCORE_TOTAL,
                lock_version=1,
                created_by=admin_id,
                updated_by=admin_id,
            )
            db.add(assignment)
            await db.flush()
            await self._sync_config(
                db, assignment, essay_scores=data.essay_scores, admin_id=admin_id
            )
            self._audit(
                db,
                admin_id=admin_id,
                action="course_assignment.create",
                object_type="course_assignment",
                object_id=int(assignment.id),
                before={},
                after={"library_id": int(assignment.library_id)},
            )
            await db.commit()
            await db.refresh(assignment)
            return await self._response(db, assignment)

    async def update_assignment(
        self,
        assignment_id: int,
        data: CourseAssignmentUpdate,
        *,
        admin_id: int,
    ) -> CourseAssignmentResponse:
        async with get_db_ctx() as db:
            assignment = await self._locked(db, QuizCourseAssignment, assignment_id)
            if assignment is None:
                raise NotFoundException("课程作业")
            self._check_version(assignment, data.lock_version)
            before = {
                "version_no": int(assignment.version_no),
                "question_count": int(assignment.question_count),
                "essay_total_score": str(assignment.essay_total_score),
                "status": assignment.status,
            }
            await self._sync_config(
                db,
                assignment,
                essay_scores=data.essay_scores,
                admin_id=admin_id,
            )
            self._audit(
                db,
                admin_id=admin_id,
                action="course_assignment.update",
                object_type="course_assignment",
                object_id=assignment_id,
                before=before,
                after={
                    "version_no": int(assignment.version_no),
                    "question_count": int(assignment.question_count),
                    "essay_total_score": str(assignment.essay_total_score),
                    "status": assignment.status,
                },
            )
            await db.commit()
            await db.refresh(assignment)
            return await self._response(db, assignment)

    async def publish_assignment(
        self, assignment_id: int, *, lock_version: int, admin_id: int
    ) -> CourseAssignmentResponse:
        async with get_db_ctx() as db:
            assignment = await self._locked(db, QuizCourseAssignment, assignment_id)
            if assignment is None:
                raise NotFoundException("课程作业")
            self._check_version(assignment, lock_version)
            if assignment.status == "published":
                raise BusinessException("作业已是发布状态")
            library = await db.get(QuizLibrary, assignment.library_id)
            if library is None or library.status != "published" or not library.v2_enabled:
                raise BusinessException("题库未开放用户入口")
            active_binding = (
                await db.execute(
                    select(QuizCourseLibraryBinding.id)
                    .where(
                        QuizCourseLibraryBinding.library_id == assignment.library_id,
                        QuizCourseLibraryBinding.status == "active",
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if active_binding is None:
                raise BusinessException("课程权益题库至少需要一个有效课程绑定")
            await self._sync_config(db, assignment, admin_id=admin_id)
            before = {"status": assignment.status}
            now = self._now()
            assignment.status = "published"
            assignment.published_at = now
            assignment.disabled_at = None
            assignment.lock_version = int(assignment.lock_version) + 1
            assignment.updated_by = admin_id
            self._audit(
                db,
                admin_id=admin_id,
                action="course_assignment.publish",
                object_type="course_assignment",
                object_id=assignment_id,
                before=before,
                after={"status": assignment.status},
            )
            await db.commit()
            await db.refresh(assignment)
            return await self._response(db, assignment)

    async def disable_assignment(
        self, assignment_id: int, *, lock_version: int, admin_id: int
    ) -> CourseAssignmentResponse:
        async with get_db_ctx() as db:
            assignment = await self._locked(db, QuizCourseAssignment, assignment_id)
            if assignment is None:
                raise NotFoundException("课程作业")
            self._check_version(assignment, lock_version)
            if assignment.status != "published":
                raise BusinessException("仅已发布作业可以停用")
            before = {"status": assignment.status}
            assignment.status = "disabled"
            assignment.disabled_at = self._now()
            assignment.lock_version = int(assignment.lock_version) + 1
            assignment.updated_by = admin_id
            self._audit(
                db,
                admin_id=admin_id,
                action="course_assignment.disable",
                object_type="course_assignment",
                object_id=assignment_id,
                before=before,
                after={"status": assignment.status},
            )
            await db.commit()
            await db.refresh(assignment)
            return await self._response(db, assignment)


class UserCourseAssignmentService:
    """Student-facing assignment APIs."""

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _display_status(
        submission: QuizCourseAssignmentSubmission | None,
    ) -> str:
        if submission is None or submission.status == "draft":
            return "draft" if submission is not None else "not_started"
        if submission.status == "submitted":
            return "submitted"
        if submission.status == "claimed":
            return "reviewing"
        return "graded"

    @staticmethod
    async def _locked_assignment(
        db: AsyncSession, assignment_id: int
    ) -> QuizCourseAssignment:
        assignment = (
            await db.execute(
                select(QuizCourseAssignment)
                .where(QuizCourseAssignment.id == assignment_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if assignment is None:
            raise NotFoundException("课程作业")
        return assignment

    @staticmethod
    async def _require_access(
        db: AsyncSession, user_id: int, assignment: QuizCourseAssignment
    ) -> QuizLibrary:
        library = await db.get(QuizLibrary, assignment.library_id)
        if library is None:
            raise NotFoundException("题库")
        if library.access_mode != "course_entitlement":
            raise ForbiddenException("题库访问模式无效")
        now = UserCourseAssignmentService._now()
        entitled = (
            await db.execute(
                select(QuizLibraryEntitlement.id)
                .where(
                    QuizLibraryEntitlement.user_id == user_id,
                    QuizLibraryEntitlement.library_id == assignment.library_id,
                    QuizLibraryEntitlement.status == "active",
                    QuizLibraryEntitlement.starts_at <= now,
                    or_(
                        QuizLibraryEntitlement.ends_at.is_(None),
                        QuizLibraryEntitlement.ends_at > now,
                    ),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if entitled is None:
            raise ForbiddenException("暂无该课程题库权益")
        return library

    async def _ensure_current_config(
        self,
        db: AsyncSession,
        assignment: QuizCourseAssignment,
    ) -> None:
        # Question edits are reflected for unsubmitted students. Submitted rows
        # have their own immutable per-submission snapshots.
        await CourseAssignmentService()._sync_config(db, assignment, admin_id=None)

    @staticmethod
    async def _submission(
        db: AsyncSession,
        assignment_id: int,
        user_id: int,
        *,
        lock: bool = False,
    ) -> QuizCourseAssignmentSubmission | None:
        stmt = select(QuizCourseAssignmentSubmission).where(
            QuizCourseAssignmentSubmission.assignment_id == assignment_id,
            QuizCourseAssignmentSubmission.user_id == user_id,
        )
        if lock:
            stmt = stmt.with_for_update()
        return (await db.execute(stmt)).scalar_one_or_none()

    async def list_assignments(
        self, user_id: int, *, course_id: int | None = None
    ) -> list[UserCourseAssignmentListItem]:
        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None or not user.is_active:
                raise NotFoundException("用户")
            now = self._now()
            stmt = (
                select(QuizCourseAssignment, QuizLibrary)
                .join(QuizLibrary, QuizLibrary.id == QuizCourseAssignment.library_id)
                .join(
                    QuizLibraryEntitlement,
                    QuizLibraryEntitlement.library_id == QuizLibrary.id,
                )
                .where(
                    QuizCourseAssignment.status == "published",
                    QuizLibrary.status == "published",
                    QuizLibrary.v2_enabled.is_(True),
                    QuizLibrary.access_mode == "course_entitlement",
                    QuizLibraryEntitlement.user_id == user_id,
                    QuizLibraryEntitlement.library_id == QuizLibrary.id,
                    QuizLibraryEntitlement.status == "active",
                    QuizLibraryEntitlement.starts_at <= now,
                    or_(
                        QuizLibraryEntitlement.ends_at.is_(None),
                        QuizLibraryEntitlement.ends_at > now,
                    ),
                )
            )
            if course_id is not None:
                stmt = stmt.where(
                    QuizCourseAssignment.library_id.in_(
                        select(QuizCourseLibraryBinding.library_id).where(
                            QuizCourseLibraryBinding.course_id == course_id,
                            QuizCourseLibraryBinding.status == "active",
                        )
                    )
                )
            rows = list((await db.execute(stmt.order_by(QuizCourseAssignment.id))).all())
            submissions = {
                int(item.assignment_id): item
                for item in (
                    await db.execute(
                        select(QuizCourseAssignmentSubmission).where(
                            QuizCourseAssignmentSubmission.user_id == user_id
                        )
                    )
                ).scalars()
            }
            result: list[UserCourseAssignmentListItem] = []
            for assignment, library in rows:
                submission = submissions.get(int(assignment.id))
                result.append(
                    UserCourseAssignmentListItem(
                        assignment_id=int(assignment.id),
                        library_id=int(library.id),
                        library_name=library.name,
                        library_code=library.library_code,
                        description=library.description or "",
                        cover_url=library.cover_url or "",
                        question_count=int(assignment.question_count),
                        essay_count=int(assignment.essay_count),
                        status=submission.status if submission else None,  # type: ignore[arg-type]
                        display_status=self._display_status(submission),  # type: ignore[arg-type]
                        can_withdraw=(
                            submission is not None and submission.status == "submitted"
                        ),
                        total_score=(
                            Decimal(submission.total_score).quantize(_CENT)
                            if submission is not None
                            and submission.status == "graded"
                            and submission.total_score is not None
                            else None
                        ),
                        submitted_at=submission.submitted_at if submission else None,
                        graded_at=submission.graded_at if submission else None,
                    )
                )
            return result

    @staticmethod
    def _current_question_response(
        row: QuizCourseAssignmentQuestion,
        answer: QuizCourseAssignmentAnswer | None,
    ) -> UserCourseAssignmentResultQuestion:
        snapshot = row.snapshot
        raw_answer = answer.user_answer if answer is not None else None
        return UserCourseAssignmentResultQuestion(
            question_id=int(row.question_id),
            position=int(row.position),
            question_type=str(snapshot["question_type"]),  # type: ignore[arg-type]
            question_text=str(snapshot["question_text"]),
            options=dict(snapshot.get("options") or {}),
            option_image_urls=sign_quiz_media_map(
                snapshot.get("option_image_urls") or {}
            ),
            image_urls=sign_quiz_media_urls(list(snapshot.get("image_urls") or [])),
            score=Decimal(row.score).quantize(_CENT),
            is_essay=bool(row.is_essay),
            user_answer=raw_answer,  # type: ignore[arg-type]
            is_answered=bool(answer.is_answered) if answer is not None else False,
        )

    async def _detail_locked(
        self,
        db: AsyncSession,
        user_id: int,
        assignment: QuizCourseAssignment,
        *,
        create_draft: bool,
    ) -> UserCourseAssignmentDetail:
        submission = await self._submission(
            db, int(assignment.id), user_id, lock=True
        )
        library: QuizLibrary | None = None
        if submission is None or submission.status == "draft":
            library = await self._require_access(db, user_id, assignment)
            await self._ensure_current_config(db, assignment)
        else:
            # Submitted snapshots and graded history remain readable after an
            # entitlement or assignment lifecycle change.
            library = await db.get(QuizLibrary, assignment.library_id)
            if library is None:
                raise NotFoundException("题库")
        assert library is not None
        if submission is None:
            if not create_draft:
                raise NotFoundException("课程作业记录")
            if assignment.status != "published":
                raise BusinessException("作业未发布或已停用")
            submission = QuizCourseAssignmentSubmission(
                assignment_id=assignment.id,
                user_id=user_id,
                status="draft",
                config_version_no=int(assignment.version_no),
                lock_version=1,
            )
            db.add(submission)
            await db.flush()
        elif submission.status == "draft":
            submission.config_version_no = int(assignment.version_no)

        if submission.status == "draft":
            current_rows = list(
                (
                    await db.execute(
                        select(QuizCourseAssignmentQuestion)
                        .where(
                            QuizCourseAssignmentQuestion.assignment_id
                            == assignment.id
                        )
                        .order_by(
                            QuizCourseAssignmentQuestion.position,
                            QuizCourseAssignmentQuestion.id,
                        )
                    )
                ).scalars()
            )
            current_ids = {int(row.question_id) for row in current_rows}
            draft_rows = list(
                (
                    await db.execute(
                        select(QuizCourseAssignmentAnswer).where(
                            QuizCourseAssignmentAnswer.submission_id
                            == submission.id
                        )
                    )
                ).scalars()
            )
            for row in draft_rows:
                if int(row.question_id) not in current_ids:
                    await db.delete(row)
            await db.flush()
            answers = {
                int(row.question_id): row
                for row in (
                    await db.execute(
                        select(QuizCourseAssignmentAnswer).where(
                            QuizCourseAssignmentAnswer.submission_id == submission.id
                        )
                    )
                ).scalars()
            }
            questions = [
                self._current_question_response(row, answers.get(int(row.question_id)))
                for row in current_rows
            ]
        else:
            result_rows = list(
                (
                    await db.execute(
                        select(QuizCourseAssignmentSubmissionQuestion)
                        .where(
                            QuizCourseAssignmentSubmissionQuestion.submission_id
                            == submission.id
                        )
                        .order_by(
                            QuizCourseAssignmentSubmissionQuestion.position,
                            QuizCourseAssignmentSubmissionQuestion.id,
                        )
                    )
                ).scalars()
            )
            graded = submission.status == "graded"
            questions = [
                UserCourseAssignmentResultQuestion(
                    question_id=int(row.question_id),
                    position=int(row.position),
                    question_type=str(row.snapshot["question_type"]),  # type: ignore[arg-type]
                    question_text=str(row.snapshot["question_text"]),
                    options=dict(row.snapshot.get("options") or {}),
                    option_image_urls=sign_quiz_media_map(
                        row.snapshot.get("option_image_urls") or {}
                    ),
                    image_urls=sign_quiz_media_urls(
                        list(row.snapshot.get("image_urls") or [])
                    ),
                    score=Decimal(row.score).quantize(_CENT),
                    is_essay=bool(row.is_essay),
                    user_answer=row.user_answer,  # type: ignore[arg-type]
                    is_answered=bool(row.is_answered),
                    correct_answer=(
                        row.snapshot.get("correct_answer")  # type: ignore[arg-type]
                        if graded
                        else None
                    ),
                    explanation=str(row.snapshot.get("explanation") or "")
                    if graded
                    else None,
                    earned_score=(
                        Decimal(row.earned_score).quantize(_CENT)
                        if graded and row.earned_score is not None
                        else None
                    ),
                    manual_score=(
                        Decimal(row.manual_score).quantize(_CENT)
                        if graded and row.manual_score is not None
                        else None
                    ),
                    review_comment=row.review_comment if graded else None,
                    requires_review=bool(row.requires_review),
                )
                for row in result_rows
            ]

        await db.commit()
        return UserCourseAssignmentDetail(
            assignment_id=int(assignment.id),
            library_id=int(assignment.library_id),
            library_name=library.name,
            status=submission.status,  # type: ignore[arg-type]
            display_status=self._display_status(submission),  # type: ignore[arg-type]
            assignment_status=assignment.status,  # type: ignore[arg-type]
            config_version_no=int(
                submission.config_version_no
                if submission.status != "draft"
                else assignment.version_no
            ),
            can_edit=submission.status == "draft" and assignment.status == "published",
            can_submit=(
                submission.status == "draft" and assignment.status == "published"
            ),
            can_withdraw=submission.status == "submitted",
            result_available=submission.status == "graded",
            total_score=(
                Decimal(submission.total_score).quantize(_CENT)
                if submission.status == "graded"
                and submission.total_score is not None
                else None
            ),
            submitted_at=submission.submitted_at,
            graded_at=submission.graded_at,
            questions=questions,
        )

    async def start_assignment(
        self, user_id: int, assignment_id: int
    ) -> UserCourseAssignmentDetail:
        async with get_db_ctx() as db:
            assignment = await self._locked_assignment(db, assignment_id)
            return await self._detail_locked(
                db, user_id, assignment, create_draft=True
            )

    async def get_assignment(
        self, user_id: int, assignment_id: int
    ) -> UserCourseAssignmentDetail:
        async with get_db_ctx() as db:
            assignment = await self._locked_assignment(db, assignment_id)
            return await self._detail_locked(
                db, user_id, assignment, create_draft=False
            )

    async def save_answers(
        self,
        user_id: int,
        assignment_id: int,
        answers: Sequence[CourseAssignmentAnswerInput],
    ) -> CourseAssignmentAnswerSaved:
        async with get_db_ctx() as db:
            assignment = await self._locked_assignment(db, assignment_id)
            await self._require_access(db, user_id, assignment)
            if assignment.status != "published":
                raise BusinessException("作业未发布或已停用")
            await self._ensure_current_config(db, assignment)
            submission = await self._submission(
                db, assignment_id, user_id, lock=True
            )
            if submission is None:
                raise NotFoundException("课程作业记录")
            if submission.status != "draft":
                raise BusinessException("作业已提交，不能修改答案")
            submission.config_version_no = int(assignment.version_no)

            rows = {
                int(row.question_id): row
                for row in (
                    await db.execute(
                        select(QuizCourseAssignmentQuestion).where(
                            QuizCourseAssignmentQuestion.assignment_id == assignment.id
                        )
                    )
                ).scalars()
            }
            existing = {
                int(row.question_id): row
                for row in (
                    await db.execute(
                        select(QuizCourseAssignmentAnswer).where(
                            QuizCourseAssignmentAnswer.submission_id == submission.id
                        )
                    )
                ).scalars()
            }
            for item in answers:
                config = rows.get(item.question_id)
                if config is None:
                    raise ValidationException(f"题目 {item.question_id} 不在当前作业中")
                snapshot = config.snapshot
                question_type = str(snapshot["question_type"])
                if item.user_answer is None or item.user_answer == "":
                    normalized = None
                    answered = False
                elif question_type == QuizQuestionType.ESSAY.value:
                    if not isinstance(item.user_answer, str):
                        raise ValidationException("问答题答案必须是文本")
                    text = item.user_answer.strip()
                    if not text:
                        normalized = None
                        answered = False
                    else:
                        normalized = text
                        answered = True
                        if len(normalized) > 5000:
                            raise ValidationException("问答题答案不能超过 5000 字")
                else:
                    try:
                        normalized = normalize_submitted_answer(
                            question_type,
                            item.user_answer,
                            options=dict(snapshot.get("options") or {}),
                        )
                    except Exception as exc:
                        raise ValidationException(f"题目 {item.question_id} 答案无效") from exc
                    answered = True

                row = existing.get(item.question_id)
                if row is None:
                    row = QuizCourseAssignmentAnswer(
                        submission_id=submission.id,
                        question_id=item.question_id,
                        user_answer=normalized,
                        is_answered=answered,
                    )
                    db.add(row)
                else:
                    row.user_answer = normalized
                    row.is_answered = answered
            submission.lock_version = int(submission.lock_version) + 1
            await db.commit()
            return CourseAssignmentAnswerSaved(
                assignment_id=assignment_id,
                saved_count=len(answers),
                config_version_no=int(assignment.version_no),
            )

    async def submit_assignment(
        self, user_id: int, assignment_id: int
    ) -> CourseAssignmentSubmitResponse:
        async with get_db_ctx() as db:
            assignment = await self._locked_assignment(db, assignment_id)
            await self._require_access(db, user_id, assignment)
            if assignment.status != "published":
                raise BusinessException("作业未发布或已停用")
            await self._ensure_current_config(db, assignment)
            submission = await self._submission(
                db, assignment_id, user_id, lock=True
            )
            if submission is None:
                raise NotFoundException("课程作业记录")
            if submission.status != "draft":
                raise BusinessException("作业已提交，不能重复提交")

            config_rows = list(
                (
                    await db.execute(
                        select(QuizCourseAssignmentQuestion)
                        .where(
                            QuizCourseAssignmentQuestion.assignment_id
                            == assignment.id
                        )
                        .order_by(
                            QuizCourseAssignmentQuestion.position,
                            QuizCourseAssignmentQuestion.id,
                        )
                    )
                ).scalars()
            )
            answers = {
                int(row.question_id): row
                for row in (
                    await db.execute(
                        select(QuizCourseAssignmentAnswer).where(
                            QuizCourseAssignmentAnswer.submission_id == submission.id
                        )
                    )
                ).scalars()
            }
            await db.execute(
                delete(QuizCourseAssignmentSubmissionQuestion).where(
                    QuizCourseAssignmentSubmissionQuestion.submission_id
                    == submission.id
                )
            )
            requires_manual = False
            total = Decimal("0.00")
            now = self._now()
            for config in config_rows:
                answer = answers.get(int(config.question_id))
                snapshot = dict(config.snapshot)
                raw_answer = answer.user_answer if answer is not None else None
                answered = bool(answer is not None and answer.is_answered)
                objective_correct: bool | None = None
                manual_score: Decimal | None = None
                earned_score: Decimal | None = None
                requires_review = bool(config.is_essay and answered)
                if config.is_essay:
                    if answered:
                        requires_manual = True
                    else:
                        earned_score = Decimal("0.00")
                        manual_score = Decimal("0.00")
                else:
                    try:
                        objective_correct = (
                            answers_match(
                                str(snapshot["question_type"]),
                                raw_answer,
                                snapshot.get("correct_answer"),
                                options=dict(snapshot.get("options") or {}),
                            )
                            if answered
                            else False
                        )
                    except Exception as exc:
                        raise ValidationException(
                            f"题目 {config.question_id} 答案无效，请修正后提交"
                        ) from exc
                    earned_score = (
                        Decimal(config.score).quantize(_CENT)
                        if objective_correct
                        else Decimal("0.00")
                    )
                    total = (total + earned_score).quantize(_CENT)

                db.add(
                    QuizCourseAssignmentSubmissionQuestion(
                        submission_id=submission.id,
                        question_id=config.question_id,
                        position=config.position,
                        is_essay=config.is_essay,
                        is_answered=answered,
                        requires_review=requires_review,
                        score=Decimal(config.score).quantize(_CENT),
                        snapshot=snapshot,
                        user_answer=raw_answer if answered else None,
                        is_objective_correct=objective_correct,
                        manual_score=manual_score,
                        earned_score=earned_score,
                    )
                )

            submission.config_version_no = int(assignment.version_no)
            submission.submitted_at = now
            submission.status = "submitted" if requires_manual else "graded"
            submission.graded_at = now if not requires_manual else None
            submission.total_score = None if requires_manual else total
            submission.lock_version = int(submission.lock_version) + 1
            await db.commit()
            return CourseAssignmentSubmitResponse(
                assignment_id=assignment_id,
                status=submission.status,  # type: ignore[arg-type]
                display_status=self._display_status(submission),  # type: ignore[arg-type]
                total_score=(
                    Decimal(submission.total_score).quantize(_CENT)
                    if submission.total_score is not None
                    else None
                ),
                submitted_at=now,
            )

    async def withdraw_assignment(
        self, user_id: int, assignment_id: int
    ) -> CourseAssignmentWithdrawResponse:
        async with get_db_ctx() as db:
            assignment = await self._locked_assignment(db, assignment_id)
            await self._require_access(db, user_id, assignment)
            submission = await self._submission(
                db, assignment_id, user_id, lock=True
            )
            if submission is None:
                raise NotFoundException("课程作业记录")
            if submission.status != "submitted":
                raise BusinessException("作业已被领取评阅或已评分，不能撤回")
            await db.execute(
                delete(QuizCourseAssignmentSubmissionQuestion).where(
                    QuizCourseAssignmentSubmissionQuestion.submission_id
                    == submission.id
                )
            )
            submission.status = "draft"
            submission.config_version_no = int(assignment.version_no)
            submission.submitted_at = None
            submission.lock_version = int(submission.lock_version) + 1
            await db.commit()
            return CourseAssignmentWithdrawResponse(
                assignment_id=assignment_id, status="draft"
            )


class CourseAssignmentReviewService:
    """Admin manual review APIs."""

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    async def _locked_submission(
        db: AsyncSession, submission_id: int
    ) -> QuizCourseAssignmentSubmission:
        submission = (
            await db.execute(
                select(QuizCourseAssignmentSubmission)
                .where(QuizCourseAssignmentSubmission.id == submission_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if submission is None:
            raise NotFoundException("作业提交记录")
        return submission

    @staticmethod
    def _check_version(submission: QuizCourseAssignmentSubmission, expected: int):
        if int(submission.lock_version) != int(expected):
            raise ConflictException("评分内容已变化，请刷新后重试")

    @staticmethod
    async def _course_ids(db: AsyncSession, library_id: int) -> list[int]:
        return sorted(
            int(item)
            for item in await db.scalars(
                select(QuizCourseLibraryBinding.course_id).where(
                    QuizCourseLibraryBinding.library_id == library_id,
                    QuizCourseLibraryBinding.status == "active",
                )
            )
        )

    async def list_submissions(
        self, query: CourseAssignmentSubmissionQuery
    ) -> PaginatedData[CourseAssignmentSubmissionListItem]:
        async with get_db_ctx() as db:
            base = (
                select(
                    QuizCourseAssignmentSubmission,
                    QuizCourseAssignment,
                    QuizLibrary,
                    User,
                    UserProfile,
                    UserRealname,
                )
                .join(
                    QuizCourseAssignment,
                    QuizCourseAssignment.id
                    == QuizCourseAssignmentSubmission.assignment_id,
                )
                .join(QuizLibrary, QuizLibrary.id == QuizCourseAssignment.library_id)
                .join(User, User.id == QuizCourseAssignmentSubmission.user_id)
                .outerjoin(UserProfile, UserProfile.user_id == User.id)
                .outerjoin(UserRealname, UserRealname.user_id == User.id)
            )
            if query.assignment_id is not None:
                base = base.where(
                    QuizCourseAssignmentSubmission.assignment_id
                    == query.assignment_id
                )
            if query.library_id is not None:
                base = base.where(QuizCourseAssignment.library_id == query.library_id)
            if query.course_id is not None:
                base = base.where(
                    QuizCourseAssignment.library_id.in_(
                        select(QuizCourseLibraryBinding.library_id).where(
                            QuizCourseLibraryBinding.course_id == query.course_id,
                            QuizCourseLibraryBinding.status == "active",
                        )
                    )
                )
            if query.status is not None:
                base = base.where(
                    QuizCourseAssignmentSubmission.status == str(query.status)
                )
            else:
                base = base.where(QuizCourseAssignmentSubmission.status != "draft")
            if query.keyword:
                term = f"%{query.keyword.strip()}%"
                maybe_id = query.keyword.strip().isdigit()
                base = base.where(
                    or_(
                        UserProfile.nickname.ilike(term),
                        UserRealname.real_name.ilike(term),
                        User.phone.ilike(term),
                        User.id == int(query.keyword.strip()) if maybe_id else False,
                    )
                )
            base = base.order_by(
                QuizCourseAssignmentSubmission.submitted_at.asc().nulls_last(),
                QuizCourseAssignmentSubmission.id.asc(),
            )
            total = int(
                await db.scalar(
                    select(func.count()).select_from(base.subquery())
                )
                or 0
            )
            rows = list(
                (
                    await db.execute(
                        base.offset((query.page - 1) * query.page_size).limit(
                            query.page_size
                        )
                    )
                ).all()
            )
            items: list[CourseAssignmentSubmissionListItem] = []
            for row in rows:
                submission, assignment, library, user, profile, realname = row
                items.append(
                    CourseAssignmentSubmissionListItem(
                        id=int(submission.id),
                        assignment_id=int(assignment.id),
                        library_id=int(library.id),
                        library_name=library.name,
                        course_ids=await self._course_ids(db, int(library.id)),
                        user_id=int(user.id),
                        student_name=(
                            realname.real_name
                            or (profile.nickname if profile else None)
                            or f"用户{user.id}"
                        ),
                        student_phone=user.phone or (profile.phone if profile else None),
                        status=submission.status,  # type: ignore[arg-type]
                        config_version_no=int(submission.config_version_no),
                        submitted_at=submission.submitted_at,
                        claimed_at=submission.claimed_at,
                        graded_at=submission.graded_at,
                        total_score=(
                            Decimal(submission.total_score).quantize(_CENT)
                            if submission.total_score is not None
                            else None
                        ),
                        lock_version=int(submission.lock_version),
                    )
                )
            return PaginatedData(
                items=items,
                total=total,
                page=query.page,
                page_size=query.page_size,
            )

    async def get_submission(
        self, submission_id: int
    ) -> AdminCourseAssignmentSubmissionDetail:
        async with get_db_ctx() as db:
            submission = await db.get(
                QuizCourseAssignmentSubmission, submission_id
            )
            if submission is None:
                raise NotFoundException("作业提交记录")
            assignment = await db.get(QuizCourseAssignment, submission.assignment_id)
            library = (
                await db.get(QuizLibrary, assignment.library_id)
                if assignment is not None
                else None
            )
            if assignment is None or library is None:
                raise NotFoundException("课程作业")
            user = await db.get(User, submission.user_id)
            profile = (
                await db.execute(
                    select(UserProfile).where(UserProfile.user_id == submission.user_id)
                )
            ).scalar_one_or_none()
            realname = (
                await db.execute(
                    select(UserRealname).where(
                        UserRealname.user_id == submission.user_id
                    )
                )
            ).scalar_one_or_none()
            rows = list(
                (
                    await db.execute(
                        select(QuizCourseAssignmentSubmissionQuestion)
                        .where(
                            QuizCourseAssignmentSubmissionQuestion.submission_id
                            == submission.id
                        )
                        .order_by(
                            QuizCourseAssignmentSubmissionQuestion.position,
                            QuizCourseAssignmentSubmissionQuestion.id,
                        )
                    )
                ).scalars()
            )
            return AdminCourseAssignmentSubmissionDetail(
                id=int(submission.id),
                assignment_id=int(assignment.id),
                library_id=int(library.id),
                library_name=library.name,
                course_ids=await self._course_ids(db, int(library.id)),
                user_id=int(submission.user_id),
                student_name=(
                    (realname.real_name if realname else None)
                    or (profile.nickname if profile else None)
                    or (f"用户{submission.user_id}" if user else "未知用户")
                ),
                student_phone=(
                    (user.phone if user else None)
                    or (profile.phone if profile else None)
                ),
                status=submission.status,  # type: ignore[arg-type]
                config_version_no=int(submission.config_version_no),
                submitted_at=submission.submitted_at,
                claimed_by=submission.claimed_by,
                claimed_at=submission.claimed_at,
                graded_by=submission.graded_by,
                graded_at=submission.graded_at,
                total_score=(
                    Decimal(submission.total_score).quantize(_CENT)
                    if submission.total_score is not None
                    else None
                ),
                lock_version=int(submission.lock_version),
                questions=[
                    AdminCourseAssignmentResultQuestion(
                        submission_question_id=int(row.id),
                        question_id=int(row.question_id),
                        position=int(row.position),
                        question_type=str(row.snapshot["question_type"]),  # type: ignore[arg-type]
                        question_text=str(row.snapshot["question_text"]),
                        options=dict(row.snapshot.get("options") or {}),
                        option_image_urls=sign_quiz_media_map(
                            row.snapshot.get("option_image_urls") or {}
                        ),
                        image_urls=sign_quiz_media_urls(
                            list(row.snapshot.get("image_urls") or [])
                        ),
                        score=Decimal(row.score).quantize(_CENT),
                        is_essay=bool(row.is_essay),
                        user_answer=row.user_answer,  # type: ignore[arg-type]
                        is_answered=bool(row.is_answered),
                        correct_answer=row.snapshot.get("correct_answer"),  # type: ignore[arg-type]
                        explanation=str(row.snapshot.get("explanation") or ""),
                        earned_score=(
                            Decimal(row.earned_score).quantize(_CENT)
                            if row.earned_score is not None
                            else None
                        ),
                        manual_score=(
                            Decimal(row.manual_score).quantize(_CENT)
                            if row.manual_score is not None
                            else None
                        ),
                        review_comment=row.review_comment,
                        requires_review=bool(row.requires_review),
                        reference_answer=row.snapshot.get("reference_answer"),
                        is_objective_correct=row.is_objective_correct,
                    )
                    for row in rows
                ],
            )

    async def claim(
        self,
        submission_id: int,
        *,
        lock_version: int,
        admin_id: int,
    ) -> CourseAssignmentReviewSaved:
        async with get_db_ctx() as db:
            submission = await self._locked_submission(db, submission_id)
            self._check_version(submission, lock_version)
            if submission.status != "submitted":
                raise BusinessException("仅待领取作业可以领取")
            now = self._now()
            submission.status = "claimed"
            submission.claimed_by = admin_id
            submission.claimed_at = now
            submission.lock_version = int(submission.lock_version) + 1
            db.add(
                QuizCourseAssignmentReviewLog(
                    submission_id=submission.id,
                    actor_type="admin",
                    admin_id=admin_id,
                    action="claim",
                    before={"status": "submitted"},
                    after={"status": "claimed"},
                )
            )
            await db.commit()
            return CourseAssignmentReviewSaved(
                submission_id=submission_id,
                status=submission.status,  # type: ignore[arg-type]
                lock_version=int(submission.lock_version),
            )

    async def save_review(
        self,
        submission_id: int,
        data: CourseAssignmentReviewSave,
        *,
        admin_id: int,
    ) -> CourseAssignmentReviewSaved:
        async with get_db_ctx() as db:
            submission = await self._locked_submission(db, submission_id)
            self._check_version(submission, data.lock_version)
            if submission.status != "claimed":
                raise BusinessException("请先领取作业再评分")
            rows = {
                int(row.id): row
                for row in (
                    await db.execute(
                        select(QuizCourseAssignmentSubmissionQuestion).where(
                            QuizCourseAssignmentSubmissionQuestion.submission_id
                            == submission.id,
                            QuizCourseAssignmentSubmissionQuestion.requires_review.is_(
                                True
                            ),
                        )
                    )
                ).scalars()
            }
            before = {
                int(row.id): {
                    "manual_score": str(row.manual_score) if row.manual_score is not None else None,
                    "review_comment": row.review_comment,
                }
                for row in rows.values()
            }
            for item in data.scores:
                row = rows.get(item.submission_question_id)
                if row is None:
                    raise ValidationException("评分题目无效或不需要人工评阅")
                if item.score < 0 or item.score > Decimal(row.score):
                    raise ValidationException("评分不能超过题目满分")
                row.manual_score = item.score.quantize(_CENT)
                row.review_comment = item.comment

            missing = [row.id for row in rows.values() if row.manual_score is None]
            if data.complete and missing:
                raise ValidationException("还有问答题未评分")

            if data.complete:
                total = Decimal("0.00")
                for row in (
                    await db.execute(
                        select(QuizCourseAssignmentSubmissionQuestion).where(
                            QuizCourseAssignmentSubmissionQuestion.submission_id
                            == submission.id
                        )
                    )
                ).scalars():
                    if row.requires_review:
                        row.earned_score = (
                            Decimal(row.manual_score or 0).quantize(_CENT)
                        )
                    total = (
                        total + Decimal(row.earned_score or 0)
                    ).quantize(_CENT)
                submission.status = "graded"
                submission.graded_at = self._now()
                submission.graded_by = admin_id
                submission.total_score = total
            submission.lock_version = int(submission.lock_version) + 1
            db.add(
                QuizCourseAssignmentReviewLog(
                    submission_id=submission.id,
                    actor_type="admin",
                    admin_id=admin_id,
                    action="complete" if data.complete else "save",
                    before=before,
                    after={
                        int(row.id): {
                            "manual_score": str(row.manual_score) if row.manual_score is not None else None,
                            "review_comment": row.review_comment,
                        }
                        for row in rows.values()
                    },
                )
            )
            await db.commit()
            return CourseAssignmentReviewSaved(
                submission_id=submission_id,
                status=submission.status,  # type: ignore[arg-type]
                total_score=(
                    Decimal(submission.total_score).quantize(_CENT)
                    if submission.total_score is not None
                    else None
                ),
                lock_version=int(submission.lock_version),
            )

    async def reopen(
        self,
        submission_id: int,
        *,
        lock_version: int,
        admin_id: int,
    ) -> CourseAssignmentReviewSaved:
        async with get_db_ctx() as db:
            submission = await self._locked_submission(db, submission_id)
            self._check_version(submission, lock_version)
            if submission.status != "graded":
                raise BusinessException("仅已评分作业可以撤回评阅")
            before = {
                "status": submission.status,
                "total_score": str(submission.total_score)
                if submission.total_score is not None
                else None,
            }
            rows = list(
                (
                    await db.execute(
                        select(QuizCourseAssignmentSubmissionQuestion).where(
                            QuizCourseAssignmentSubmissionQuestion.submission_id
                            == submission.id
                        )
                    )
                ).scalars()
            )
            for row in rows:
                if row.requires_review:
                    row.earned_score = None
            submission.status = "claimed"
            submission.claimed_by = admin_id
            submission.claimed_at = self._now()
            submission.graded_at = None
            submission.graded_by = None
            submission.total_score = None
            submission.lock_version = int(submission.lock_version) + 1
            db.add(
                QuizCourseAssignmentReviewLog(
                    submission_id=submission.id,
                    actor_type="admin",
                    admin_id=admin_id,
                    action="reopen",
                    before=before,
                    after={"status": "claimed", "total_score": None},
                )
            )
            await db.commit()
            return CourseAssignmentReviewSaved(
                submission_id=submission_id,
                status=submission.status,  # type: ignore[arg-type]
                lock_version=int(submission.lock_version),
            )

    async def list_logs(
        self, submission_id: int
    ) -> list[CourseAssignmentReviewLogItem]:
        async with get_db_ctx() as db:
            submission = await db.get(
                QuizCourseAssignmentSubmission, submission_id
            )
            if submission is None:
                raise NotFoundException("作业提交记录")
            rows = list(
                (
                    await db.execute(
                        select(QuizCourseAssignmentReviewLog)
                        .where(QuizCourseAssignmentReviewLog.submission_id == submission_id)
                        .order_by(
                            QuizCourseAssignmentReviewLog.created_at.desc(),
                            QuizCourseAssignmentReviewLog.id.desc(),
                        )
                    )
                ).scalars()
            )
            return [
                CourseAssignmentReviewLogItem(
                    id=int(row.id),
                    action=row.action,  # type: ignore[arg-type]
                    admin_id=row.admin_id,
                    before=row.before,
                    after=row.after,
                    created_at=row.created_at,
                )
                for row in rows
            ]
