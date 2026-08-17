"""Admin operations for the fixed quiz-library V2 hierarchy.

The service intentionally coexists with ``AdminQuizService`` during the
one-week compatibility window. V2 writes never create recursive categories;
legacy routes remain adapters until the cutover is complete.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.adapter.database import get_db_ctx
from app.adapter.logging import client_ip_var, request_id_var
from app.domain.community.src.index import (
    QuizAdminAuditLog,
    QuizKnowledgePoint,
    QuizCourseLibraryBinding,
    QuizLegacyMigrationMap,
    QuizLibrary,
    QuizMigrationIssue,
    QuizModule,
    QuizQuestion,
    QuizQuestionRevision,
)
from app.domain.community.src.rule.quiz import (
    QuizRuleViolation,
    normalize_category_name,
    normalize_question_payload,
)
from app.port.exceptions import (
    BusinessException,
    ConflictException,
    NotFoundException,
    ValidationException,
)
from app.schemas.admin_quiz_contract import (
    AdminQuizBatchItemError,
    AdminQuizBatchRequest,
    AdminQuizBatchResponse,
    AdminQuizContentStatusUpdate,
    AdminQuizContentTreeResponse,
    AdminQuizKnowledgePointCreate,
    AdminQuizKnowledgePointResponse,
    AdminQuizKnowledgePointUpdate,
    AdminQuizCourseBindingCreate,
    AdminQuizCourseOptionResponse,
    AdminQuizCourseBindingResponse,
    AdminQuizCourseBindingStatusUpdate,
    AdminQuizLibraryCreate,
    AdminQuizLibraryQuery,
    AdminQuizLibraryResponse,
    AdminQuizLibraryStatusUpdate,
    AdminQuizLibraryUpdate,
    AdminQuizMigrationIssueResponse,
    AdminQuizMigrationReportResponse,
    AdminQuizModuleCreate,
    AdminQuizModuleResponse,
    AdminQuizModuleUpdate,
    AdminQuizQuestionCreate,
    AdminQuizQuestionQuery,
    AdminQuizQuestionResponse,
    AdminQuizQuestionRevisionResponse,
    AdminQuizQuestionUpdate,
    AdminQuizVersionRequest,
)
from app.domain.certification.src.index import Course
from app.schemas.common import PaginatedData
from app.services.quiz_v2 import QuizV2Service
from app.services.course_entitlement import CourseEntitlementService
from app.utils.audit import sanitize_audit_value


_RESTORE_WINDOW = timedelta(days=7)


class AdminQuizV2Service:
    _CONFLICT = "数据已被其他管理员修改，请刷新后重试"

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _value(value: object) -> object:
        value = getattr(value, "value", value)
        if isinstance(value, datetime):
            return value.isoformat()
        return sanitize_audit_value(value)

    @classmethod
    def _audit(
        cls,
        db,
        *,
        admin_id: int,
        action: str,
        object_type: str,
        object_id: int | None,
        before: dict[str, object] | None = None,
        after: dict[str, object] | None = None,
        permission: str = "quiz_library_manage",
    ) -> None:
        changed_fields = None
        if before is not None and after is not None:
            changed_fields = {
                key: {
                    "before": cls._value(before.get(key)),
                    "after": cls._value(after.get(key)),
                }
                for key in after
                if before.get(key) != after.get(key)
            }
        request_id = request_id_var.get()
        db.add(
            QuizAdminAuditLog(
                actor_type="admin",
                admin_id=admin_id,
                permission=permission,
                request_id=None if request_id == "-" else request_id,
                ip_address=client_ip_var.get(),
                action=action,
                object_type=object_type,
                object_id=object_id,
                result="succeeded",
                changed_fields=changed_fields,
            )
        )

    @classmethod
    def _check_version(cls, entity, expected: int) -> None:
        if int(entity.lock_version) != expected:
            raise ConflictException(cls._CONFLICT)

    @staticmethod
    async def _locked(db, model, entity_id: int):
        return (
            await db.execute(
                select(model).where(model.id == entity_id).with_for_update()
            )
        ).scalar_one_or_none()

    @staticmethod
    def _library_fields(library: QuizLibrary) -> dict[str, object]:
        return {
            "name": library.name,
            "description": library.description,
            "cover_url": library.cover_url,
            "details": library.details,
            "access_mode": library.access_mode,
            "status": library.status,
            "v2_enabled": library.v2_enabled,
            "sort_order": library.sort_order,
        }

    @staticmethod
    def _module_fields(module: QuizModule) -> dict[str, object]:
        return {
            "name": module.name,
            "description": module.description,
            "status": module.status,
            "sort_order": module.sort_order,
        }

    @staticmethod
    def _point_fields(point: QuizKnowledgePoint) -> dict[str, object]:
        return {
            "module_id": point.module_id,
            "name": point.name,
            "description": point.description,
            "status": point.status,
            "sort_order": point.sort_order,
        }

    @staticmethod
    def _question_fields(question: QuizQuestion) -> dict[str, object]:
        return {
            "library_id": question.library_id,
            "knowledge_point_id": question.knowledge_point_id,
            "status": question.status,
            "question_type": question.question_type,
            "question_text": question.question_text,
            "options": question.options,
            "correct_answer": question.correct_answer,
            "explanation": question.explanation,
            "current_revision_id": question.current_revision_id,
            "pending_revision_id": question.pending_revision_id,
            "deleted_at": question.deleted_at,
        }

    @staticmethod
    def _revision_response(
        revision: QuizQuestionRevision,
    ) -> AdminQuizQuestionRevisionResponse:
        return AdminQuizQuestionRevisionResponse.model_validate(revision)

    @classmethod
    async def _question_response(
        cls, db, question: QuizQuestion
    ) -> AdminQuizQuestionResponse:
        revision_ids = [
            revision_id
            for revision_id in (
                question.current_revision_id,
                question.pending_revision_id,
            )
            if revision_id is not None
        ]
        revisions: dict[int, QuizQuestionRevision] = {}
        if revision_ids:
            revisions = {
                int(item.id): item
                for item in (
                    await db.execute(
                        select(QuizQuestionRevision).where(
                            QuizQuestionRevision.id.in_(revision_ids)
                        )
                    )
                ).scalars()
            }
        current = revisions.get(int(question.current_revision_id or 0))
        pending = revisions.get(int(question.pending_revision_id or 0))
        return AdminQuizQuestionResponse(
            id=int(question.id),
            category_id=question.category_id,
            library_id=question.library_id,
            knowledge_point_id=question.knowledge_point_id,
            question_type=question.question_type,
            status=question.status,
            question_text=question.question_text,
            normalized_question_text=question.normalized_question_text,
            options=question.options,
            correct_answer=question.correct_answer,
            explanation=question.explanation,
            ever_published=bool(question.ever_published),
            published_at=question.published_at,
            disabled_at=question.disabled_at,
            deleted_at=question.deleted_at,
            restore_until=question.restore_until,
            current_revision_id=question.current_revision_id,
            current_revision_no=(int(current.revision_no) if current else None),
            pending_revision_id=question.pending_revision_id,
            pending_revision_no=(int(pending.revision_no) if pending else None),
            has_pending_revision=pending is not None,
            lock_version=int(question.lock_version),
            created_by=int(question.created_by),
            updated_by=int(question.updated_by),
            created_at=question.created_at,
            updated_at=question.updated_at,
        )

    @staticmethod
    async def _active_point(db, point_id: int) -> tuple[QuizLibrary, QuizModule, QuizKnowledgePoint]:
        row = (
            await db.execute(
                select(QuizLibrary, QuizModule, QuizKnowledgePoint)
                .join(QuizModule, QuizModule.library_id == QuizLibrary.id)
                .join(
                    QuizKnowledgePoint,
                    QuizKnowledgePoint.module_id == QuizModule.id,
                )
                .where(QuizKnowledgePoint.id == point_id)
                .with_for_update()
            )
        ).one_or_none()
        if row is None:
            raise NotFoundException("知识点")
        library, module, point = row
        if library.status in {"archived", "deleted"}:
            raise BusinessException("已归档或删除题库不可写入题目")
        if module.status != "active" or point.status != "active":
            raise BusinessException("题目只能写入有效模块下的有效知识点")
        return library, module, point

    @staticmethod
    def _normalize_question(
        *,
        question_type: object,
        question_text: object,
        options: object,
        correct_answer: object,
        explanation: object,
        require_publishable: bool,
    ):
        try:
            return normalize_question_payload(
                question_type=question_type,
                question_text=question_text,
                options=options,
                correct_answer=correct_answer,
                explanation=explanation,
                require_publishable=require_publishable,
            )
        except QuizRuleViolation as exc:
            raise ValidationException(exc.message, [{"field": exc.field, "reason": exc.message}]) from exc

    @staticmethod
    async def _stem_taken(
        db,
        *,
        library_id: int,
        question_text_hash: str,
        exclude_id: int | None = None,
    ) -> bool:
        stmt = select(QuizQuestion.id).where(
            QuizQuestion.library_id == library_id,
            QuizQuestion.question_text_hash == question_text_hash,
            QuizQuestion.stem_reserved.is_(True),
        )
        if exclude_id is not None:
            stmt = stmt.where(QuizQuestion.id != exclude_id)
        return (await db.execute(stmt.limit(1))).scalar_one_or_none() is not None

    @staticmethod
    def _apply_content(question: QuizQuestion, normalized) -> None:
        question.question_type = normalized.question_type.value
        question.question_text = normalized.question_text
        question.normalized_question_text = normalized.normalized_question_text
        question.question_text_hash = normalized.question_text_hash
        question.options = normalized.options
        question.correct_answer = normalized.correct_answer
        question.explanation = normalized.explanation

    @staticmethod
    def _new_revision(
        question: QuizQuestion,
        normalized,
        *,
        revision_no: int,
        status: str,
        admin_id: int,
        published_at: datetime | None = None,
    ) -> QuizQuestionRevision:
        return QuizQuestionRevision(
            question_id=question.id,
            revision_no=revision_no,
            status=status,
            question_type=normalized.question_type.value,
            question_text=normalized.question_text,
            normalized_question_text=normalized.normalized_question_text,
            question_text_hash=normalized.question_text_hash,
            options=normalized.options,
            correct_answer=normalized.correct_answer,
            explanation=normalized.explanation,
            published_at=published_at,
            created_by=admin_id,
        )

    @staticmethod
    async def _library_counts(db, library_ids: list[int]) -> dict[int, dict[str, int]]:
        counts = {
            library_id: {"issues": 0, "modules": 0, "points": 0, "questions": 0}
            for library_id in library_ids
        }
        if not library_ids:
            return counts
        queries = (
            (
                "issues",
                select(QuizMigrationIssue.library_id, func.count())
                .where(
                    QuizMigrationIssue.library_id.in_(library_ids),
                    QuizMigrationIssue.status == "open",
                )
                .group_by(QuizMigrationIssue.library_id),
            ),
            (
                "modules",
                select(QuizModule.library_id, func.count())
                .where(QuizModule.library_id.in_(library_ids), QuizModule.status != "deleted")
                .group_by(QuizModule.library_id),
            ),
            (
                "points",
                select(QuizKnowledgePoint.library_id, func.count())
                .where(
                    QuizKnowledgePoint.library_id.in_(library_ids),
                    QuizKnowledgePoint.status != "deleted",
                )
                .group_by(QuizKnowledgePoint.library_id),
            ),
            (
                "questions",
                select(QuizQuestion.library_id, func.count())
                .where(
                    QuizQuestion.library_id.in_(library_ids),
                    QuizQuestion.status != "deleted",
                )
                .group_by(QuizQuestion.library_id),
            ),
        )
        for field, stmt in queries:
            for library_id, count in (await db.execute(stmt)).all():
                counts[int(library_id)][field] = int(count)
        return counts

    @classmethod
    def _library_response(
        cls, library: QuizLibrary, counts: dict[str, int]
    ) -> AdminQuizLibraryResponse:
        return AdminQuizLibraryResponse(
            id=int(library.id),
            library_code=library.library_code,
            name=library.name,
            normalized_name=library.normalized_name,
            description=library.description,
            cover_url=library.cover_url,
            details=library.details,
            access_mode=library.access_mode,
            system_kind=library.system_kind,
            migration_state=library.migration_state,
            status=library.status,
            v2_enabled=bool(library.v2_enabled),
            sort_order=int(library.sort_order),
            lock_version=int(library.lock_version),
            published_at=library.published_at,
            suspended_at=library.suspended_at,
            archived_at=library.archived_at,
            deleted_at=library.deleted_at,
            restore_until=library.restore_until,
            open_migration_issue_count=counts["issues"],
            module_count=counts["modules"],
            knowledge_point_count=counts["points"],
            question_count=counts["questions"],
            created_at=library.created_at,
            updated_at=library.updated_at,
        )

    async def list_libraries(
        self, query: AdminQuizLibraryQuery
    ) -> list[AdminQuizLibraryResponse]:
        async with get_db_ctx() as db:
            stmt = select(QuizLibrary)
            if not query.include_deleted:
                stmt = stmt.where(QuizLibrary.status != "deleted")
            if query.status is not None:
                stmt = stmt.where(QuizLibrary.status == str(query.status))
            if query.access_mode is not None:
                stmt = stmt.where(QuizLibrary.access_mode == str(query.access_mode))
            if query.keyword:
                keyword = f"%{query.keyword.strip()}%"
                stmt = stmt.where(
                    or_(QuizLibrary.name.ilike(keyword), QuizLibrary.library_code.ilike(keyword))
                )
            libraries = list(
                (
                    await db.execute(
                        stmt.order_by(QuizLibrary.sort_order.asc(), QuizLibrary.id.asc())
                    )
                ).scalars()
            )
            counts = await self._library_counts(db, [int(item.id) for item in libraries])
            return [self._library_response(item, counts[int(item.id)]) for item in libraries]

    async def get_library(self, library_id: int) -> AdminQuizLibraryResponse:
        async with get_db_ctx() as db:
            library = await db.get(QuizLibrary, library_id)
            if library is None:
                raise NotFoundException("题库")
            counts = await self._library_counts(db, [library_id])
            return self._library_response(library, counts[library_id])

    async def create_library(
        self, data: AdminQuizLibraryCreate, *, admin_id: int
    ) -> AdminQuizLibraryResponse:
        name = normalize_category_name(data.name)
        library = QuizLibrary(
            name=name,
            normalized_name=name,
            description=data.description,
            cover_url=data.cover_url,
            details=data.details,
            access_mode=str(data.access_mode),
            system_kind="none",
            migration_state="ready",
            status="draft",
            v2_enabled=False,
            name_reserved=True,
            sort_order=data.sort_order,
            lock_version=1,
            created_by=admin_id,
            updated_by=admin_id,
        )
        async with get_db_ctx() as db:
            db.add(library)
            try:
                await db.flush()
                self._audit(
                    db,
                    admin_id=admin_id,
                    action="library.create",
                    object_type="library",
                    object_id=int(library.id),
                    before={},
                    after=self._library_fields(library),
                )
                await db.commit()
            except IntegrityError as exc:
                await db.rollback()
                raise ValidationException("题库名称已存在") from exc
            await db.refresh(library)
            return self._library_response(
                library,
                {"issues": 0, "modules": 0, "points": 0, "questions": 0},
            )

    async def update_library(
        self,
        library_id: int,
        data: AdminQuizLibraryUpdate,
        *,
        admin_id: int,
    ) -> AdminQuizLibraryResponse:
        async with get_db_ctx() as db:
            library = await self._locked(db, QuizLibrary, library_id)
            if library is None:
                raise NotFoundException("题库")
            self._check_version(library, data.lock_version)
            if library.status in {"archived", "deleted"}:
                raise BusinessException("已归档或删除题库不可编辑")
            before = self._library_fields(library)
            fields = data.model_fields_set - {"lock_version"}
            for field in ("description", "cover_url", "details", "sort_order"):
                if field in fields:
                    setattr(library, field, getattr(data, field))
            if "name" in fields and data.name is not None:
                library.name = normalize_category_name(data.name)
                library.normalized_name = library.name
            if "access_mode" in fields and data.access_mode is not None:
                library.access_mode = str(data.access_mode)
            if "v2_enabled" in fields and data.v2_enabled is not None:
                library.v2_enabled = data.v2_enabled
            if library.v2_enabled and ({"access_mode", "v2_enabled"} & fields):
                if library.status != "published":
                    raise BusinessException("只有已发布题库可以启用 V2 用户入口")
                blockers = await self._publication_blockers(db, library)
                if blockers:
                    raise BusinessException("；".join(blockers))
                if library.access_mode == "course_entitlement":
                    active_binding_count = int(
                        (
                            await db.execute(
                                select(func.count()).where(
                                    QuizCourseLibraryBinding.library_id == library.id,
                                    QuizCourseLibraryBinding.status == "active",
                                )
                            )
                        ).scalar()
                        or 0
                    )
                    if active_binding_count == 0:
                        raise BusinessException("课程权益题库至少需要一个有效课程绑定")
            access_changed = (
                before["access_mode"] != library.access_mode
                or before["v2_enabled"] != library.v2_enabled
            )
            if access_changed:
                await QuizV2Service.sync_sessions_for_library(
                    db, library_id, now=self._now()
                )
            library.updated_by = admin_id
            library.lock_version += 1
            self._audit(
                db,
                admin_id=admin_id,
                action="library.update",
                object_type="library",
                object_id=library_id,
                before=before,
                after=self._library_fields(library),
                permission="quiz_library_manage",
            )
            try:
                await db.commit()
            except IntegrityError as exc:
                await db.rollback()
                raise ValidationException("题库名称已存在") from exc
            await db.refresh(library)
            counts = await self._library_counts(db, [library_id])
            return self._library_response(library, counts[library_id])

    async def _publication_blockers(self, db, library: QuizLibrary) -> list[str]:
        blockers: list[str] = []
        if not library.cover_url:
            blockers.append("缺少封面")
        if not library.description:
            blockers.append("缺少简介")
        if library.access_mode == "access_mode_pending":
            blockers.append("访问模式未确认")
        open_issues = int(
            (
                await db.execute(
                    select(func.count()).where(
                        QuizMigrationIssue.library_id == library.id,
                        QuizMigrationIssue.status == "open",
                        QuizMigrationIssue.severity == "blocking",
                    )
                )
            ).scalar()
            or 0
        )
        if open_issues:
            blockers.append(f"仍有 {open_issues} 个迁移阻断项")
        system_questions = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(QuizQuestion)
                    .join(
                        QuizKnowledgePoint,
                        QuizKnowledgePoint.id == QuizQuestion.knowledge_point_id,
                    )
                    .join(QuizModule, QuizModule.id == QuizKnowledgePoint.module_id)
                    .where(
                        QuizQuestion.library_id == library.id,
                        QuizQuestion.status != "deleted",
                        or_(
                            QuizModule.system_kind != "none",
                            QuizKnowledgePoint.system_kind != "none",
                        ),
                    )
                )
            ).scalar()
            or 0
        )
        if system_questions:
            blockers.append(f"仍有 {system_questions} 道待整理题目")
        published_count = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(QuizQuestion)
                    .join(
                        QuizKnowledgePoint,
                        QuizKnowledgePoint.id == QuizQuestion.knowledge_point_id,
                    )
                    .join(QuizModule, QuizModule.id == QuizKnowledgePoint.module_id)
                    .where(
                        QuizQuestion.library_id == library.id,
                        QuizQuestion.status == "published",
                        QuizKnowledgePoint.status == "active",
                        QuizKnowledgePoint.system_kind == "none",
                        QuizModule.status == "active",
                        QuizModule.system_kind == "none",
                    )
                )
            ).scalar()
            or 0
        )
        if published_count == 0:
            blockers.append("没有有效的模块、知识点和已发布题目路径")
        return blockers

    @classmethod
    async def _reconcile_library_migration_issues(
        cls,
        db,
        library: QuizLibrary,
        *,
        now: datetime,
    ) -> dict[str, int]:
        """Re-evaluate migration issues against the current V2 ownership tree.

        Migration issues describe work to be performed, not permanent facts.
        A structural issue is resolved after its question has been moved out of
        system holding nodes (or deleted). Duplicate-stem issues additionally
        require the question to reclaim the library-wide stem reservation.
        Resolved issues can reopen if a deleted legacy question is restored
        into an unresolved system location.
        """

        issues = list(
            (
                await db.execute(
                    select(QuizMigrationIssue)
                    .where(QuizMigrationIssue.library_id == library.id)
                    .order_by(QuizMigrationIssue.id.asc())
                    .with_for_update()
                )
            ).scalars()
        )
        question_ids = {
            int(issue.legacy_id)
            for issue in issues
            if issue.legacy_object_type == "question"
        }
        category_ids = {
            int(issue.legacy_id)
            for issue in issues
            if issue.legacy_object_type == "category"
        }
        question_predicates = []
        if question_ids:
            question_predicates.append(QuizQuestion.id.in_(question_ids))
        if category_ids:
            question_predicates.append(QuizQuestion.category_id.in_(category_ids))
        questions = (
            list(
                (
                    await db.execute(
                        select(QuizQuestion).where(or_(*question_predicates))
                    )
                ).scalars()
            )
            if question_predicates
            else []
        )
        questions_by_id = {int(item.id): item for item in questions}
        point_ids = {
            int(item.knowledge_point_id)
            for item in questions
            if item.knowledge_point_id is not None
        }
        points = (
            {
                int(item.id): item
                for item in (
                    await db.execute(
                        select(QuizKnowledgePoint).where(
                            QuizKnowledgePoint.id.in_(point_ids)
                        )
                    )
                ).scalars()
            }
            if point_ids
            else {}
        )
        module_ids = {int(item.module_id) for item in points.values()}
        modules = (
            {
                int(item.id): item
                for item in (
                    await db.execute(
                        select(QuizModule).where(QuizModule.id.in_(module_ids))
                    )
                ).scalars()
            }
            if module_ids
            else {}
        )

        def has_normal_location(question: QuizQuestion) -> bool:
            if question.status == "deleted" or int(question.library_id or 0) != int(library.id):
                return True
            point = points.get(int(question.knowledge_point_id or 0))
            module = modules.get(int(point.module_id)) if point is not None else None
            return bool(
                point is not None
                and module is not None
                and int(point.library_id) == int(library.id)
                and int(module.library_id) == int(library.id)
                and point.status != "deleted"
                and module.status != "deleted"
                and point.system_kind == "none"
                and module.system_kind == "none"
            )

        resolved_count = 0
        reopened_count = 0
        structural_codes = {
            "question_attached_to_library",
            "question_attached_to_module",
            "invalid_legacy_hierarchy",
        }
        for issue in issues:
            resolved = False
            if issue.legacy_object_type == "question":
                question = questions_by_id.get(int(issue.legacy_id))
                if (
                    question is None
                    or question.status == "deleted"
                    or int(question.library_id or 0) != int(library.id)
                ):
                    resolved = True
                elif issue.issue_code == "duplicate_question_stem_in_library":
                    resolved = bool(question.stem_reserved)
                elif issue.issue_code in structural_codes:
                    resolved = has_normal_location(question)
            elif (
                issue.legacy_object_type == "category"
                and issue.issue_code == "invalid_legacy_hierarchy"
            ):
                affected = [
                    question
                    for question in questions
                    if question.category_id == issue.legacy_id
                    and int(question.library_id or 0) == int(library.id)
                ]
                resolved = all(has_normal_location(question) for question in affected)

            desired_status = "resolved" if resolved else "open"
            if issue.status == desired_status:
                continue
            if resolved:
                resolved_count += 1
                issue.resolved_at = now
            else:
                reopened_count += 1
                issue.resolved_at = None
            issue.status = desired_status

        await db.flush()
        open_count = sum(1 for issue in issues if issue.status == "open")
        open_blocking_count = sum(
            1
            for issue in issues
            if issue.status == "open" and issue.severity == "blocking"
        )
        system_question_count = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(QuizQuestion)
                    .join(
                        QuizKnowledgePoint,
                        QuizKnowledgePoint.id == QuizQuestion.knowledge_point_id,
                    )
                    .join(QuizModule, QuizModule.id == QuizKnowledgePoint.module_id)
                    .where(
                        QuizQuestion.library_id == library.id,
                        QuizQuestion.status != "deleted",
                        or_(
                            QuizModule.system_kind != "none",
                            QuizKnowledgePoint.system_kind != "none",
                        ),
                    )
                )
            ).scalar()
            or 0
        )
        library.migration_state = (
            "ready"
            if open_blocking_count == 0 and system_question_count == 0
            else "needs_organization"
        )
        return {
            "resolved": resolved_count,
            "reopened": reopened_count,
            "open": open_count,
            "open_blocking": open_blocking_count,
            "system_questions": system_question_count,
        }

    @classmethod
    async def _ensure_published_library_remains_usable(
        cls,
        db,
        library_id: int,
        *,
        excluded_question_ids: set[int] | None = None,
        excluded_module_ids: set[int] | None = None,
        excluded_point_ids: set[int] | None = None,
    ) -> None:
        """Serialize destructive content transitions and protect the live pool.

        A published library must always retain at least one effective
        ``module -> knowledge point -> published question`` path.  Locking the
        library row makes concurrent disables observe one another instead of
        both accepting the same last remaining path.
        """

        library = (
            await db.execute(
                select(QuizLibrary)
                .where(QuizLibrary.id == library_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if library is None:
            raise NotFoundException("题库")
        if library.status != "published":
            return

        stmt = (
            select(func.count())
            .select_from(QuizQuestion)
            .join(
                QuizKnowledgePoint,
                QuizKnowledgePoint.id == QuizQuestion.knowledge_point_id,
            )
            .join(QuizModule, QuizModule.id == QuizKnowledgePoint.module_id)
            .where(
                QuizQuestion.library_id == library_id,
                QuizQuestion.status == "published",
                QuizKnowledgePoint.status == "active",
                QuizKnowledgePoint.system_kind == "none",
                QuizModule.status == "active",
                QuizModule.system_kind == "none",
            )
        )
        if excluded_question_ids:
            stmt = stmt.where(QuizQuestion.id.not_in(excluded_question_ids))
        if excluded_module_ids:
            stmt = stmt.where(QuizModule.id.not_in(excluded_module_ids))
        if excluded_point_ids:
            stmt = stmt.where(QuizKnowledgePoint.id.not_in(excluded_point_ids))
        remaining = int((await db.execute(stmt)).scalar() or 0)
        if remaining == 0:
            raise BusinessException(
                "已发布题库必须保留至少一条有效的模块、知识点和已发布题目路径；"
                "如需紧急清空，请先停用题库"
            )

    async def transition_library(
        self,
        library_id: int,
        data: AdminQuizLibraryStatusUpdate,
        *,
        admin_id: int,
    ) -> AdminQuizLibraryResponse:
        async with get_db_ctx() as db:
            library = await self._locked(db, QuizLibrary, library_id)
            if library is None:
                raise NotFoundException("题库")
            self._check_version(library, data.lock_version)
            now = self._now()
            before = self._library_fields(library)
            action = data.action
            if action == "publish":
                if library.status != "draft":
                    raise BusinessException("仅草稿题库可以发布")
                await self._reconcile_library_migration_issues(
                    db, library, now=now
                )
                blockers = await self._publication_blockers(db, library)
                if blockers:
                    raise BusinessException("；".join(blockers))
                library.status = "published"
                library.published_at = now
                library.migration_state = "ready"
            elif action == "reconcile_migration":
                if library.status != "draft":
                    raise BusinessException("只有草稿题库可以重新检查迁移问题")
                await self._reconcile_library_migration_issues(
                    db, library, now=now
                )
            elif action == "suspend":
                if library.status != "published":
                    raise BusinessException("仅已发布题库可以停用")
                library.status = "suspended"
                library.suspended_at = now
                library.v2_enabled = False
            elif action == "restore":
                if library.status != "suspended":
                    raise BusinessException("仅已停用题库可以恢复")
                blockers = await self._publication_blockers(db, library)
                if blockers:
                    raise BusinessException("；".join(blockers))
                library.status = "published"
                library.suspended_at = None
            elif action == "archive":
                if library.status not in {"published", "suspended"}:
                    raise BusinessException("仅已发布或停用题库可以归档")
                active_questions = int(
                    (
                        await db.execute(
                            select(func.count()).where(
                                QuizQuestion.library_id == library_id,
                                QuizQuestion.status == "published",
                            )
                        )
                    ).scalar()
                    or 0
                )
                if active_questions:
                    raise BusinessException("归档前必须停用全部已发布题目")
                library.status = "archived"
                library.archived_at = now
                library.v2_enabled = False
            elif action == "delete":
                if library.status != "archived":
                    raise BusinessException("只有已归档题库可以删除")
                library.status = "deleted"
                library.deleted_at = now
                library.restore_until = now + _RESTORE_WINDOW
                library.v2_enabled = False
            elif action == "undo_delete":
                if library.status != "deleted":
                    raise BusinessException("只有已删除题库可以撤销删除")
                if library.restore_until is None or library.restore_until <= now:
                    raise BusinessException("删除已超过 7 天，无法撤销")
                library.status = "archived"
                library.deleted_at = None
                library.restore_until = None
            else:  # protected by Pydantic, retained for direct callers
                raise BusinessException("不支持的题库状态操作")
            if action in {"suspend", "restore", "archive", "delete"}:
                await QuizV2Service.sync_sessions_for_library(
                    db, library_id, now=now
                )
            library.updated_by = admin_id
            library.lock_version += 1
            self._audit(
                db,
                admin_id=admin_id,
                action=f"library.{action}",
                object_type="library",
                object_id=library_id,
                before=before,
                after=self._library_fields(library),
            )
            await db.commit()
            await db.refresh(library)
            counts = await self._library_counts(db, [library_id])
            return self._library_response(library, counts[library_id])

    async def list_course_options(
        self,
        *,
        keyword: str | None = None,
        limit: int = 100,
    ) -> list[AdminQuizCourseOptionResponse]:
        """Return only fields needed by the quiz course-binding selector.

        Quiz administrators intentionally cannot call the general course
        management API.  Keeping this projection under ``/admin/quiz`` lets
        them complete course binding without granting ``course:list`` or
        exposing course-management fields.
        """

        async with get_db_ctx() as db:
            query = select(Course.id, Course.title, Course.status).where(
                Course.status == "published"
            )
            if keyword and keyword.strip():
                query = query.where(Course.title.ilike(f"%{keyword.strip()}%"))
            rows = (
                await db.execute(
                    query.order_by(Course.title.asc(), Course.id.asc()).limit(limit)
                )
            ).all()
            return [
                AdminQuizCourseOptionResponse(
                    id=row.id, title=row.title, status=row.status
                )
                for row in rows
            ]

    async def list_course_bindings(
        self, library_id: int
    ) -> list[AdminQuizCourseBindingResponse]:
        async with get_db_ctx() as db:
            if await db.get(QuizLibrary, library_id) is None:
                raise NotFoundException("题库")
            bindings = list(
                (
                    await db.execute(
                        select(QuizCourseLibraryBinding)
                        .where(QuizCourseLibraryBinding.library_id == library_id)
                        .order_by(QuizCourseLibraryBinding.id.asc())
                    )
                ).scalars()
            )
            return [
                AdminQuizCourseBindingResponse.model_validate(item)
                for item in bindings
            ]

    async def create_course_binding(
        self,
        library_id: int,
        data: AdminQuizCourseBindingCreate,
        *,
        admin_id: int,
    ) -> AdminQuizCourseBindingResponse:
        job = await CourseEntitlementService.create_binding(
            data.course_id, library_id, admin_id=admin_id
        )
        async with get_db_ctx() as db:
            binding = await db.get(QuizCourseLibraryBinding, job.binding_id)
            if binding is None:
                raise NotFoundException("课程题库绑定")
            await db.refresh(binding)
            return AdminQuizCourseBindingResponse.model_validate(binding)

    async def set_course_binding_status(
        self,
        binding_id: int,
        data: AdminQuizCourseBindingStatusUpdate,
        *,
        admin_id: int,
    ) -> AdminQuizCourseBindingResponse:
        await CourseEntitlementService.set_binding_status(
            binding_id,
            str(data.status),
            admin_id=admin_id,
            expected_lock_version=data.lock_version,
        )
        async with get_db_ctx() as db:
            binding = await db.get(QuizCourseLibraryBinding, binding_id)
            if binding is None:
                raise NotFoundException("课程题库绑定")
            await db.refresh(binding)
            return AdminQuizCourseBindingResponse.model_validate(binding)

    @staticmethod
    def _point_response(
        point: QuizKnowledgePoint, question_count: int = 0
    ) -> AdminQuizKnowledgePointResponse:
        return AdminQuizKnowledgePointResponse(
            id=int(point.id),
            library_id=int(point.library_id),
            module_id=int(point.module_id),
            name=point.name,
            normalized_name=point.normalized_name,
            description=point.description,
            status=point.status,
            system_kind=point.system_kind,
            sort_order=int(point.sort_order),
            lock_version=int(point.lock_version),
            question_count=question_count,
            disabled_at=point.disabled_at,
            deleted_at=point.deleted_at,
            restore_until=point.restore_until,
            created_at=point.created_at,
            updated_at=point.updated_at,
        )

    @classmethod
    def _module_response(
        cls,
        module: QuizModule,
        points: list[QuizKnowledgePoint],
        question_counts: dict[int, int],
    ) -> AdminQuizModuleResponse:
        point_responses = [
            cls._point_response(point, question_counts.get(int(point.id), 0))
            for point in points
        ]
        return AdminQuizModuleResponse(
            id=int(module.id),
            library_id=int(module.library_id),
            name=module.name,
            normalized_name=module.normalized_name,
            description=module.description,
            status=module.status,
            system_kind=module.system_kind,
            sort_order=int(module.sort_order),
            lock_version=int(module.lock_version),
            question_count=sum(item.question_count for item in point_responses),
            knowledge_points=point_responses,
            disabled_at=module.disabled_at,
            deleted_at=module.deleted_at,
            restore_until=module.restore_until,
            created_at=module.created_at,
            updated_at=module.updated_at,
        )

    async def get_content_tree(self, library_id: int) -> AdminQuizContentTreeResponse:
        async with get_db_ctx() as db:
            if await db.get(QuizLibrary, library_id) is None:
                raise NotFoundException("题库")
            modules = list(
                (
                    await db.execute(
                        select(QuizModule)
                        .where(QuizModule.library_id == library_id)
                        .order_by(QuizModule.sort_order.asc(), QuizModule.id.asc())
                    )
                ).scalars()
            )
            points = list(
                (
                    await db.execute(
                        select(QuizKnowledgePoint)
                        .where(
                            QuizKnowledgePoint.library_id == library_id,
                        )
                        .order_by(
                            QuizKnowledgePoint.module_id.asc(),
                            QuizKnowledgePoint.sort_order.asc(),
                            QuizKnowledgePoint.id.asc(),
                        )
                    )
                ).scalars()
            )
            question_counts = {
                int(point_id): int(count)
                for point_id, count in (
                    await db.execute(
                        select(QuizQuestion.knowledge_point_id, func.count())
                        .where(
                            QuizQuestion.library_id == library_id,
                            QuizQuestion.knowledge_point_id.is_not(None),
                            QuizQuestion.status != "deleted",
                        )
                        .group_by(QuizQuestion.knowledge_point_id)
                    )
                ).all()
            }
            by_module: dict[int, list[QuizKnowledgePoint]] = {}
            for point in points:
                by_module.setdefault(int(point.module_id), []).append(point)
            return AdminQuizContentTreeResponse(
                library_id=library_id,
                modules=[
                    self._module_response(
                        module,
                        by_module.get(int(module.id), []),
                        question_counts,
                    )
                    for module in modules
                ],
            )

    async def create_module(
        self, data: AdminQuizModuleCreate, *, admin_id: int
    ) -> AdminQuizModuleResponse:
        name = normalize_category_name(data.name)
        async with get_db_ctx() as db:
            library = await self._locked(db, QuizLibrary, data.library_id)
            if library is None:
                raise NotFoundException("题库")
            if library.status in {"archived", "deleted"}:
                raise BusinessException("已归档或删除题库不可新增模块")
            module = QuizModule(
                library_id=data.library_id,
                name=name,
                normalized_name=name,
                description=data.description,
                status="active",
                system_kind="none",
                name_reserved=True,
                sort_order=data.sort_order,
                lock_version=1,
                created_by=admin_id,
                updated_by=admin_id,
            )
            db.add(module)
            try:
                await db.flush()
                self._audit(
                    db,
                    admin_id=admin_id,
                    action="module.create",
                    object_type="module",
                    object_id=int(module.id),
                    before={},
                    after=self._module_fields(module),
                    permission="quiz_content_edit",
                )
                await db.commit()
            except IntegrityError as exc:
                await db.rollback()
                raise ValidationException("同一题库内模块名称不能重复") from exc
            await db.refresh(module)
            return self._module_response(module, [], {})

    async def update_module(
        self, module_id: int, data: AdminQuizModuleUpdate, *, admin_id: int
    ) -> AdminQuizModuleResponse:
        async with get_db_ctx() as db:
            module = await self._locked(db, QuizModule, module_id)
            if module is None:
                raise NotFoundException("模块")
            self._check_version(module, data.lock_version)
            if module.status == "deleted" or module.system_kind != "none":
                raise BusinessException("系统或已删除模块不可编辑")
            before = self._module_fields(module)
            fields = data.model_fields_set - {"lock_version"}
            if "name" in fields and data.name is not None:
                module.name = normalize_category_name(data.name)
                module.normalized_name = module.name
            for field in ("description", "sort_order"):
                if field in fields:
                    setattr(module, field, getattr(data, field))
            module.updated_by = admin_id
            module.lock_version += 1
            self._audit(
                db,
                admin_id=admin_id,
                action="module.update",
                object_type="module",
                object_id=module_id,
                before=before,
                after=self._module_fields(module),
                permission="quiz_content_edit",
            )
            try:
                await db.commit()
            except IntegrityError as exc:
                await db.rollback()
                raise ValidationException("同一题库内模块名称不能重复") from exc
            await db.refresh(module)
            return self._module_response(module, [], {})

    async def set_module_status(
        self, module_id: int, data: AdminQuizContentStatusUpdate, *, admin_id: int
    ) -> AdminQuizModuleResponse:
        async with get_db_ctx() as db:
            module = await self._locked(db, QuizModule, module_id)
            if module is None:
                raise NotFoundException("模块")
            self._check_version(module, data.lock_version)
            if module.status == "deleted" or module.system_kind != "none":
                raise BusinessException("系统或已删除模块不可变更状态")
            target = str(data.status)
            if module.status == target:
                raise BusinessException("模块已经是目标状态")
            if target == "disabled":
                await self._ensure_published_library_remains_usable(
                    db,
                    int(module.library_id),
                    excluded_module_ids={module_id},
                )
            before = self._module_fields(module)
            module.status = target
            module.disabled_at = self._now() if target == "disabled" else None
            module.updated_by = admin_id
            module.lock_version += 1
            self._audit(
                db,
                admin_id=admin_id,
                action=f"module.{target}",
                object_type="module",
                object_id=module_id,
                before=before,
                after=self._module_fields(module),
                permission="quiz_content_edit",
            )
            await db.commit()
            await db.refresh(module)
            return self._module_response(module, [], {})

    async def delete_module(
        self, module_id: int, data: AdminQuizVersionRequest, *, admin_id: int
    ) -> None:
        async with get_db_ctx() as db:
            module = await self._locked(db, QuizModule, module_id)
            if module is None:
                raise NotFoundException("模块")
            self._check_version(module, data.lock_version)
            if module.system_kind != "none":
                raise BusinessException("系统待整理模块需先清空，由迁移清理流程移除")
            if module.status != "disabled":
                raise BusinessException("只有已停用模块可以删除")
            point_count = int(
                (
                    await db.execute(
                        select(func.count()).where(
                            QuizKnowledgePoint.module_id == module_id,
                            QuizKnowledgePoint.status != "deleted",
                        )
                    )
                ).scalar()
                or 0
            )
            if point_count:
                raise BusinessException(f"模块仍有 {point_count} 个知识点，请先逐项删除")
            before = self._module_fields(module)
            now = self._now()
            module.status = "deleted"
            module.deleted_at = now
            module.restore_until = now + _RESTORE_WINDOW
            module.updated_by = admin_id
            module.lock_version += 1
            self._audit(
                db,
                admin_id=admin_id,
                action="module.delete",
                object_type="module",
                object_id=module_id,
                before=before,
                after=self._module_fields(module),
                permission="quiz_content_edit",
            )
            await db.commit()

    async def undo_delete_module(
        self, module_id: int, data: AdminQuizVersionRequest, *, admin_id: int
    ) -> AdminQuizModuleResponse:
        async with get_db_ctx() as db:
            module = await self._locked(db, QuizModule, module_id)
            if module is None:
                raise NotFoundException("模块")
            self._check_version(module, data.lock_version)
            now = self._now()
            if module.status != "deleted":
                raise BusinessException("只有已删除模块可以撤销删除")
            if module.restore_until is None or module.restore_until <= now:
                raise BusinessException("删除已超过 7 天，无法撤销")
            before = self._module_fields(module)
            module.status = "disabled"
            module.deleted_at = None
            module.restore_until = None
            module.updated_by = admin_id
            module.lock_version += 1
            self._audit(
                db,
                admin_id=admin_id,
                action="module.undo_delete",
                object_type="module",
                object_id=module_id,
                before=before,
                after=self._module_fields(module),
                permission="quiz_content_edit",
            )
            await db.commit()
            await db.refresh(module)
            return self._module_response(module, [], {})

    async def create_knowledge_point(
        self, data: AdminQuizKnowledgePointCreate, *, admin_id: int
    ) -> AdminQuizKnowledgePointResponse:
        name = normalize_category_name(data.name)
        async with get_db_ctx() as db:
            module = await self._locked(db, QuizModule, data.module_id)
            if module is None:
                raise NotFoundException("模块")
            if module.status != "active" or module.system_kind != "none":
                raise BusinessException("目标模块不可用")
            point = QuizKnowledgePoint(
                library_id=module.library_id,
                module_id=module.id,
                name=name,
                normalized_name=name,
                description=data.description,
                status="active",
                system_kind="none",
                name_reserved=True,
                sort_order=data.sort_order,
                lock_version=1,
                created_by=admin_id,
                updated_by=admin_id,
            )
            db.add(point)
            try:
                await db.flush()
                self._audit(
                    db,
                    admin_id=admin_id,
                    action="knowledge_point.create",
                    object_type="knowledge_point",
                    object_id=int(point.id),
                    before={},
                    after=self._point_fields(point),
                    permission="quiz_content_edit",
                )
                await db.commit()
            except IntegrityError as exc:
                await db.rollback()
                raise ValidationException("同一模块内知识点名称不能重复") from exc
            await db.refresh(point)
            return self._point_response(point)

    async def update_knowledge_point(
        self,
        point_id: int,
        data: AdminQuizKnowledgePointUpdate,
        *,
        admin_id: int,
    ) -> AdminQuizKnowledgePointResponse:
        async with get_db_ctx() as db:
            point = await self._locked(db, QuizKnowledgePoint, point_id)
            if point is None:
                raise NotFoundException("知识点")
            self._check_version(point, data.lock_version)
            if point.status == "deleted" or point.system_kind != "none":
                raise BusinessException("系统或已删除知识点不可编辑")
            before = self._point_fields(point)
            fields = data.model_fields_set - {"lock_version"}
            if "module_id" in fields and data.module_id is not None:
                target = await self._locked(db, QuizModule, data.module_id)
                if target is None:
                    raise NotFoundException("目标模块")
                if (
                    target.library_id != point.library_id
                    or target.status != "active"
                    or target.system_kind != "none"
                ):
                    raise BusinessException("知识点只能移动到同一题库的启用模块")
                point.module_id = target.id
            if "name" in fields and data.name is not None:
                point.name = normalize_category_name(data.name)
                point.normalized_name = point.name
            for field in ("description", "sort_order"):
                if field in fields:
                    setattr(point, field, getattr(data, field))
            point.updated_by = admin_id
            point.lock_version += 1
            self._audit(
                db,
                admin_id=admin_id,
                action="knowledge_point.update",
                object_type="knowledge_point",
                object_id=point_id,
                before=before,
                after=self._point_fields(point),
                permission="quiz_content_edit",
            )
            try:
                await db.commit()
            except IntegrityError as exc:
                await db.rollback()
                raise ValidationException("目标模块内知识点名称不能重复") from exc
            await db.refresh(point)
            question_count = int(
                (
                    await db.execute(
                        select(func.count()).where(
                            QuizQuestion.knowledge_point_id == point_id,
                            QuizQuestion.status != "deleted",
                        )
                    )
                ).scalar()
                or 0
            )
            return self._point_response(point, question_count)

    async def set_knowledge_point_status(
        self, point_id: int, data: AdminQuizContentStatusUpdate, *, admin_id: int
    ) -> AdminQuizKnowledgePointResponse:
        async with get_db_ctx() as db:
            point = await self._locked(db, QuizKnowledgePoint, point_id)
            if point is None:
                raise NotFoundException("知识点")
            self._check_version(point, data.lock_version)
            if point.status == "deleted" or point.system_kind != "none":
                raise BusinessException("系统或已删除知识点不可变更状态")
            target = str(data.status)
            if point.status == target:
                raise BusinessException("知识点已经是目标状态")
            if target == "disabled":
                await self._ensure_published_library_remains_usable(
                    db,
                    int(point.library_id),
                    excluded_point_ids={point_id},
                )
            before = self._point_fields(point)
            point.status = target
            point.disabled_at = self._now() if target == "disabled" else None
            point.updated_by = admin_id
            point.lock_version += 1
            self._audit(
                db,
                admin_id=admin_id,
                action=f"knowledge_point.{target}",
                object_type="knowledge_point",
                object_id=point_id,
                before=before,
                after=self._point_fields(point),
                permission="quiz_content_edit",
            )
            await db.commit()
            await db.refresh(point)
            question_count = int(
                (
                    await db.execute(
                        select(func.count()).where(
                            QuizQuestion.knowledge_point_id == point_id,
                            QuizQuestion.status != "deleted",
                        )
                    )
                ).scalar()
                or 0
            )
            return self._point_response(point, question_count)

    async def delete_knowledge_point(
        self, point_id: int, data: AdminQuizVersionRequest, *, admin_id: int
    ) -> None:
        async with get_db_ctx() as db:
            point = await self._locked(db, QuizKnowledgePoint, point_id)
            if point is None:
                raise NotFoundException("知识点")
            self._check_version(point, data.lock_version)
            if point.system_kind != "none":
                raise BusinessException("系统未分类知识点需先清空，由迁移清理流程移除")
            if point.status != "disabled":
                raise BusinessException("只有已停用知识点可以删除")
            question_count = int(
                (
                    await db.execute(
                        select(func.count()).where(
                            QuizQuestion.knowledge_point_id == point_id,
                            QuizQuestion.status != "deleted",
                        )
                    )
                ).scalar()
                or 0
            )
            if question_count:
                raise BusinessException(f"知识点仍有 {question_count} 道题，请先逐项删除")
            before = self._point_fields(point)
            now = self._now()
            point.status = "deleted"
            point.deleted_at = now
            point.restore_until = now + _RESTORE_WINDOW
            point.updated_by = admin_id
            point.lock_version += 1
            self._audit(
                db,
                admin_id=admin_id,
                action="knowledge_point.delete",
                object_type="knowledge_point",
                object_id=point_id,
                before=before,
                after=self._point_fields(point),
                permission="quiz_content_edit",
            )
            await db.commit()

    async def undo_delete_knowledge_point(
        self, point_id: int, data: AdminQuizVersionRequest, *, admin_id: int
    ) -> AdminQuizKnowledgePointResponse:
        async with get_db_ctx() as db:
            point = await self._locked(db, QuizKnowledgePoint, point_id)
            if point is None:
                raise NotFoundException("知识点")
            self._check_version(point, data.lock_version)
            now = self._now()
            if point.status != "deleted":
                raise BusinessException("只有已删除知识点可以撤销删除")
            if point.restore_until is None or point.restore_until <= now:
                raise BusinessException("删除已超过 7 天，无法撤销")
            before = self._point_fields(point)
            point.status = "disabled"
            point.deleted_at = None
            point.restore_until = None
            point.updated_by = admin_id
            point.lock_version += 1
            self._audit(
                db,
                admin_id=admin_id,
                action="knowledge_point.undo_delete",
                object_type="knowledge_point",
                object_id=point_id,
                before=before,
                after=self._point_fields(point),
                permission="quiz_content_edit",
            )
            await db.commit()
            await db.refresh(point)
            return self._point_response(point)

    # ── V2 logical questions and immutable revisions ──

    async def list_questions(
        self, query: AdminQuizQuestionQuery
    ) -> PaginatedData[AdminQuizQuestionResponse]:
        async with get_db_ctx() as db:
            stmt = select(QuizQuestion).where(QuizQuestion.library_id.is_not(None))
            if not query.include_deleted:
                stmt = stmt.where(QuizQuestion.status != "deleted")
            if query.library_id is not None:
                stmt = stmt.where(QuizQuestion.library_id == query.library_id)
            if query.module_id is not None:
                point_ids = select(QuizKnowledgePoint.id).where(
                    QuizKnowledgePoint.module_id == query.module_id
                )
                stmt = stmt.where(QuizQuestion.knowledge_point_id.in_(point_ids))
            if query.knowledge_point_id is not None:
                stmt = stmt.where(
                    QuizQuestion.knowledge_point_id == query.knowledge_point_id
                )
            if query.question_type is not None:
                stmt = stmt.where(QuizQuestion.question_type == str(query.question_type))
            if query.status is not None:
                stmt = stmt.where(QuizQuestion.status == str(query.status))
            if query.keyword:
                pattern = f"%{query.keyword.strip()}%"
                stmt = stmt.where(
                    or_(
                        QuizQuestion.question_text.ilike(pattern),
                        QuizQuestion.normalized_question_text.ilike(pattern),
                    )
                )
            total = int(
                (
                    await db.execute(select(func.count()).select_from(stmt.subquery()))
                ).scalar()
                or 0
            )
            questions = list(
                (
                    await db.execute(
                        stmt.order_by(
                            QuizQuestion.updated_at.desc(), QuizQuestion.id.desc()
                        )
                        .offset((query.page - 1) * query.page_size)
                        .limit(query.page_size)
                    )
                ).scalars()
            )
            return PaginatedData(
                items=[await self._question_response(db, item) for item in questions],
                total=total,
                page=query.page,
                page_size=query.page_size,
            )

    async def get_question(self, question_id: int) -> AdminQuizQuestionResponse:
        async with get_db_ctx() as db:
            question = await db.get(QuizQuestion, question_id)
            if question is None or question.library_id is None:
                raise NotFoundException("题目")
            return await self._question_response(db, question)

    async def list_question_revisions(
        self, question_id: int
    ) -> list[AdminQuizQuestionRevisionResponse]:
        async with get_db_ctx() as db:
            question = await db.get(QuizQuestion, question_id)
            if question is None or question.library_id is None:
                raise NotFoundException("题目")
            revisions = list(
                (
                    await db.execute(
                        select(QuizQuestionRevision)
                        .where(QuizQuestionRevision.question_id == question_id)
                        .order_by(QuizQuestionRevision.revision_no.desc())
                    )
                ).scalars()
            )
            return [self._revision_response(item) for item in revisions]

    async def create_question(
        self, data: AdminQuizQuestionCreate, *, admin_id: int
    ) -> AdminQuizQuestionResponse:
        if data.knowledge_point_id is None or data.category_id is not None:
            raise ValidationException("V2 题目必须且只能选择知识点")
        normalized = self._normalize_question(
            question_type=data.question_type,
            question_text=data.question_text,
            options=data.options,
            correct_answer=data.correct_answer,
            explanation=data.explanation,
            require_publishable=False,
        )
        async with get_db_ctx() as db:
            library, _module, point = await self._active_point(
                db, data.knowledge_point_id
            )
            if await self._stem_taken(
                db,
                library_id=int(library.id),
                question_text_hash=normalized.question_text_hash,
            ):
                raise ValidationException("同一题库内规范化题干不能重复")
            question = QuizQuestion(
                library_id=library.id,
                knowledge_point_id=point.id,
                category_id=None,
                question_type=normalized.question_type.value,
                status="draft",
                question_text=normalized.question_text,
                normalized_question_text=normalized.normalized_question_text,
                question_text_hash=normalized.question_text_hash,
                options=normalized.options,
                correct_answer=normalized.correct_answer,
                explanation=normalized.explanation,
                ever_published=False,
                stem_reserved=True,
                lock_version=1,
                created_by=admin_id,
                updated_by=admin_id,
            )
            db.add(question)
            try:
                await db.flush()
                revision = self._new_revision(
                    question,
                    normalized,
                    revision_no=1,
                    status="draft",
                    admin_id=admin_id,
                )
                db.add(revision)
                await db.flush()
                question.pending_revision_id = revision.id
                self._audit(
                    db,
                    admin_id=admin_id,
                    action="question.create",
                    object_type="question",
                    object_id=int(question.id),
                    before={},
                    after=self._question_fields(question),
                    permission="quiz_content_edit",
                )
                await db.commit()
            except IntegrityError as exc:
                await db.rollback()
                raise ValidationException("同一题库内规范化题干不能重复") from exc
            await db.refresh(question)
            return await self._question_response(db, question)

    async def update_question(
        self,
        question_id: int,
        data: AdminQuizQuestionUpdate,
        *,
        admin_id: int,
    ) -> AdminQuizQuestionResponse:
        async with get_db_ctx() as db:
            question = await self._locked(db, QuizQuestion, question_id)
            if question is None or question.library_id is None:
                raise NotFoundException("题目")
            self._check_version(question, data.lock_version)
            if question.status == "deleted":
                raise BusinessException("已删除题目不可编辑")
            if data.category_id is not None:
                raise ValidationException("V2 题目不能移动到旧分类")
            target_point = None
            if data.knowledge_point_id is not None:
                target_library, _module, target_point = await self._active_point(
                    db, data.knowledge_point_id
                )
                if int(target_library.id) != int(question.library_id):
                    raise BusinessException("题目只能在同一题库内移动")
            fields = data.model_fields_set - {
                "lock_version",
                "category_id",
                "knowledge_point_id",
            }
            content_changed = bool(fields)
            base_revision = None
            if question.pending_revision_id is not None:
                base_revision = await db.get(
                    QuizQuestionRevision, question.pending_revision_id
                )
            if base_revision is None and question.current_revision_id is not None:
                base_revision = await db.get(
                    QuizQuestionRevision, question.current_revision_id
                )
            source = base_revision or question
            normalized = self._normalize_question(
                question_type=(
                    data.question_type if "question_type" in fields else source.question_type
                ),
                question_text=(
                    data.question_text if "question_text" in fields else source.question_text
                ),
                options=data.options if "options" in fields else source.options,
                correct_answer=(
                    data.correct_answer
                    if "correct_answer" in fields
                    else source.correct_answer
                ),
                explanation=(
                    data.explanation if "explanation" in fields else source.explanation
                ),
                require_publishable=bool(question.ever_published),
            )
            if await self._stem_taken(
                db,
                library_id=int(question.library_id),
                question_text_hash=normalized.question_text_hash,
                exclude_id=int(question.id),
            ):
                raise ValidationException("同一题库内规范化题干不能重复")
            before = self._question_fields(question)
            if target_point is not None:
                question.knowledge_point_id = target_point.id
            if content_changed:
                next_no = int(
                    (
                        await db.execute(
                            select(func.max(QuizQuestionRevision.revision_no)).where(
                                QuizQuestionRevision.question_id == question.id
                            )
                        )
                    ).scalar()
                    or 0
                ) + 1
                old_pending = None
                if question.pending_revision_id is not None:
                    old_pending = await db.get(
                        QuizQuestionRevision, question.pending_revision_id
                    )
                    if old_pending is not None:
                        old_pending.status = "discarded"
                        # Release the partial unique index before inserting the
                        # replacement draft revision.
                        await db.flush()
                revision = self._new_revision(
                    question,
                    normalized,
                    revision_no=next_no,
                    status="draft",
                    admin_id=admin_id,
                )
                db.add(revision)
                await db.flush()
                question.pending_revision_id = revision.id
                # Drafts have no public version, so their working projection may
                # track the new immutable draft. Published/disabled projections
                # stay pinned to the current published revision until publish.
                if not question.ever_published:
                    self._apply_content(question, normalized)
            question.updated_by = admin_id
            question.lock_version += 1
            self._audit(
                db,
                admin_id=admin_id,
                action="question.update",
                object_type="question",
                object_id=question_id,
                before=before,
                after=self._question_fields(question),
                permission="quiz_content_edit",
            )
            try:
                await db.commit()
            except IntegrityError as exc:
                await db.rollback()
                raise ValidationException("同一题库内规范化题干不能重复") from exc
            await db.refresh(question)
            return await self._question_response(db, question)

    async def publish_question_revision(
        self, question_id: int, data: AdminQuizVersionRequest, *, admin_id: int
    ) -> AdminQuizQuestionResponse:
        async with get_db_ctx() as db:
            question = await self._locked(db, QuizQuestion, question_id)
            if question is None or question.library_id is None:
                raise NotFoundException("题目")
            self._check_version(question, data.lock_version)
            if question.status == "deleted":
                raise BusinessException("已删除题目不可发布版本")
            if question.pending_revision_id is None:
                raise BusinessException("题目没有待发布版本")
            library, _module, point = await self._active_point(
                db, int(question.knowledge_point_id)
            )
            if int(library.id) != int(question.library_id) or int(point.id) != int(
                question.knowledge_point_id
            ):
                raise BusinessException("题目归属无效")
            pending = await db.get(
                QuizQuestionRevision, question.pending_revision_id
            )
            if pending is None or pending.status != "draft":
                raise ConflictException(self._CONFLICT)
            normalized = self._normalize_question(
                question_type=pending.question_type,
                question_text=pending.question_text,
                options=pending.options,
                correct_answer=pending.correct_answer,
                explanation=pending.explanation,
                require_publishable=True,
            )
            if await self._stem_taken(
                db,
                library_id=int(question.library_id),
                question_text_hash=normalized.question_text_hash,
                exclude_id=question_id,
            ):
                raise ValidationException("同一题库内规范化题干不能重复")
            before = self._question_fields(question)
            now = self._now()
            if question.current_revision_id is not None:
                current = await db.get(
                    QuizQuestionRevision, question.current_revision_id
                )
                if current is not None and current.status == "published":
                    current.status = "superseded"
            pending.status = "published"
            pending.published_at = now
            question.current_revision_id = pending.id
            question.pending_revision_id = None
            self._apply_content(question, normalized)
            if not question.ever_published:
                question.status = "published"
                question.ever_published = True
                question.published_at = now
                question.disabled_at = None
            # Publishing a new version of a disabled question intentionally
            # keeps the logical question disabled.
            question.updated_by = admin_id
            question.lock_version += 1
            self._audit(
                db,
                admin_id=admin_id,
                action="question.revision.publish",
                object_type="question",
                object_id=question_id,
                before=before,
                after=self._question_fields(question),
                permission="quiz_content_publish",
            )
            await db.commit()
            await db.refresh(question)
            return await self._question_response(db, question)

    async def transition_question(
        self,
        question_id: int,
        data: AdminQuizVersionRequest,
        action: str,
        *,
        admin_id: int,
    ) -> AdminQuizQuestionResponse:
        async with get_db_ctx() as db:
            question = await self._locked(db, QuizQuestion, question_id)
            if question is None or question.library_id is None:
                raise NotFoundException("题目")
            self._check_version(question, data.lock_version)
            now = self._now()
            before = self._question_fields(question)
            if action == "disable":
                if question.status != "published":
                    raise BusinessException("仅已发布题目可以停用")
                await self._ensure_published_library_remains_usable(
                    db,
                    int(question.library_id),
                    excluded_question_ids={question_id},
                )
                question.status = "disabled"
                question.disabled_at = now
            elif action == "restore":
                if question.status != "disabled":
                    raise BusinessException("仅已停用题目可以恢复")
                await self._active_point(db, int(question.knowledge_point_id))
                question.status = "published"
                # Preserve disabled_at as historical metadata, matching the
                # database lifecycle rule and legacy behavior.
            elif action == "delete":
                if question.status == "draft" and not question.ever_published:
                    question.status = "deleted"
                elif question.status != "disabled":
                    raise BusinessException("已发布题目必须先停用后删除")
                else:
                    question.status = "deleted"
                question.deleted_at = now
                question.restore_until = now + _RESTORE_WINDOW
            elif action == "undo_delete":
                if question.status != "deleted":
                    raise BusinessException("仅已删除题目可以撤销删除")
                if question.restore_until is None or question.restore_until <= now:
                    raise BusinessException("删除已超过 7 天，无法撤销")
                question.status = "disabled" if question.ever_published else "draft"
                question.deleted_at = None
                question.restore_until = None
                if not question.ever_published:
                    question.disabled_at = None
            else:
                raise BusinessException("不支持的题目状态操作")
            question.updated_by = admin_id
            question.lock_version += 1
            self._audit(
                db,
                admin_id=admin_id,
                action=f"question.{action}",
                object_type="question",
                object_id=question_id,
                before=before,
                after=self._question_fields(question),
                permission=(
                    "quiz_content_publish"
                    if action in {"disable", "restore"}
                    else "quiz_content_edit"
                ),
            )
            await db.commit()
            await db.refresh(question)
            return await self._question_response(db, question)

    async def batch_transition_questions(
        self,
        data: AdminQuizBatchRequest,
        action: str,
        *,
        admin_id: int,
    ) -> AdminQuizBatchResponse:
        """Atomically publish pending revisions or disable V2 questions."""

        errors: list[AdminQuizBatchItemError] = []
        async with get_db_ctx() as db:
            questions: dict[int, QuizQuestion] = {}
            pending_revisions: dict[int, QuizQuestionRevision] = {}
            for item in data.items:
                question = await self._locked(db, QuizQuestion, item.question_id)
                if question is None or question.library_id is None:
                    errors.append(AdminQuizBatchItemError(question_id=item.question_id, code=40300, field=None, message="V2 题目不存在"))
                    continue
                questions[item.question_id] = question
                if int(question.lock_version) != item.lock_version:
                    errors.append(AdminQuizBatchItemError(question_id=item.question_id, code=40201, field="lock_version", message=self._CONFLICT))
                    continue
                if action == "publish":
                    if question.status == "deleted":
                        errors.append(AdminQuizBatchItemError(question_id=item.question_id, code=40200, field="status", message="已删除题目不可发布版本"))
                        continue
                    if question.pending_revision_id is None:
                        errors.append(AdminQuizBatchItemError(question_id=item.question_id, code=40200, field="pending_revision_id", message="题目没有待发布版本"))
                        continue
                    try:
                        library, _module, point = await self._active_point(db, int(question.knowledge_point_id))
                    except (BusinessException, NotFoundException) as exc:
                        errors.append(AdminQuizBatchItemError(question_id=item.question_id, code=40200, field="knowledge_point_id", message=exc.message))
                        continue
                    if int(library.id) != int(question.library_id) or int(point.id) != int(question.knowledge_point_id):
                        errors.append(AdminQuizBatchItemError(question_id=item.question_id, code=40200, field="knowledge_point_id", message="题目归属无效"))
                        continue
                    pending = await db.get(QuizQuestionRevision, question.pending_revision_id)
                    if pending is None or pending.status != "draft":
                        errors.append(AdminQuizBatchItemError(question_id=item.question_id, code=40201, field="pending_revision_id", message=self._CONFLICT))
                        continue
                    try:
                        normalized = self._normalize_question(
                            question_type=pending.question_type,
                            question_text=pending.question_text,
                            options=pending.options,
                            correct_answer=pending.correct_answer,
                            explanation=pending.explanation,
                            require_publishable=True,
                        )
                    except ValidationException as exc:
                        errors.append(AdminQuizBatchItemError(question_id=item.question_id, code=40001, field=None, message=exc.message))
                        continue
                    if await self._stem_taken(db, library_id=int(question.library_id), question_text_hash=normalized.question_text_hash, exclude_id=int(question.id)):
                        errors.append(AdminQuizBatchItemError(question_id=item.question_id, code=40001, field="question_text", message="同一题库内规范化题干不能重复"))
                        continue
                    pending_revisions[item.question_id] = pending
                elif action == "disable" and question.status != "published":
                    errors.append(AdminQuizBatchItemError(question_id=item.question_id, code=40200, field="status", message="仅已发布题目可以停用"))
            if errors:
                await db.rollback()
                return AdminQuizBatchResponse(succeeded=False, updated_count=0, errors=errors)

            if action == "disable":
                by_library: dict[int, set[int]] = {}
                for question_id, question in questions.items():
                    by_library.setdefault(int(question.library_id), set()).add(
                        question_id
                    )
                for library_id in sorted(by_library):
                    try:
                        await self._ensure_published_library_remains_usable(
                            db,
                            library_id,
                            excluded_question_ids=by_library[library_id],
                        )
                    except BusinessException as exc:
                        errors.extend(
                            AdminQuizBatchItemError(
                                question_id=question_id,
                                code=40200,
                                field="status",
                                message=exc.message,
                            )
                            for question_id in sorted(by_library[library_id])
                        )
                if errors:
                    await db.rollback()
                    return AdminQuizBatchResponse(
                        succeeded=False, updated_count=0, errors=errors
                    )

            now = self._now()
            for question_id, question in questions.items():
                before = self._question_fields(question)
                if action == "publish":
                    pending = pending_revisions[question_id]
                    if question.current_revision_id is not None:
                        current = await db.get(QuizQuestionRevision, question.current_revision_id)
                        if current is not None and current.status == "published":
                            current.status = "superseded"
                    pending.status = "published"
                    pending.published_at = now
                    question.current_revision_id = pending.id
                    question.pending_revision_id = None
                    normalized = self._normalize_question(
                        question_type=pending.question_type,
                        question_text=pending.question_text,
                        options=pending.options,
                        correct_answer=pending.correct_answer,
                        explanation=pending.explanation,
                        require_publishable=True,
                    )
                    self._apply_content(question, normalized)
                    if not question.ever_published:
                        question.status = "published"
                        question.ever_published = True
                        question.published_at = now
                        question.disabled_at = None
                else:
                    question.status = "disabled"
                    question.disabled_at = now
                question.updated_by = admin_id
                question.lock_version += 1
                self._audit(
                    db,
                    admin_id=admin_id,
                    action=f"question.batch_{action}",
                    object_type="question",
                    object_id=question_id,
                    before=before,
                    after=self._question_fields(question),
                    permission="quiz_content_publish",
                )
            await db.commit()
            return AdminQuizBatchResponse(succeeded=True, updated_count=len(questions), errors=[])

    async def is_v2_question(self, question_id: int) -> bool:
        async with get_db_ctx() as db:
            return (
                await db.execute(
                    select(QuizQuestion.id).where(
                        QuizQuestion.id == question_id,
                        QuizQuestion.library_id.is_not(None),
                    )
                )
            ).scalar_one_or_none() is not None

    async def migration_report(self) -> AdminQuizMigrationReportResponse:
        async with get_db_ctx() as db:
            libraries = list((await db.execute(select(QuizLibrary))).scalars())
            issues = list(
                (
                    await db.execute(
                        select(QuizMigrationIssue)
                        .where(QuizMigrationIssue.status == "open")
                        .order_by(
                            QuizMigrationIssue.library_id.asc(),
                            QuizMigrationIssue.id.asc(),
                        )
                    )
                ).scalars()
            )
            category_count = int(
                (
                    await db.execute(
                        select(func.count()).where(
                            QuizLegacyMigrationMap.legacy_object_type == "category"
                        )
                    )
                ).scalar()
                or 0
            )
            question_count = int(
                (
                    await db.execute(
                        select(func.count()).where(
                            QuizLegacyMigrationMap.legacy_object_type == "question"
                        )
                    )
                ).scalar()
                or 0
            )
            return AdminQuizMigrationReportResponse(
                generated_at=self._now(),
                library_count=len(libraries),
                ready_library_count=sum(
                    1 for item in libraries if item.migration_state == "ready"
                ),
                pending_library_count=sum(
                    1 for item in libraries if item.migration_state != "ready"
                ),
                open_blocking_issue_count=sum(
                    1 for item in issues if item.severity == "blocking"
                ),
                mapped_category_count=category_count,
                mapped_question_count=question_count,
                issues=[
                    AdminQuizMigrationIssueResponse.model_validate(item)
                    for item in issues
                ],
            )
