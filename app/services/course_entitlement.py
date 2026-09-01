from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapter.database import get_db_ctx
from app.domain.certification.src.index import (
    Course,
    CourseAuditLog,
    CourseEnrollment,
    CourseEntitlementJob,
    CourseEntitlementJobItem,
)
from app.domain.community.src.index import (
    QuizCourseLibraryBinding,
    QuizExam,
    QuizLibrary,
    QuizLibraryEntitlement,
    QuizModule,
    QuizPracticeSession,
)
from app.port.exceptions import BusinessException, ConflictException, NotFoundException
from app.schemas.admin_course import (
    AdminCourseBindingImpact,
)
from app.services.quiz_v2 import QuizV2Service


ACTIVE_ENROLLMENT_STATUSES = ("enrolled", "completed")
COURSE_ENTITLEMENT_BATCH_SIZE = 500


class CourseEntitlementService:
    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _snapshot(library: QuizLibrary) -> dict[str, object]:
        return {
            "library_id": int(library.id),
            "library_code": library.library_code,
            "name": library.name,
            "cover_url": library.cover_url,
            "description": library.description,
        }

    @classmethod
    async def _locked_course(cls, db: AsyncSession, course_id: int) -> Course:
        course = (
            await db.execute(select(Course).where(Course.id == course_id).with_for_update())
        ).scalar_one_or_none()
        if course is None:
            raise NotFoundException("课程")
        return course

    @classmethod
    async def _locked_library(cls, db: AsyncSession, library_id: int) -> QuizLibrary:
        library = (
            await db.execute(
                select(QuizLibrary).where(QuizLibrary.id == library_id).with_for_update()
            )
        ).scalar_one_or_none()
        if library is None:
            raise NotFoundException("题库")
        return library

    @staticmethod
    def _audit(
        db: AsyncSession,
        *,
        actor_id: int | None,
        action: str,
        object_type: str,
        object_id: int | None,
        summary: dict[str, object] | None = None,
    ) -> None:
        db.add(
            CourseAuditLog(
                actor_type="admin" if actor_id is not None else "system",
                actor_id=actor_id,
                action=action,
                object_type=object_type,
                object_id=object_id,
                result="succeeded",
                summary=summary,
            )
        )

    @classmethod
    async def grant_for_enrollment(
        cls,
        db: AsyncSession,
        enrollment: CourseEnrollment,
        *,
        now: datetime | None = None,
    ) -> list[QuizLibraryEntitlement]:
        """Grant all active course-bound libraries to one effective enrollment."""
        current = now or cls._now()
        rows = (
            await db.execute(
                select(QuizCourseLibraryBinding, QuizLibrary)
                .join(QuizLibrary, QuizLibrary.id == QuizCourseLibraryBinding.library_id)
                .where(
                    QuizCourseLibraryBinding.course_id == enrollment.course_id,
                    QuizCourseLibraryBinding.status == "active",
                    QuizLibrary.access_mode == "course_entitlement",
                    QuizLibrary.status.in_(("draft", "published", "suspended")),
                )
                .order_by(QuizCourseLibraryBinding.id.asc())
            )
        ).all()
        result: list[QuizLibraryEntitlement] = []
        changed_libraries: set[int] = set()
        for _binding, library in rows:
            source_filter = (
                (
                    QuizLibraryEntitlement.order_id == enrollment.order_id,
                    QuizLibraryEntitlement.source_type == "course_order",
                )
                if enrollment.order_id is not None
                else (
                    QuizLibraryEntitlement.enrollment_id == enrollment.id,
                    QuizLibraryEntitlement.source_type == "course_enrollment",
                )
            )
            entitlement = (
                await db.execute(
                    select(QuizLibraryEntitlement)
                    .where(
                        *source_filter,
                        QuizLibraryEntitlement.library_id == library.id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if entitlement is None:
                entitlement = QuizLibraryEntitlement(
                    user_id=enrollment.user_id,
                    library_id=library.id,
                    course_id=enrollment.course_id,
                    order_id=enrollment.order_id,
                    enrollment_id=None if enrollment.order_id is not None else enrollment.id,
                    source_type="course_order" if enrollment.order_id is not None else "course_enrollment",
                    status="active",
                    starts_at=current,
                    snapshot=cls._snapshot(library),
                )
                db.add(entitlement)
                result.append(entitlement)
                changed_libraries.add(int(library.id))
            elif entitlement.status != "active":
                entitlement.status = "active"
                entitlement.starts_at = current
                entitlement.ends_at = None
                entitlement.revoked_at = None
                entitlement.snapshot = cls._snapshot(library)
                result.append(entitlement)
                changed_libraries.add(int(library.id))
        for library_id in changed_libraries:
            await QuizV2Service.sync_sessions_for_library(
                db, library_id, user_id=enrollment.user_id, now=current
            )
        return result

    @classmethod
    async def preview_binding(
        cls, course_id: int, library_id: int
    ) -> AdminCourseBindingImpact:
        async with get_db_ctx() as db:
            course = await db.get(Course, course_id)
            library = await db.get(QuizLibrary, library_id)
            if course is None or library is None:
                raise NotFoundException("课程或题库")

            active_enrollment_count = int(
                await db.scalar(
                    select(func.count())
                    .select_from(CourseEnrollment)
                    .where(
                        CourseEnrollment.course_id == course_id,
                        CourseEnrollment.status.in_(ACTIVE_ENROLLMENT_STATUSES),
                    )
                )
                or 0
            )
            existing_entitlement_count = int(
                await db.scalar(
                    select(func.count())
                    .select_from(QuizLibraryEntitlement)
                    .where(
                        QuizLibraryEntitlement.course_id == course_id,
                        QuizLibraryEntitlement.library_id == library_id,
                        QuizLibraryEntitlement.status == "active",
                    )
                )
                or 0
            )
            candidates = await cls._backfill_candidates(db, course_id, library_id)
            active_session_count = int(
                await db.scalar(
                    select(func.count())
                    .select_from(QuizPracticeSession)
                    .where(
                        QuizPracticeSession.library_id == library_id,
                        QuizPracticeSession.status.in_(("in_progress", "paused")),
                    )
                )
                or 0
            ) + int(
                await db.scalar(
                    select(func.count())
                    .select_from(QuizExam)
                    .where(
                        QuizExam.library_id == library_id,
                        QuizExam.status == "in_progress",
                    )
                )
                or 0
            )
            other_active_source_count = int(
                await db.scalar(
                    select(func.count())
                    .select_from(QuizLibraryEntitlement)
                    .where(
                        QuizLibraryEntitlement.library_id == library_id,
                        QuizLibraryEntitlement.status == "active",
                        QuizLibraryEntitlement.course_id != course_id,
                    )
                )
                or 0
            )
            blockers: list[str] = []
            if course.status not in {"draft", "published"}:
                blockers.append("course_not_published")
            if library.access_mode != "course_entitlement":
                blockers.append("library_not_course_entitlement")
            if library.status != "published":
                blockers.append("library_not_published")
            return AdminCourseBindingImpact(
                course_id=course_id,
                library_id=library_id,
                course_status=course.status,
                library_status=library.status,
                active_enrollment_count=active_enrollment_count,
                existing_entitlement_count=existing_entitlement_count,
                candidates_to_backfill=len(candidates),
                active_session_count=active_session_count,
                other_active_source_count=other_active_source_count,
                can_execute=not blockers,
                blockers=blockers,
            )

    @staticmethod
    async def _backfill_candidates(
        db: AsyncSession, course_id: int, library_id: int
    ) -> list[CourseEnrollment]:
        from sqlalchemy import exists

        already_has_active = exists(
            select(1).where(
                QuizLibraryEntitlement.user_id == CourseEnrollment.user_id,
                QuizLibraryEntitlement.library_id == library_id,
                QuizLibraryEntitlement.status == "active",
            )
        )
        return list(
            (
                await db.execute(
                    select(CourseEnrollment)
                    .where(
                        CourseEnrollment.course_id == course_id,
                        CourseEnrollment.status.in_(ACTIVE_ENROLLMENT_STATUSES),
                        ~already_has_active,
                    )
                    .order_by(CourseEnrollment.id.asc())
                )
            ).scalars()
        )

    @classmethod
    async def create_binding(
        cls,
        course_id: int,
        library_id: int,
        *,
        admin_id: int,
    ) -> CourseEntitlementJob:
        async with get_db_ctx() as db:
            async with db.begin():
                course = await cls._locked_course(db, course_id)
                library = await cls._locked_library(db, library_id)
                if course.status not in {"draft", "published"}:
                    raise BusinessException("只有草稿或已发布课程可以绑定题库")
                if library.access_mode != "course_entitlement":
                    raise BusinessException("题库不是课程权益模式")
                if library.status != "published":
                    raise BusinessException("题库未发布")
                existing = (
                    await db.execute(
                        select(QuizCourseLibraryBinding).where(
                            QuizCourseLibraryBinding.course_id == course_id,
                            QuizCourseLibraryBinding.library_id == library_id,
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    raise ConflictException("课程和题库已存在绑定")
                binding = QuizCourseLibraryBinding(
                    course_id=course_id,
                    library_id=library_id,
                    status="active",
                    created_by=admin_id,
                    updated_by=admin_id,
                )
                db.add(binding)
                await db.flush()

                if course.status == "draft":
                    # A draft course cannot be purchased yet. Keep the binding
                    # active for future fulfillment, but do not queue a backfill
                    # job. A completed zero-count job preserves the route shape
                    # and makes the no-backfill decision visible in task history.
                    job = CourseEntitlementJob(
                        course_id=course_id,
                        library_id=library_id,
                        binding_id=int(binding.id),
                        action="backfill",
                        status="succeeded",
                        batch_size=COURSE_ENTITLEMENT_BATCH_SIZE,
                        total_count=0,
                        created_by=admin_id,
                        started_at=cls._now(),
                        finished_at=cls._now(),
                    )
                    db.add(job)
                    await db.flush()
                else:
                    # Paid enrollments are represented by their immutable order
                    # and can also be backfilled idempotently.
                    paid_enrollments = list(
                        (
                            await db.execute(
                                select(CourseEnrollment)
                                .where(
                                    CourseEnrollment.course_id == course_id,
                                    CourseEnrollment.status.in_(
                                        ACTIVE_ENROLLMENT_STATUSES
                                    ),
                                )
                                .order_by(CourseEnrollment.id.asc())
                            )
                        ).scalars()
                    )
                    job = CourseEntitlementJob(
                        course_id=course_id,
                        library_id=library_id,
                        binding_id=int(binding.id),
                        action="backfill",
                        status="queued",
                        batch_size=COURSE_ENTITLEMENT_BATCH_SIZE,
                        total_count=len(paid_enrollments),
                        created_by=admin_id,
                    )
                    db.add(job)
                    await db.flush()
                    db.add_all(
                        CourseEntitlementJobItem(
                            job_id=job.id,
                            enrollment_id=enrollment.id,
                            user_id=enrollment.user_id,
                        )
                        for enrollment in paid_enrollments
                    )
                cls._audit(
                    db,
                    actor_id=admin_id,
                    action="course.quiz_binding.created",
                    object_type="course_library_binding",
                    object_id=int(binding.id),
                    summary={
                        "course_id": course_id,
                        "library_id": library_id,
                        "backfill_count": len(paid_enrollments),
                    },
                )
                return job

    @classmethod
    async def set_binding_status(
        cls,
        binding_id: int,
        status: str,
        *,
        admin_id: int,
        expected_lock_version: int | None = None,
    ) -> CourseEntitlementJob:
        if status not in {"active", "inactive"}:
            raise BusinessException("绑定状态无效")
        async with get_db_ctx() as db:
            async with db.begin():
                binding = (
                    await db.execute(
                        select(QuizCourseLibraryBinding)
                        .where(QuizCourseLibraryBinding.id == binding_id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if binding is None:
                    raise NotFoundException("课程题库绑定")
                if (
                    expected_lock_version is not None
                    and int(binding.lock_version) != int(expected_lock_version)
                ):
                    raise ConflictException("课程题库绑定已变化，请刷新后重试")
                if binding.status == status:
                    raise BusinessException("绑定状态未变化")
                binding.status = status
                binding.updated_by = admin_id
                binding.lock_version += 1

                if status == "inactive":
                    targets = []
                    for entitlement in (
                        await db.execute(
                            select(QuizLibraryEntitlement)
                            .where(
                                QuizLibraryEntitlement.course_id == binding.course_id,
                                QuizLibraryEntitlement.library_id == binding.library_id,
                                QuizLibraryEntitlement.status == "active",
                            )
                            .order_by(QuizLibraryEntitlement.id.asc())
                        )
                    ).scalars():
                        if entitlement.enrollment_id is not None:
                            enrollment = await db.get(
                                CourseEnrollment, entitlement.enrollment_id
                            )
                        else:
                            enrollment = (
                                await db.execute(
                                    select(CourseEnrollment).where(
                                        CourseEnrollment.course_id == binding.course_id,
                                        CourseEnrollment.order_id == entitlement.order_id,
                                    )
                                )
                            ).scalar_one_or_none()
                        if enrollment is not None:
                            targets.append(enrollment)
                else:
                    enrollments = list(
                        (
                            await db.execute(
                                select(CourseEnrollment)
                                .where(
                                    CourseEnrollment.course_id == binding.course_id,
                                    CourseEnrollment.status.in_(ACTIVE_ENROLLMENT_STATUSES),
                                )
                                .order_by(CourseEnrollment.id.asc())
                            )
                        ).scalars()
                    )
                    targets = enrollments

                job = CourseEntitlementJob(
                    course_id=binding.course_id,
                    library_id=binding.library_id,
                    binding_id=binding_id,
                    action="backfill" if status == "active" else "revoke",
                    status="queued",
                    batch_size=COURSE_ENTITLEMENT_BATCH_SIZE,
                    total_count=len(targets),
                    created_by=admin_id,
                )
                db.add(job)
                await db.flush()
                if status == "inactive":
                    db.add_all(
                        CourseEntitlementJobItem(
                            job_id=job.id,
                            enrollment_id=int(item.id),
                            user_id=int(item.user_id),
                        )
                        for item in targets
                    )
                else:
                    db.add_all(
                        CourseEntitlementJobItem(
                            job_id=job.id,
                            enrollment_id=int(item.id),
                            user_id=int(item.user_id),
                        )
                        for item in targets
                    )
                cls._audit(
                    db,
                    actor_id=admin_id,
                    action=f"course.quiz_binding.{status}",
                    object_type="course_library_binding",
                    object_id=binding_id,
                    summary={"affected_count": len(targets)},
                )
                return job

    @classmethod
    async def process_one_job(cls) -> bool:
        """Process at most one queued job and one configured batch per tick."""
        async with get_db_ctx() as db:
            async with db.begin():
                job = (
                    await db.execute(
                        select(CourseEntitlementJob)
                        .where(CourseEntitlementJob.status == "queued")
                        .order_by(CourseEntitlementJob.id.asc())
                        .with_for_update(skip_locked=True)
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if job is None:
                    return False
                job.status = "running"
                job.started_at = job.started_at or cls._now()
                items = list(
                    (
                        await db.execute(
                            select(CourseEntitlementJobItem)
                            .where(
                                CourseEntitlementJobItem.job_id == job.id,
                                CourseEntitlementJobItem.status == "pending",
                            )
                            .order_by(CourseEntitlementJobItem.id.asc())
                            .limit(job.batch_size)
                        )
                    ).scalars()
                )
                success = 0
                failure = 0
                for item in items:
                    try:
                        if job.action == "backfill":
                            enrollment = await db.get(CourseEnrollment, item.enrollment_id)
                            if enrollment is None:
                                raise BusinessException("课程报名不存在")
                            library = await db.get(QuizLibrary, job.library_id)
                            if library is None:
                                raise BusinessException("题库不存在")
                            await cls._grant_one_source(db, enrollment, library)
                        else:
                            await cls._revoke_course_library_for_user(
                                db, job.course_id, job.library_id, item.user_id
                            )
                        item.status = "succeeded"
                        success += 1
                    except Exception as exc:
                        item.status = "failed"
                        item.error_message = str(exc)[:1024]
                        failure += 1
                job.processed_count += success + failure
                job.success_count += success
                job.failure_count += failure
                remaining = int(
                    await db.scalar(
                        select(func.count())
                        .select_from(CourseEntitlementJobItem)
                        .where(
                            CourseEntitlementJobItem.job_id == job.id,
                            CourseEntitlementJobItem.status == "pending",
                        )
                    )
                    or 0
                )
                if remaining:
                    job.status = "queued"
                else:
                    job.status = "succeeded" if job.failure_count == 0 else "partial"
                    job.finished_at = cls._now()
                cls._audit(
                    db,
                    actor_id=None,
                    action="course.entitlement.job.progress",
                    object_type="course_entitlement_job",
                    object_id=job.id,
                    summary={
                        "success": success,
                        "failure": failure,
                        "remaining": remaining,
                    },
                )
                return True

    @classmethod
    async def _grant_one_source(
        cls, db: AsyncSession, enrollment: CourseEnrollment, library: QuizLibrary
    ) -> QuizLibraryEntitlement:
        if enrollment.order_id is not None:
            entitlement = (
                await db.execute(
                    select(QuizLibraryEntitlement)
                    .where(
                        QuizLibraryEntitlement.order_id == enrollment.order_id,
                        QuizLibraryEntitlement.library_id == library.id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
        else:
            entitlement = (
                await db.execute(
                    select(QuizLibraryEntitlement)
                    .where(
                        QuizLibraryEntitlement.enrollment_id == enrollment.id,
                        QuizLibraryEntitlement.library_id == library.id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
        now = cls._now()
        if entitlement is None:
            entitlement = QuizLibraryEntitlement(
                user_id=enrollment.user_id,
                library_id=library.id,
                course_id=enrollment.course_id,
                order_id=enrollment.order_id,
                enrollment_id=None if enrollment.order_id is not None else enrollment.id,
                source_type="course_order" if enrollment.order_id is not None else "course_enrollment",
                status="active",
                starts_at=now,
                snapshot=cls._snapshot(library),
            )
            db.add(entitlement)
            await db.flush()
        elif entitlement.status != "active":
            entitlement.status = "active"
            entitlement.starts_at = now
            entitlement.ends_at = None
            entitlement.revoked_at = None
            entitlement.snapshot = cls._snapshot(library)
        await QuizV2Service.sync_sessions_for_library(
            db, int(library.id), user_id=enrollment.user_id, now=now
        )
        return entitlement

    @classmethod
    async def _revoke_course_library_for_user(
        cls, db: AsyncSession, course_id: int, library_id: int, user_id: int
    ) -> bool:
        entitlements = list(
            (
                await db.execute(
                    select(QuizLibraryEntitlement)
                    .where(
                        QuizLibraryEntitlement.course_id == course_id,
                        QuizLibraryEntitlement.library_id == library_id,
                        QuizLibraryEntitlement.user_id == user_id,
                        QuizLibraryEntitlement.status == "active",
                    )
                    .with_for_update()
                )
            ).scalars()
        )
        now = cls._now()
        for entitlement in entitlements:
            entitlement.status = "revoked"
            entitlement.revoked_at = now
        other_active = (
            await db.execute(
                select(QuizLibraryEntitlement.id).where(
                    QuizLibraryEntitlement.user_id == user_id,
                    QuizLibraryEntitlement.library_id == library_id,
                    QuizLibraryEntitlement.status == "active",
                )
            )
        ).scalar_one_or_none()
        if other_active is None:
            await QuizV2Service.sync_sessions_for_library(
                db, library_id, user_id=user_id, now=now
            )
        return bool(entitlements)

    @classmethod
    async def retry_job(cls, job_id: int, *, admin_id: int) -> CourseEntitlementJob:
        async with get_db_ctx() as db:
            async with db.begin():
                job = (
                    await db.execute(
                        select(CourseEntitlementJob)
                        .where(CourseEntitlementJob.id == job_id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if job is None:
                    raise NotFoundException("课程权益任务")
                if job.status not in {"partial", "failed"}:
                    raise BusinessException("当前任务不需要重试")
                await db.execute(
                    CourseEntitlementJobItem.__table__.update()
                    .where(
                        CourseEntitlementJobItem.job_id == job_id,
                        CourseEntitlementJobItem.status == "failed",
                    )
                    .values(status="pending", error_message=None)
                )
                pending = int(
                    await db.scalar(
                        select(func.count())
                        .select_from(CourseEntitlementJobItem)
                        .where(
                            CourseEntitlementJobItem.job_id == job_id,
                            CourseEntitlementJobItem.status == "pending",
                        )
                    )
                    or 0
                )
                job.status = "queued"
                job.total_count = job.success_count + pending
                job.processed_count = job.success_count
                job.failure_count = 0
                job.retry_count += 1
                job.last_error = None
                job.finished_at = None
                cls._audit(
                    db,
                    actor_id=admin_id,
                    action="course.entitlement.job.retried",
                    object_type="course_entitlement_job",
                    object_id=job_id,
                    summary={"pending": pending},
                )
                return job

    @staticmethod
    async def list_library_modules(db: AsyncSession, library_ids: list[int]):
        if not library_ids:
            return {}
        rows = (
            await db.execute(
                select(QuizModule)
                .where(
                    QuizModule.library_id.in_(library_ids),
                    QuizModule.status == "active",
                )
                .order_by(QuizModule.sort_order.asc(), QuizModule.id.asc())
            )
        ).scalars()
        grouped: dict[int, list[QuizModule]] = {}
        for module in rows:
            grouped.setdefault(int(module.library_id), []).append(module)
        return grouped
