"""Entitlement-safe user catalog for fixed-hierarchy quiz libraries."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import and_, func, or_, select

from app.adapter.database import get_db_ctx
from app.domain.community.src.index import (
    QuizKnowledgePoint,
    QuizLibrary,
    QuizLibraryEntitlement,
    QuizModule,
    QuizPracticeAttempt,
    QuizPracticeSession,
    QuizPracticeSessionQuestion,
    QuizQuestion,
    QuizQuestionRevision,
    QuizWrongItem,
)
from app.domain.user.src.index import User
from app.port.exceptions import QuizV2Exception
from app.port.exceptions import BusinessException, ConflictException, NotFoundException
from app.domain.community.src.rule.quiz import QuizPracticeMode
from app.schemas.common import PaginatedData
from app.schemas.quiz_contract import (
    QuizKnowledgePointProgress,
    QuizKnowledgePointCatalogItem,
    QuizLibraryCatalogDetail,
    QuizLibraryCatalogItem,
    QuizLibraryProgressResponse,
    QuizModuleCatalogItem,
    QuizModuleProgress,
    QuizPracticeAttemptResult,
    QuizPracticeQuestionState,
    QuizPracticeScopePreview,
    QuizPracticeSessionCreate,
    QuizPracticeSessionResponse,
    QuizPracticeSkipResponse,
    QuizCategoryPathItem,
    QuizLibraryQuestionQuery,
    QuizPublicQuestion,
)


class QuizV2Service:
    _SESSION_WINDOW_DAYS = 7

    async def list_library_questions(
        self,
        user_id: int,
        library_id: int,
        query: QuizLibraryQuestionQuery,
    ) -> PaginatedData[QuizPublicQuestion]:
        """Browse questions of one visible library scope for manual assembly."""

        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            scope_id = int(query.scope_id) if query.scope_type != "library" else int(library_id)
            await self._resolve_scope(db, user_id, query.scope_type, scope_id)
            conditions = [
                QuizQuestion.library_id == library_id,
                QuizQuestion.status == "published",
                QuizQuestion.current_revision_id.is_not(None),
                QuizKnowledgePoint.status == "active",
                QuizKnowledgePoint.system_kind == "none",
            ]
            if query.scope_type == "knowledge_point":
                conditions.append(QuizQuestion.knowledge_point_id == scope_id)
            elif query.scope_type == "module":
                conditions.append(
                    QuizQuestion.knowledge_point_id.in_(
                        select(QuizKnowledgePoint.id).where(
                            QuizKnowledgePoint.module_id == scope_id,
                            QuizKnowledgePoint.status == "active",
                            QuizKnowledgePoint.system_kind == "none",
                        )
                    )
                )
            else:
                conditions.append(
                    QuizQuestion.knowledge_point_id.in_(
                        select(QuizKnowledgePoint.id).where(
                            QuizKnowledgePoint.library_id == library_id,
                            QuizKnowledgePoint.status == "active",
                            QuizKnowledgePoint.system_kind == "none",
                        )
                    )
                )
            if query.question_type is not None:
                conditions.append(QuizQuestion.question_type == query.question_type)
            base = (
                select(QuizQuestion)
                .join(
                    QuizKnowledgePoint,
                    QuizKnowledgePoint.id == QuizQuestion.knowledge_point_id,
                )
                .where(*conditions)
            )
            total = int(
                (
                    await db.execute(
                        select(func.count()).select_from(base.subquery())
                    )
                ).scalar()
                or 0
            )
            rows = list(
                (
                    await db.execute(
                        base.order_by(QuizQuestion.id.asc())
                        .offset((query.page - 1) * query.page_size)
                        .limit(query.page_size)
                    )
                ).scalars()
            )
            items = [
                QuizPublicQuestion(
                    id=int(question.id),
                    category_id=None,
                    library_id=int(question.library_id),
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
                    question_type=question.question_type,
                    question_text=question.question_text,
                    options=dict(question.options or {}),
                    image_urls=list(question.image_urls or []),
                    option_image_urls=dict(
                        getattr(question, "option_image_urls", None) or {}
                    ),
                )
                for question in rows
            ]
            return PaginatedData(
                items=items,
                total=total,
                page=query.page,
                page_size=query.page_size,
            )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _not_found() -> QuizV2Exception:
        return QuizV2Exception(
            reason="quiz_library_not_found",
            message="题库不存在",
            http_status_code=404,
            code=40300,
        )

    @staticmethod
    def _session_expired() -> QuizV2Exception:
        return QuizV2Exception(
            reason="practice_session_expired",
            message="练习会话已过期",
            http_status_code=410,
            code=40204,
        )

    @staticmethod
    def _session_terminated() -> QuizV2Exception:
        return QuizV2Exception(
            reason="practice_session_terminated",
            message="题库已归档或删除，本轮练习已终止",
            http_status_code=410,
            code=40204,
        )

    @staticmethod
    def _session_paused(reason: str) -> QuizV2Exception:
        if reason == "quiz_entitlement_inactive":
            message = "课程题库权益当前不可用，练习有效期已暂停"
        else:
            reason = "quiz_library_suspended"
            message = "题库当前暂停开放，练习有效期已暂停"
        return QuizV2Exception(
            reason=reason,
            message=message,
            http_status_code=423,
            code=40203,
        )

    @classmethod
    def _entitlement_exists(cls, user_id: int):
        now = cls._now()
        return (
            select(QuizLibraryEntitlement.id)
            .where(
                QuizLibraryEntitlement.user_id == user_id,
                QuizLibraryEntitlement.library_id == QuizLibrary.id,
                QuizLibraryEntitlement.status == "active",
                QuizLibraryEntitlement.starts_at <= now,
                or_(
                    QuizLibraryEntitlement.ends_at.is_(None),
                    QuizLibraryEntitlement.ends_at > now,
                ),
            )
            .exists()
        )

    @classmethod
    def _visible_clause(cls, user_id: int):
        return or_(
            QuizLibrary.access_mode == "free",
            and_(
                QuizLibrary.access_mode == "course_entitlement",
                cls._entitlement_exists(user_id),
            ),
        )

    @staticmethod
    async def _require_user(db, user_id: int) -> None:
        user = await db.get(User, user_id)
        if user is None or not user.is_active:
            raise QuizV2Service._not_found()

    @staticmethod
    async def _counts(db, library_ids: list[int]) -> dict[int, tuple[int, int]]:
        output = {library_id: (0, 0) for library_id in library_ids}
        if not library_ids:
            return output
        rows = (
            await db.execute(
                select(
                    QuizQuestion.library_id,
                    func.count(QuizQuestion.id),
                    func.count(func.distinct(QuizKnowledgePoint.module_id)),
                )
                .join(
                    QuizKnowledgePoint,
                    QuizKnowledgePoint.id == QuizQuestion.knowledge_point_id,
                )
                .join(QuizModule, QuizModule.id == QuizKnowledgePoint.module_id)
                .where(
                    QuizQuestion.library_id.in_(library_ids),
                    QuizQuestion.status == "published",
                    QuizQuestion.current_revision_id.is_not(None),
                    QuizKnowledgePoint.status == "active",
                    QuizModule.status == "active",
                )
                .group_by(QuizQuestion.library_id)
            )
        ).all()
        for library_id, question_count, module_count in rows:
            output[int(library_id)] = (int(question_count), int(module_count))
        return output

    @staticmethod
    def _summary(
        library: QuizLibrary, question_count: int, module_count: int
    ) -> QuizLibraryCatalogItem:
        return QuizLibraryCatalogItem(
            id=int(library.id),
            library_code=library.library_code,
            name=library.name,
            description=library.description or "",
            cover_url=library.cover_url or "",
            access_mode=library.access_mode,
            question_count=question_count,
            module_count=module_count,
        )

    async def list_libraries(self, user_id: int) -> list[QuizLibraryCatalogItem]:
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            libraries = list(
                (
                    await db.execute(
                        select(QuizLibrary)
                        .where(
                            QuizLibrary.v2_enabled.is_(True),
                            QuizLibrary.status == "published",
                            self._visible_clause(user_id),
                        )
                        .order_by(QuizLibrary.sort_order.asc(), QuizLibrary.id.asc())
                    )
                ).scalars()
            )
            counts = await self._counts(db, [int(item.id) for item in libraries])
            return [
                self._summary(item, *counts[int(item.id)])
                for item in libraries
                if counts[int(item.id)][0] > 0
            ]

    async def _require_accessible_library(
        self, db, user_id: int, library_id: int
    ) -> QuizLibrary:
        library = await db.get(QuizLibrary, library_id)
        if library is None:
            raise self._not_found()
        # Deliberately evaluate entitlement before reporting lifecycle state so
        # an unentitled caller cannot infer that a paid library exists.
        entitled = bool(
            (
                await db.execute(
                    select(QuizLibrary.id).where(
                        QuizLibrary.id == library_id,
                        self._visible_clause(user_id),
                    )
                )
            ).scalar_one_or_none()
        )
        if not entitled:
            raise self._not_found()
        if library.status == "suspended":
            raise QuizV2Exception(
                reason="quiz_library_suspended",
                message="题库已暂停开放",
                http_status_code=423,
                code=40203,
            )
        if library.status != "published" or not library.v2_enabled:
            raise self._not_found()
        return library

    @classmethod
    async def _session_access_state(
        cls,
        db,
        session: QuizPracticeSession,
        now: datetime,
    ) -> tuple[str, str | None, datetime]:
        """Resolve access without leaking catalog visibility to other users.

        Session ownership has already been established before this method is
        called, so it can distinguish temporary entitlement loss from a
        missing library.  Catalog/detail endpoints intentionally keep using
        the indistinguishable 404 contract.
        """

        library = await db.get(QuizLibrary, session.library_id)
        if library is None or library.status in {"archived", "deleted"}:
            event_at = (
                getattr(library, "archived_at", None)
                or getattr(library, "deleted_at", None)
                or now
            )
            return "terminated", "library_unavailable", event_at
        if library.status != "published" or not library.v2_enabled:
            return (
                "paused",
                "quiz_library_suspended",
                library.suspended_at or now,
            )
        if library.access_mode == "free":
            return "active", None, now
        if library.access_mode != "course_entitlement":
            return "paused", "quiz_entitlement_inactive", now

        entitlements = list(
            (
                await db.execute(
                    select(QuizLibraryEntitlement).where(
                        QuizLibraryEntitlement.user_id == session.user_id,
                        QuizLibraryEntitlement.library_id == session.library_id,
                    )
                )
            ).scalars()
        )
        if any(
            item.status == "active"
            and item.starts_at <= now
            and (item.ends_at is None or item.ends_at > now)
            for item in entitlements
        ):
            return "active", None, now
        inactive_at = max(
            (
                value
                for item in entitlements
                for value in (item.revoked_at, item.ends_at)
                if value is not None and value <= now
            ),
            default=now,
        )
        return "paused", "quiz_entitlement_inactive", inactive_at

    @classmethod
    async def _sync_session_access(
        cls,
        db,
        session: QuizPracticeSession,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Synchronize one unfinished V2 session with library access state."""

        current = now or cls._now()
        if session.status not in {"in_progress", "paused"}:
            return False
        state, reason, event_at = await cls._session_access_state(db, session, current)
        changed = False

        if state == "terminated":
            session.status = "terminated"
            session.terminated_at = max(event_at, session.started_at)
            session.termination_reason = reason
            session.lock_version += 1
            return True

        if state == "paused":
            if session.status == "in_progress":
                # If the seven-day window ended before access was withdrawn,
                # the session was already expired and must not be revived.
                if session.expires_at is not None and session.expires_at <= event_at:
                    session.status = "expired"
                    session.expired_at = session.expires_at
                else:
                    session.status = "paused"
                    session.paused_at = max(event_at, session.started_at)
                    session.pause_reason = reason
                session.lock_version += 1
                changed = True
            elif session.pause_reason != reason:
                session.pause_reason = reason
                session.lock_version += 1
                changed = True
            return changed

        if session.status == "paused":
            paused_at = session.paused_at or current
            paused_for = max(timedelta(0), current - paused_at)
            if session.expires_at is not None:
                session.expires_at += paused_for
            session.paused_seconds += int(paused_for.total_seconds())
            session.status = "in_progress"
            session.paused_at = None
            session.pause_reason = None
            session.lock_version += 1
            changed = True

        if session.expires_at is not None and session.expires_at <= current:
            session.status = "expired"
            session.expired_at = current
            session.lock_version += 1
            changed = True
        return changed

    @classmethod
    async def sync_sessions_for_library(
        cls,
        db,
        library_id: int,
        *,
        user_id: int | None = None,
        now: datetime | None = None,
    ) -> int:
        """Synchronize all unfinished sessions after a lifecycle/access write."""

        stmt = select(QuizPracticeSession).where(
            QuizPracticeSession.library_id == library_id,
            QuizPracticeSession.scope_type.is_not(None),
            QuizPracticeSession.status.in_(("in_progress", "paused")),
        )
        if user_id is not None:
            stmt = stmt.where(QuizPracticeSession.user_id == user_id)
        sessions = list((await db.execute(stmt.with_for_update())).scalars())
        changed = 0
        current = now or cls._now()
        for session in sessions:
            if await cls._sync_session_access(db, session, now=current):
                changed += 1
        return changed

    @classmethod
    async def _require_usable_session(cls, db, session: QuizPracticeSession) -> bool:
        changed = await cls._sync_session_access(db, session)
        if session.status == "expired":
            await db.commit()
            raise cls._session_expired()
        if session.status == "terminated":
            await db.commit()
            raise cls._session_terminated()
        if session.status == "paused":
            await db.commit()
            raise cls._session_paused(session.pause_reason or "quiz_library_suspended")
        return changed

    async def get_library(
        self, user_id: int, library_id: int
    ) -> QuizLibraryCatalogDetail:
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            library = await self._require_accessible_library(db, user_id, library_id)
            modules = list(
                (
                    await db.execute(
                        select(QuizModule)
                        .where(
                            QuizModule.library_id == library_id,
                            QuizModule.status == "active",
                            QuizModule.system_kind == "none",
                        )
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
                            QuizKnowledgePoint.status == "active",
                            QuizKnowledgePoint.system_kind == "none",
                        )
                        .order_by(
                            QuizKnowledgePoint.module_id.asc(),
                            QuizKnowledgePoint.sort_order.asc(),
                            QuizKnowledgePoint.id.asc(),
                        )
                    )
                ).scalars()
            )
            counts = {
                int(point_id): int(count)
                for point_id, count in (
                    await db.execute(
                        select(QuizQuestion.knowledge_point_id, func.count())
                        .where(
                            QuizQuestion.library_id == library_id,
                            QuizQuestion.status == "published",
                            QuizQuestion.current_revision_id.is_not(None),
                        )
                        .group_by(QuizQuestion.knowledge_point_id)
                    )
                ).all()
            }
            by_module: dict[int, list[QuizKnowledgePointCatalogItem]] = {}
            for point in points:
                count = counts.get(int(point.id), 0)
                if not count:
                    continue
                by_module.setdefault(int(point.module_id), []).append(
                    QuizKnowledgePointCatalogItem(
                        id=int(point.id),
                        module_id=int(point.module_id),
                        name=point.name,
                        description=point.description,
                        sort_order=int(point.sort_order),
                        question_count=count,
                    )
                )
            module_items = [
                QuizModuleCatalogItem(
                    id=int(module.id),
                    library_id=library_id,
                    name=module.name,
                    description=module.description,
                    sort_order=int(module.sort_order),
                    question_count=sum(
                        item.question_count
                        for item in by_module.get(int(module.id), [])
                    ),
                    knowledge_points=by_module.get(int(module.id), []),
                )
                for module in modules
                if by_module.get(int(module.id))
            ]
            question_count = sum(item.question_count for item in module_items)
            return QuizLibraryCatalogDetail(
                **self._summary(
                    library, question_count, len(module_items)
                ).model_dump(),
                details=library.details,
                modules=module_items,
            )

    async def get_library_progress(
        self, user_id: int, library_id: int
    ) -> QuizLibraryProgressResponse:
        """Practice-only progress for every catalog node of one library.

        The counting rules intentionally mirror ``QuizPracticeService.get_stats``
        with an explicit scope: ``answered_questions`` is the number of distinct
        questions with at least one practice attempt and ``accuracy`` is the
        first-attempt accuracy. Exam answers are deliberately excluded so the
        catalog numbers always agree with the stats page.
        """

        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            await self._require_accessible_library(db, user_id, library_id)
            rows = (
                await db.execute(
                    select(
                        QuizQuestion.knowledge_point_id,
                        func.count(func.distinct(QuizQuestion.id)),
                        func.count(func.distinct(QuizQuestion.id)).filter(
                            QuizPracticeAttempt.id.is_not(None)
                        ),
                        func.count(QuizPracticeAttempt.id).filter(
                            QuizPracticeAttempt.is_first_attempt.is_(True)
                        ),
                        func.count(QuizPracticeAttempt.id).filter(
                            QuizPracticeAttempt.is_first_attempt.is_(True),
                            QuizPracticeAttempt.is_correct.is_(True),
                        ),
                    )
                    .select_from(QuizQuestion)
                    .outerjoin(
                        QuizPracticeSessionQuestion,
                        QuizPracticeSessionQuestion.question_id == QuizQuestion.id,
                    )
                    .outerjoin(
                        QuizPracticeAttempt,
                        and_(
                            QuizPracticeAttempt.session_question_id
                            == QuizPracticeSessionQuestion.id,
                            QuizPracticeAttempt.user_id == user_id,
                        ),
                    )
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
                    .group_by(QuizQuestion.knowledge_point_id)
                )
            ).all()
            by_point: dict[int, tuple[int, int, int, int]] = {}
            for point_id, total, answered, first_attempts, first_correct in rows:
                by_point[int(point_id)] = (
                    int(total),
                    int(answered),
                    int(first_attempts),
                    int(first_correct),
                )

            def summarize(
                totals: list[tuple[int, int, int, int]],
            ) -> tuple[int, int, Decimal]:
                question_count = sum(item[0] for item in totals)
                answered_questions = sum(item[1] for item in totals)
                first_attempts = sum(item[2] for item in totals)
                first_correct = sum(item[3] for item in totals)
                accuracy = (
                    (Decimal(first_correct) * Decimal("100") / Decimal(first_attempts)).quantize(
                        Decimal("0.1")
                    )
                    if first_attempts
                    else Decimal("0.0")
                )
                return question_count, answered_questions, accuracy

            modules = list(
                (
                    await db.execute(
                        select(QuizModule)
                        .where(
                            QuizModule.library_id == library_id,
                            QuizModule.status == "active",
                            QuizModule.system_kind == "none",
                        )
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
                            QuizKnowledgePoint.status == "active",
                            QuizKnowledgePoint.system_kind == "none",
                        )
                        .order_by(
                            QuizKnowledgePoint.module_id.asc(),
                            QuizKnowledgePoint.sort_order.asc(),
                            QuizKnowledgePoint.id.asc(),
                        )
                    )
                ).scalars()
            )
            points_by_module: dict[int, list[QuizKnowledgePoint]] = {}
            for point in points:
                if by_point.get(int(point.id), (0, 0, 0, 0))[0] > 0:
                    points_by_module.setdefault(int(point.module_id), []).append(point)

            module_items: list[QuizModuleProgress] = []
            for module in modules:
                module_points = points_by_module.get(int(module.id), [])
                if not module_points:
                    continue
                point_items: list[QuizKnowledgePointProgress] = []
                for point in module_points:
                    point_total, point_answered, point_accuracy = summarize(
                        [by_point[int(point.id)]]
                    )
                    point_items.append(
                        QuizKnowledgePointProgress(
                            knowledge_point_id=int(point.id),
                            question_count=point_total,
                            answered_questions=point_answered,
                            accuracy=point_accuracy,
                        )
                    )
                module_total, module_answered, module_accuracy = summarize(
                    [by_point[int(point.id)] for point in module_points]
                )
                module_items.append(
                    QuizModuleProgress(
                        module_id=int(module.id),
                        question_count=module_total,
                        answered_questions=module_answered,
                        accuracy=module_accuracy,
                        knowledge_points=point_items,
                    )
                )
            library_total, library_answered, library_accuracy = summarize(
                [by_point[int(point.id)] for point in points if int(point.id) in by_point]
            )
            return QuizLibraryProgressResponse(
                library_id=library_id,
                question_count=library_total,
                answered_questions=library_answered,
                accuracy=library_accuracy,
                modules=module_items,
            )

    async def _resolve_scope(
        self, db, user_id: int, scope_type: str, scope_id: int
    ) -> tuple[QuizLibrary, list[int], list[dict[str, object]]]:
        if scope_type == "library":
            library_id = scope_id
            point_rows = (
                await db.execute(
                    select(QuizKnowledgePoint, QuizModule)
                    .join(QuizModule, QuizModule.id == QuizKnowledgePoint.module_id)
                    .where(
                        QuizKnowledgePoint.library_id == library_id,
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
                    )
                )
            ).all()
            path_prefix: list[dict[str, object]] = []
        elif scope_type == "module":
            module = await db.get(QuizModule, scope_id)
            if module is None or module.status != "active":
                raise self._not_found()
            library_id = int(module.library_id)
            point_rows = (
                await db.execute(
                    select(QuizKnowledgePoint, QuizModule)
                    .join(QuizModule, QuizModule.id == QuizKnowledgePoint.module_id)
                    .where(
                        QuizKnowledgePoint.module_id == scope_id,
                        QuizKnowledgePoint.status == "active",
                        QuizKnowledgePoint.system_kind == "none",
                        QuizModule.status == "active",
                        QuizModule.system_kind == "none",
                    )
                    .order_by(
                        QuizKnowledgePoint.sort_order.asc(),
                        QuizKnowledgePoint.id.asc(),
                    )
                )
            ).all()
            path_prefix = []
        elif scope_type == "knowledge_point":
            point_rows = (
                await db.execute(
                    select(QuizKnowledgePoint, QuizModule)
                    .join(QuizModule, QuizModule.id == QuizKnowledgePoint.module_id)
                    .where(
                        QuizKnowledgePoint.id == scope_id,
                        QuizKnowledgePoint.status == "active",
                        QuizKnowledgePoint.system_kind == "none",
                        QuizModule.status == "active",
                        QuizModule.system_kind == "none",
                    )
                )
            ).all()
            if not point_rows:
                raise self._not_found()
            library_id = int(point_rows[0][0].library_id)
            path_prefix = []
        else:
            raise BusinessException("不支持的练习范围")
        library = await self._require_accessible_library(db, user_id, library_id)
        point_ids = [int(point.id) for point, _module in point_rows]
        point_paths = [
            {
                "point_id": int(point.id),
                "path": [
                    {"id": int(library.id), "name": library.name, "kind": "library"},
                    {"id": int(module.id), "name": module.name, "kind": "module"},
                    {
                        "id": int(point.id),
                        "name": point.name,
                        "kind": "knowledge_point",
                    },
                ],
            }
            for point, module in point_rows
        ]
        return library, point_ids, point_paths

    @staticmethod
    async def _unfinished_session(
        db, user_id: int, scope_type: str, scope_id: int, mode: str, *, lock: bool = False
    ) -> QuizPracticeSession | None:
        stmt = (
            select(QuizPracticeSession)
            .where(
                QuizPracticeSession.user_id == user_id,
                QuizPracticeSession.scope_type == scope_type,
                QuizPracticeSession.scope_id == scope_id,
                QuizPracticeSession.mode == mode,
                QuizPracticeSession.status.in_(("in_progress", "paused")),
            )
            .order_by(QuizPracticeSession.id.desc())
            .limit(1)
        )
        if lock:
            stmt = stmt.with_for_update()
        return (await db.execute(stmt)).scalar_one_or_none()

    async def preview_practice_scope(
        self,
        user_id: int,
        scope_type: str,
        scope_id: int,
        mode: str = "full",
    ) -> QuizPracticeScopePreview:
        if mode not in {"full", "wrong_only"}:
            raise BusinessException("V2 练习模式必须为 full 或 wrong_only")
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            library, point_ids, _paths = await self._resolve_scope(
                db, user_id, scope_type, scope_id
            )
            stmt = select(func.count()).select_from(QuizQuestion).where(
                QuizQuestion.library_id == library.id,
                QuizQuestion.knowledge_point_id.in_(point_ids),
                QuizQuestion.status == "published",
                QuizQuestion.current_revision_id.is_not(None),
            )
            if mode == "wrong_only":
                stmt = stmt.join(
                    QuizWrongItem, QuizWrongItem.question_id == QuizQuestion.id
                ).where(
                    QuizWrongItem.user_id == user_id,
                    QuizWrongItem.status == "active",
                )
            count = int((await db.execute(stmt)).scalar() or 0)
            existing = await self._unfinished_session(
                db, user_id, scope_type, scope_id, mode
            )
            if existing is not None:
                changed = await self._sync_session_access(db, existing)
                if existing.status not in {"in_progress", "paused"}:
                    existing = None
                if changed:
                    await db.commit()
            return QuizPracticeScopePreview(
                library_id=int(library.id),
                scope_type=scope_type,
                scope_id=scope_id,
                mode=mode,
                question_count=count,
                estimated_minutes=(count * 90 + 59) // 60,
                requires_large_scope_confirmation=count > 100,
                unfinished_session_id=(int(existing.id) if existing else None),
                unfinished_session_expires_at=(existing.expires_at if existing else None),
            )

    async def _expire_if_needed(self, db, session: QuizPracticeSession) -> None:
        if (
            session.status == "in_progress"
            and session.expires_at is not None
            and session.expires_at <= self._now()
        ):
            session.status = "expired"
            session.expired_at = self._now()
            session.lock_version += 1
            await db.commit()
            raise self._session_expired()

    async def create_practice_session(
        self, user_id: int, data: QuizPracticeSessionCreate
    ) -> QuizPracticeSessionResponse:
        if data.scope_type is None or data.scope_id is None or data.mode not in {
            QuizPracticeMode.FULL,
            QuizPracticeMode.WRONG_ONLY,
        }:
            raise BusinessException("V2 全量练习需要有效范围")
        scope_type = str(data.scope_type)
        scope_id = int(data.scope_id)
        mode = str(data.mode)
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            existing = await self._unfinished_session(
                db, user_id, scope_type, scope_id, mode, lock=True
            )
            if existing is not None:
                access_changed = await self._require_usable_session(db, existing)
                if not data.restart_existing:
                    if access_changed:
                        await db.commit()
                    response = await self._serialize_practice_session(db, existing)
                    response.created_new = False
                    response.resume_available = True
                    return response
                existing.status = "abandoned"
                existing.abandoned_at = self._now()
                existing.lock_version += 1
                await db.flush()

            library, point_ids, paths = await self._resolve_scope(
                db, user_id, scope_type, scope_id
            )
            path_by_point = {item["point_id"]: item["path"] for item in paths}
            stmt = (
                select(QuizQuestion, QuizQuestionRevision)
                .join(
                    QuizQuestionRevision,
                    QuizQuestionRevision.id == QuizQuestion.current_revision_id,
                )
                .where(
                    QuizQuestion.library_id == library.id,
                    QuizQuestion.knowledge_point_id.in_(point_ids),
                    QuizQuestion.status == "published",
                    QuizQuestionRevision.status == "published",
                )
            )
            if mode == "wrong_only":
                stmt = stmt.join(
                    QuizWrongItem, QuizWrongItem.question_id == QuizQuestion.id
                ).where(
                    QuizWrongItem.user_id == user_id,
                    QuizWrongItem.status == "active",
                )
            rows = list(
                (
                    await db.execute(
                        stmt.order_by(QuizQuestion.id.asc())
                    )
                ).all()
            )
            if not rows:
                raise BusinessException("当前范围没有可用题目")
            if len(rows) > 100 and not data.confirm_large_scope:
                raise ConflictException("该范围超过 100 题，请确认预计耗时和 7 天有效期")
            now = self._now()
            session = QuizPracticeSession(
                user_id=user_id,
                mode=mode,
                category_id=None,
                library_id=library.id,
                scope_type=scope_type,
                scope_id=scope_id,
                requested_count=len(rows),
                actual_count=len(rows),
                status="in_progress",
                started_at=now,
                last_answered_at=None,
                expires_at=now + self._practice_window(),
                lock_version=1,
            )
            db.add(session)
            await db.flush()
            for position, (question, revision) in enumerate(rows, start=1):
                db.add(
                    QuizPracticeSessionQuestion(
                        session_id=session.id,
                        question_id=question.id,
                        question_revision_id=revision.id,
                        position=position,
                        category_id=None,
                        category_path=path_by_point[int(question.knowledge_point_id)],
                        question_type=revision.question_type,
                        question_text=revision.question_text,
                        options=dict(revision.options or {}),
                        option_image_urls=dict(getattr(revision, "option_image_urls", None) or {}),
                        correct_answer=revision.correct_answer,
                        explanation=revision.explanation or "",
                        image_urls=list(revision.image_urls or []),
                        question_lock_version=question.lock_version,
                        skip_count=0,
                    )
                )
            try:
                await db.commit()
            except Exception:
                await db.rollback()
                winner = await self._unfinished_session(
                    db, user_id, scope_type, scope_id, mode
                )
                if winner is None:
                    raise
                response = await self._serialize_practice_session(db, winner)
                response.created_new = False
                response.resume_available = True
                return response
            return await self._serialize_practice_session(db, session)

    @classmethod
    def _practice_window(cls):
        return timedelta(days=cls._SESSION_WINDOW_DAYS)

    async def get_practice_session(
        self, user_id: int, session_id: int
    ) -> QuizPracticeSessionResponse:
        async with get_db_ctx() as db:
            session = (
                await db.execute(
                    select(QuizPracticeSession).where(
                        QuizPracticeSession.id == session_id,
                        QuizPracticeSession.user_id == user_id,
                        QuizPracticeSession.scope_type.is_not(None),
                    )
                )
            ).scalar_one_or_none()
            if session is None:
                raise NotFoundException("练习会话")
            changed = await self._sync_session_access(db, session)
            if session.status == "expired":
                await db.commit()
                raise self._session_expired()
            if session.status == "terminated":
                await db.commit()
                raise self._session_terminated()
            if changed:
                await db.commit()
            return await self._serialize_practice_session(db, session)

    async def _serialize_practice_session(
        self, db, session: QuizPracticeSession
    ) -> QuizPracticeSessionResponse:
        snapshots = list(
            (
                await db.execute(
                    select(QuizPracticeSessionQuestion)
                    .where(QuizPracticeSessionQuestion.session_id == session.id)
                    .order_by(QuizPracticeSessionQuestion.position.asc())
                )
            ).scalars()
        )
        attempts = list(
            (
                await db.execute(
                    select(QuizPracticeAttempt)
                    .where(QuizPracticeAttempt.session_id == session.id)
                    .order_by(QuizPracticeAttempt.id.asc())
                )
            ).scalars()
        )
        latest: dict[int, QuizPracticeAttempt] = {}
        counts: dict[int, int] = {}
        for attempt in attempts:
            key = int(attempt.session_question_id)
            latest[key] = attempt
            counts[key] = counts.get(key, 0) + 1
        questions: list[QuizPracticeQuestionState] = []
        for snapshot in snapshots:
            attempt = latest.get(int(snapshot.id))
            user_answer = (
                attempt.user_answer if attempt is not None else snapshot.user_answer
            )
            result = (
                QuizPracticeAttemptResult(
                    attempt_id=int(attempt.id),
                    attempt_no=int(attempt.attempt_no),
                    user_answer=attempt.user_answer,
                    is_correct=bool(attempt.is_correct),
                    correct_answer=snapshot.correct_answer,
                    explanation=snapshot.explanation,
                    submitted_at=attempt.submitted_at,
                )
                if attempt
                else None
            )
            questions.append(
                QuizPracticeQuestionState(
                    id=int(snapshot.question_id),
                    category_id=None,
                    library_id=session.library_id,
                    knowledge_point_id=(
                        int(snapshot.category_path[-1]["id"])
                        if snapshot.category_path
                        else None
                    ),
                    question_revision_id=snapshot.question_revision_id,
                    question_type=snapshot.question_type,
                    question_text=snapshot.question_text,
                    options=dict(snapshot.options or {}),
                    option_image_urls=dict(getattr(snapshot, "option_image_urls", None) or {}),
                    image_urls=list(snapshot.image_urls or []),
                    session_question_id=int(snapshot.id),
                    position=int(snapshot.position),
                    category_path=[
                        QuizCategoryPathItem.model_validate(item)
                        for item in snapshot.category_path
                    ],
                    answered=user_answer is not None,
                    user_answer=user_answer,
                    answer_lock_version=int(snapshot.answer_lock_version),
                    correct_answer=(
                        snapshot.correct_answer
                        if session.status == "completed"
                        else None
                    ),
                    explanation=(
                        snapshot.explanation
                        if session.status == "completed"
                        else None
                    ),
                    is_correct=(
                        bool(attempt.is_correct)
                        if session.status == "completed" and attempt is not None
                        else None
                    ),
                    attempt_count=counts.get(int(snapshot.id), 0),
                    latest_result=result,
                )
            )
        answered_count = sum(question.answered for question in questions)
        first_unanswered = next(
            (question for question in questions if not question.answered),
            None,
        )
        focus = (
            int(first_unanswered.position)
            if first_unanswered is not None
            and session.status in {"in_progress", "paused"}
            else None
        )
        return QuizPracticeSessionResponse(
            id=int(session.id),
            mode=session.mode,
            category_id=None,
            library_id=session.library_id,
            scope_type=session.scope_type,
            scope_id=session.scope_id,
            requested_count=int(session.requested_count),
            actual_count=int(session.actual_count),
            status=session.status,
            started_at=session.started_at,
            completed_at=session.completed_at,
            abandoned_at=session.abandoned_at,
            expires_at=session.expires_at,
            paused_at=session.paused_at,
            pause_reason=session.pause_reason,
            answered_count=answered_count,
            remaining_count=max(0, int(session.actual_count) - answered_count),
            current_position=focus,
            lock_version=int(session.lock_version),
            questions=questions,
        )

    async def skip_practice_question(
        self, user_id: int, session_id: int, session_question_id: int
    ) -> QuizPracticeSkipResponse:
        async with get_db_ctx() as db:
            session = (
                await db.execute(
                    select(QuizPracticeSession)
                    .where(
                        QuizPracticeSession.id == session_id,
                        QuizPracticeSession.user_id == user_id,
                        QuizPracticeSession.scope_type.is_not(None),
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if session is None:
                raise NotFoundException("练习会话")
            await self._require_usable_session(db, session)
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
            if int(snapshot.skip_count) >= 1:
                raise ConflictException("每道题只能跳过一次")
            answered = (
                await db.execute(
                    select(QuizPracticeAttempt.id).where(
                        QuizPracticeAttempt.session_question_id == session_question_id
                    ).limit(1)
                )
            ).scalar_one_or_none()
            if answered is not None:
                raise ConflictException("已作答题目不能跳过")
            snapshot.skip_count = 1
            session.lock_version += 1
            await db.commit()
            response = await self._serialize_practice_session(db, session)
            next_question = next(
                (item for item in response.questions if not item.answered), None
            )
            return QuizPracticeSkipResponse(
                session_id=session_id,
                session_question_id=session_question_id,
                skip_count=1,
                next_question=next_question,
            )

    async def is_v2_session(self, user_id: int, session_id: int) -> bool:
        async with get_db_ctx() as db:
            return (
                await db.execute(
                    select(QuizPracticeSession.id).where(
                        QuizPracticeSession.id == session_id,
                        QuizPracticeSession.user_id == user_id,
                        QuizPracticeSession.scope_type.is_not(None),
                    )
                )
            ).scalar_one_or_none() is not None
