import asyncio
import csv
import hashlib
import hmac
import io
import json
import re
import uuid
from dataclasses import dataclass
from zoneinfo import ZoneInfo
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import case, delete, exists, func, or_, select, text, union
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from app.adapter.database import get_db_ctx
from app.adapter.logging import client_ip_var, request_id_var
from app.port.exceptions import (
    BusinessException,
    ConflictException,
    NotFoundException,
    ThirdPartyException,
    ValidationException,
)
from app.domain.community.src.index import (
    QuizAdminAuditLog,
    QuizCategory,
    QuizExam,
    QuizExamAnswer,
    QuizExamQuestion,
    QuizImportError,
    QuizImportJob,
    QuizKnowledgePoint,
    QuizLibrary,
    QuizModule,
    QuizQuestionRevision,
    QuizPracticeAttempt,
    QuizPracticeSession,
    QuizPracticeSessionQuestion,
    QuizCollection,
    QuizCheckin,
    QuizWrongItem,
    QuizQuestion,
    QuizQuestionStats,
    QuizUserStats,
)
from app.domain.user.src.index import User, UserProfile
from app.domain.community.src.rule.quiz import (
    QUESTION_TYPE_IMPORT_ALIASES,
    QuizCategoryStatus,
    QuizQuestionStatus,
    QuizRuleViolation,
    normalize_category_name,
    normalize_question_payload,
)
from app.schemas.admin_quiz import (
    # This import is used only by the deprecated, unmounted helper methods at
    # the bottom of this module. The active `/admin/quiz/imports/*` routes use
    # `app.schemas.admin_quiz_contract` exclusively.
    AdminQuizImportJsonRequest as LegacyAdminQuizImportJsonRequest,
)
from app.schemas.common import PaginatedData
from app.schemas.admin_quiz_contract import (
    AdminQuizBatchItemError,
    AdminQuizBatchRequest,
    AdminQuizCategoryCreate,
    AdminQuizCategoryImpactQuery,
    AdminQuizCategoryImpactResponse,
    AdminQuizCategoryUpdate,
    AdminQuizBatchResponse,
    AdminQuizCategoryStatusUpdate,
    AdminQuizCategoryQuery,
    AdminQuizQuestionCreate,
    AdminQuizQuestionUpdate,
    AdminQuizQuestionQuery,
    AdminQuizQuestionResponse,
    AdminQuizQuestionStatsResponse,
    AdminQuizQuestionStatsListItem,
    AdminQuizStatsOverviewResponse,
    AdminQuizStatsQuestionQuery,
    AdminQuizDailyStatsQuery,
    AdminQuizDailyStatsItem,
    AdminQuizUserStatsQuery,
    AdminQuizUserStatsListItem,
    AdminQuizUserPracticeQuery,
    AdminQuizUserPracticeDay,
    AdminQuizUserPracticeStats,
    AdminQuizUserExamRound,
    AdminQuizVersionRequest,
    AdminQuizAuditLogResponse,
    AdminQuizAuditQuery,
    AdminQuizImportJobQuery,
    AdminQuizImportJobResponse,
    AdminQuizImportErrorPage,
    AdminQuizImportErrorQuery,
    AdminQuizImportCategoryImpactNode,
    AdminQuizImportCategoryImpactResponse,
    AdminQuizImportConfirmCategoriesRequest,
    AdminQuizImportCancelRequest,
    AdminQuizImportReportResponse,
    AdminQuizJsonImportRequest,
    AdminQuizSignedUrlResponse,
    AdminQuizImportQuestion,
)
from app.port.config import settings
from app.utils.audit import redact_sensitive_text, sanitize_audit_value


@dataclass(frozen=True, slots=True)
class LocalImportDownload:
    data: bytes
    media_type: str
    extension: str


@dataclass(frozen=True, slots=True)
class ImportCategoryResolution:
    category_id: int | None
    missing: bool
    blocked: bool
    message: str | None = None


@dataclass(frozen=True, slots=True)
class ImportValidationResult:
    valid: list[tuple[int, AdminQuizImportQuestion, int | tuple[str, ...]]]
    errors: list[dict[str, object]]
    missing_errors: list[dict[str, object]]
    impact: AdminQuizImportCategoryImpactResponse | None


class AdminQuizService:

    _VERSION_CONFLICT_MESSAGE = "数据已被其他管理员修改，请刷新后重试"

    _AUDIT_PERMISSION = "quiz:write"

    @staticmethod
    def _audit_value(value: object) -> object:
        """Convert ORM values to JSON-safe values for immutable audit rows."""
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, dict):
            serialized = {
                str(key): AdminQuizService._audit_value(item)
                for key, item in value.items()
            }
        elif isinstance(value, (list, tuple)):
            serialized = [AdminQuizService._audit_value(item) for item in value]
        else:
            serialized = getattr(value, "value", value)
        return sanitize_audit_value(serialized)

    @classmethod
    def _add_audit(
        cls,
        db,
        *,
        admin_id: int | None,
        action: str,
        object_type: str,
        object_id: int | None = None,
        result: str = "succeeded",
        changed_fields: dict[str, dict[str, object]] | None = None,
        target_ids: list[int] | None = None,
        error_summary: str | None = None,
        permission: str | None = None,
        request_id: str | None = None,
        ip_address: str | None = None,
    ) -> None:
        """Stage an append-only audit row in the current transaction."""
        # Tiny unit-test fakes intentionally model only CRUD calls. Audit rows
        # are a database concern and are therefore skipped for those fakes.
        if not hasattr(db, "execute"):
            return
        if admin_id is not None:
            if request_id is None:
                request_id = request_id_var.get()
                if request_id == "-":
                    request_id = None
            if ip_address is None:
                ip_address = client_ip_var.get()
        db.add(
            QuizAdminAuditLog(
                actor_type="admin" if admin_id is not None else "system",
                admin_id=admin_id,
                permission=permission or cls._AUDIT_PERMISSION,
                request_id=request_id,
                ip_address=ip_address,
                action=action,
                object_type=object_type,
                object_id=object_id,
                result=result,
                changed_fields=(
                    cls._audit_value(changed_fields) if changed_fields is not None else None
                ),
                target_ids=target_ids,
                error_summary=redact_sensitive_text(error_summary),
            )
        )

    @staticmethod
    async def _flush_if_supported(db) -> None:
        """Flush real sessions so newly-created audit rows can reference IDs.

        The small CRUD fakes used by the unit suite intentionally do not expose
        ``flush``; keeping this helper capability-based preserves those tests
        while real AsyncSession instances get database-assigned primary keys
        before the audit row is staged.
        """
        flush = getattr(db, "flush", None)
        if flush is not None:
            await flush()

    @classmethod
    def _changed_fields(
        cls,
        before: dict[str, object],
        after: dict[str, object],
    ) -> dict[str, dict[str, object]]:
        return {
            field: {
                "before": cls._audit_value(before.get(field)),
                "after": cls._audit_value(after.get(field)),
            }
            for field in after
            if before.get(field) != after.get(field)
        }

    @staticmethod
    def _enum_value(value: object) -> object:
        return getattr(value, "value", value)

    @classmethod
    def _check_lock_version(cls, entity: object, expected: int | None) -> None:
        # Legacy internal callers did not carry a version. New HTTP models do.
        if expected is not None and getattr(entity, "lock_version", None) != expected:
            raise ConflictException(cls._VERSION_CONFLICT_MESSAGE)

    @classmethod
    async def record_permission_denied(
        cls, *, admin_id: int, permission: str
    ) -> None:
        """Persist a safe, append-only audit row for a quiz RBAC denial."""

        async with get_db_ctx() as db:
            cls._add_audit(
                db,
                admin_id=admin_id,
                action="permission.denied",
                object_type="permission",
                result="failed",
                error_summary=f"缺少权限: {permission}",
                permission=permission,
            )
            await db.commit()

    @staticmethod
    async def _get_for_update(db, model, entity_id: int):
        """Load a write target under a row lock when using a real session."""
        if not hasattr(db, "execute"):
            return await db.get(model, entity_id)
        result = await db.execute(
            select(model).where(model.id == entity_id).with_for_update()
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def _category_chain(db, category_id: int) -> list[QuizCategory]:
        chain: list[QuizCategory] = []
        seen: set[int] = set()
        current_id: int | None = category_id
        while current_id is not None:
            if current_id in seen:
                raise BusinessException("题库分类存在循环引用")
            seen.add(current_id)
            category = await db.get(QuizCategory, current_id)
            if category is None:
                raise NotFoundException("题库分类")
            chain.append(category)
            current_id = category.parent_id
        return chain

    @classmethod
    async def _category_is_active(cls, db, category_id: int) -> bool:
        return all(
            category.status == QuizCategoryStatus.ACTIVE.value
            for category in await cls._category_chain(db, category_id)
        )

    @staticmethod
    async def _question_text_taken(
        db,
        *,
        category_id: int,
        question_text_hash: str,
        exclude_id: int | None = None,
    ) -> bool:
        # Small service fakes used by unit tests only implement ``get/add``.
        # Real sessions always expose execute, so the duplicate guard remains
        # enforced for every HTTP/database request.
        if not hasattr(db, "execute"):
            return False
        stmt = select(QuizQuestion.id).where(
            QuizQuestion.category_id == category_id,
            QuizQuestion.question_text_hash == question_text_hash,
        )
        if exclude_id is not None:
            stmt = stmt.where(QuizQuestion.id != exclude_id)
        return (await db.execute(stmt.limit(1))).scalar_one_or_none() is not None

    @staticmethod
    async def _recalculate_descendant_depths(db, category_id: int, depth: int) -> None:
        result = await db.execute(select(QuizCategory))
        categories = list(result.scalars().all())
        by_parent: dict[int | None, list[QuizCategory]] = {}
        for item in categories:
            by_parent.setdefault(item.parent_id, []).append(item)
        queue: list[tuple[int, int]] = [(category_id, depth)]
        by_id = {item.id: item for item in categories}
        while queue:
            current_id, current_depth = queue.pop(0)
            current = by_id.get(current_id)
            if current is None:
                continue
            current.depth = current_depth
            queue.extend((child.id, current_depth + 1) for child in by_parent.get(current_id, []))
        if any(item.depth > 3 for item in categories):
            raise BusinessException("题库分类最多支持三级")

    @staticmethod
    def _normalize_admin_question(
        *,
        question_type: str,
        question_text: object,
        options: object,
        correct_answer: object,
        explanation: object,
        image_urls: object,
        option_image_urls: object = None,
        require_publishable: bool,
    ):
        # The pre-contract admin UI sent multi-select answers as "AC". Accept
        # that legacy shape at this boundary, then persist the frozen array form.
        if question_type == "multiple_choice" and isinstance(correct_answer, str):
            answer_text = correct_answer.strip()
            # Accept the legacy ``AC``/``A,C``/``A、C`` shapes at the service
            # boundary, then persist the frozen sorted array representation.
            if re.search(r"[,，、;；\s]", answer_text):
                correct_answer = [part for part in re.split(r"[,，、;；\s]+", answer_text) if part]
            else:
                correct_answer = list(answer_text)
        try:
            return normalize_question_payload(
                question_type=question_type,
                question_text=question_text,
                options=options,
                correct_answer=correct_answer,
                explanation=explanation,
                image_urls=image_urls,
                option_image_urls=option_image_urls,
                require_publishable=require_publishable,
            )
        except QuizRuleViolation as exc:
            raise ValidationException(
                exc.message,
                detail=[{"field": exc.field, "message": exc.message}],
            ) from exc

    # ── Category queries ──

    async def list_categories(
        self,
        query: AdminQuizCategoryQuery | None = None,
        *,
        status: str | None = None,
        parent_id: int | None = None,
    ) -> list[QuizCategory]:
        if query is not None:
            status = query.status
            parent_id = query.parent_id
        status_value = self._enum_value(status)
        async with get_db_ctx() as db:
            stmt = select(QuizCategory)
            if status_value is not None:
                stmt = stmt.where(QuizCategory.status == status_value)
            if parent_id is not None:
                stmt = stmt.where(QuizCategory.parent_id == parent_id)
            result = await db.execute(
                stmt.order_by(
                    QuizCategory.parent_id.asc().nullsfirst(),
                    QuizCategory.sort_order.asc(),
                    QuizCategory.id.asc(),
                )
            )
            return list(result.scalars().all())

    @staticmethod
    def _category_subtree_ids(
        categories: list[QuizCategory], category_id: int
    ) -> list[int]:
        children: dict[int, list[int]] = {}
        for item in categories:
            if item.parent_id is not None:
                children.setdefault(item.parent_id, []).append(item.id)
        result: list[int] = []
        queue = [category_id]
        while queue:
            current = queue.pop(0)
            if current in result:
                continue
            result.append(current)
            queue.extend(children.get(current, []))
        return result

    async def preview_category_impact(
        self,
        category_id: int,
        query: AdminQuizCategoryImpactQuery,
    ) -> AdminQuizCategoryImpactResponse:
        """Calculate a read-only impact snapshot for one category operation."""

        calculated_at = datetime.now(timezone.utc)
        async with get_db_ctx() as db:
            categories = list((await db.execute(select(QuizCategory))).scalars().all())
            by_id = {item.id: item for item in categories}
            category = by_id.get(category_id)
            if category is None:
                raise NotFoundException("题库分类")
            subtree_ids = self._category_subtree_ids(categories, category_id)
            descendant_ids = subtree_ids[1:]
            count_rows = (
                await db.execute(
                    select(QuizQuestion.status, func.count(QuizQuestion.id))
                    .where(QuizQuestion.category_id.in_(subtree_ids))
                    .group_by(QuizQuestion.status)
                )
            ).all()
            question_counts = {str(status): int(count) for status, count in count_rows}
            published_rows = (
                await db.execute(
                    select(QuizQuestion.category_id, func.count(QuizQuestion.id))
                    .where(
                        QuizQuestion.category_id.in_(subtree_ids),
                        QuizQuestion.status == QuizQuestionStatus.PUBLISHED.value,
                    )
                    .group_by(QuizQuestion.category_id)
                )
            ).all()
            published_by_category = {
                int(item_category_id): int(count)
                for item_category_id, count in published_rows
            }

            def chain_active(item_id: int) -> bool:
                seen: set[int] = set()
                current_id: int | None = item_id
                while current_id is not None:
                    if current_id in seen:
                        return False
                    seen.add(current_id)
                    current = by_id.get(current_id)
                    if current is None or current.status != QuizCategoryStatus.ACTIVE.value:
                        return False
                    current_id = current.parent_id
                return True

            def subtree_chain_active(item_id: int) -> bool:
                """Check only the category chain that moves with the subtree."""

                seen: set[int] = set()
                current_id: int | None = item_id
                while current_id is not None:
                    if current_id in seen:
                        return False
                    seen.add(current_id)
                    current = by_id.get(current_id)
                    if current is None or current.status != QuizCategoryStatus.ACTIVE.value:
                        return False
                    if current_id == category_id:
                        return True
                    current_id = current.parent_id
                return False

            blockers: list[str] = []
            affected_new_pool = 0
            target_parent_id = query.target_parent_id if query.action == "move" else None
            if query.action == "disable":
                if category.status == QuizCategoryStatus.DISABLED.value:
                    blockers.append("分类已经停用")
                # Only currently effective published questions leave the pool.
                affected_new_pool = sum(
                    published_by_category.get(item_id, 0)
                    for item_id in subtree_ids
                    if chain_active(item_id)
                )
            elif query.action == "delete":
                if descendant_ids:
                    blockers.append("该分类下存在子分类，请先删除子分类")
                if category.ever_had_question:
                    blockers.append("该分类曾包含题目，不允许物理删除")
                if sum(question_counts.values()):
                    blockers.append("该分类下存在题目，请先删除题目")
            else:
                if target_parent_id == category.parent_id:
                    blockers.append("目标父分类与当前父分类相同")
                if target_parent_id == category_id:
                    blockers.append("分类不能将自身设为父分类")
                target_parent = by_id.get(target_parent_id) if target_parent_id else None
                if target_parent_id is not None and target_parent is None:
                    blockers.append("目标父分类不存在")
                if target_parent_id in descendant_ids:
                    blockers.append("分类不能移动到自身的子分类下")
                target_depth = 1 if target_parent is None else target_parent.depth + 1
                subtree_height = max(
                    (by_id[item_id].depth - category.depth for item_id in subtree_ids),
                    default=0,
                )
                if target_depth + subtree_height > 3:
                    blockers.append("移动后分类层级将超过三级")
                if target_parent_id is None:
                    duplicate = any(
                        item.id != category_id
                        and item.parent_id is None
                        and item.normalized_name == category.normalized_name
                        for item in categories
                    )
                else:
                    duplicate = any(
                        item.id != category_id
                        and item.parent_id == target_parent_id
                        and item.normalized_name == category.normalized_name
                        for item in categories
                    )
                if duplicate:
                    blockers.append("目标父级下已存在同名分类")
                if not blockers:
                    target_chain_active = (
                        True
                        if target_parent is None
                        else chain_active(target_parent.id)
                    )
                    for item_id in subtree_ids:
                        old_effective = chain_active(item_id)
                        internal_active = subtree_chain_active(item_id)
                        new_effective = internal_active and target_chain_active
                        if old_effective != new_effective:
                            affected_new_pool += published_by_category.get(item_id, 0)

        return AdminQuizCategoryImpactResponse(
            category_id=category_id,
            action=query.action,
            target_parent_id=target_parent_id,
            descendant_category_count=len(descendant_ids),
            draft_question_count=question_counts.get("draft", 0),
            published_question_count=question_counts.get("published", 0),
            disabled_question_count=question_counts.get("disabled", 0),
            affected_new_pool_question_count=affected_new_pool,
            history_snapshot_affected=False,
            can_execute=not blockers,
            blocking_reasons=blockers,
            calculated_at=calculated_at,
        )

    # ── Question queries ──

    async def list_questions(
        self,
        *,
        category_id: int | None = None,
        include_descendants: bool = False,
        question_type: str | None = None,
        status: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedData[AdminQuizQuestionResponse]:
        async with get_db_ctx() as db:
            base = select(QuizQuestion)
            if category_id is not None:
                category_ids = [category_id]
                if include_descendants:
                    categories = list(
                        (await db.execute(select(QuizCategory))).scalars().all()
                    )
                    if not any(item.id == category_id for item in categories):
                        raise NotFoundException("题库分类")
                    category_ids = self._category_subtree_ids(
                        categories, category_id
                    )
                base = base.where(QuizQuestion.category_id.in_(category_ids))
            if question_type is not None:
                base = base.where(
                    QuizQuestion.question_type == self._enum_value(question_type)
                )
            if status is not None:
                base = base.where(QuizQuestion.status == self._enum_value(status))
            if keyword:
                pattern = f"%{keyword.strip()}%"
                base = base.where(
                    or_(
                        QuizQuestion.question_text.ilike(pattern),
                        QuizQuestion.normalized_question_text.ilike(pattern),
                    )
                )

            count_stmt = select(func.count()).select_from(base.subquery())
            total = (await db.execute(count_stmt)).scalar() or 0

            result = await db.execute(
                base.order_by(QuizQuestion.updated_at.desc(), QuizQuestion.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            questions = result.scalars().all()

        return PaginatedData[AdminQuizQuestionResponse](
            items=[AdminQuizQuestionResponse.model_validate(q) for q in questions],
            total=total,
            page=page,
            page_size=page_size,
        )

    # ── Category CRUD ──

    async def create_category(
        self, data: AdminQuizCategoryCreate, *, admin_id: int | None = None
    ) -> QuizCategory:
        if admin_id is None:
            raise BusinessException("缺少管理员身份，无法创建题库分类")
        async with get_db_ctx() as db:
            try:
                normalized_name = normalize_category_name(data.name)
            except ValueError as exc:
                raise ValidationException(str(exc)) from exc

            depth = 1
            if data.parent_id is not None:
                parent = await db.get(QuizCategory, data.parent_id)
                if parent is None:
                    raise NotFoundException("父级分类")
                depth = parent.depth + 1
                if depth > 3:
                    raise BusinessException("题库分类最多支持三级")

            if hasattr(db, "execute"):
                duplicate = await db.execute(
                    select(QuizCategory.id)
                    .where(
                        QuizCategory.normalized_name == normalized_name,
                        QuizCategory.parent_id == data.parent_id,
                    )
                    .limit(1)
                )
                if duplicate.scalar_one_or_none() is not None:
                    raise ValidationException("同一父级分类下名称不能重复")

            category = QuizCategory(
                name=normalized_name,
                normalized_name=normalized_name,
                parent_id=data.parent_id,
                depth=depth,
                description=data.description,
                status="active",
                sort_order=getattr(data, "sort_order", 0),
                ever_had_question=False,
                lock_version=1,
                created_by=admin_id,
                updated_by=admin_id,
            )
            db.add(category)
            await self._flush_if_supported(db)
            self._add_audit(
                db,
                admin_id=admin_id,
                action="category.create",
                object_type="category",
                object_id=category.id,
                changed_fields={
                    "name": {"before": None, "after": normalized_name},
                    "normalized_name": {"before": None, "after": normalized_name},
                    "parent_id": {"before": None, "after": data.parent_id},
                    "description": {"before": None, "after": data.description},
                    "sort_order": {"before": None, "after": getattr(data, "sort_order", 0)},
                    "status": {"before": None, "after": "active"},
                },
            )
            try:
                await db.commit()
            except IntegrityError as exc:
                await db.rollback()
                raise ValidationException("同一父级分类下名称不能重复") from exc
            await db.refresh(category)
            return category

    async def update_category(
        self,
        category_id: int,
        data: AdminQuizCategoryUpdate,
        *,
        admin_id: int | None = None,
    ) -> QuizCategory:
        if admin_id is None:
            raise BusinessException("缺少管理员身份，无法编辑题库分类")
        async with get_db_ctx() as db:
            category = await self._get_for_update(db, QuizCategory, category_id)
            if category is None:
                raise NotFoundException("题库分类")

            update_data = data.model_dump(exclude_unset=True)
            expected_version = update_data.pop("lock_version", None)
            self._check_lock_version(category, expected_version)
            audit_before = {
                key: getattr(category, key)
                for key in ("name", "normalized_name", "parent_id", "description", "sort_order", "status")
            }
            if "parent_id" in update_data and update_data["parent_id"] is not None:
                new_parent_id = update_data["parent_id"]
                if new_parent_id == category_id:
                    raise BusinessException("分类不能将自身设为父分类")
                parent = await db.get(QuizCategory, new_parent_id)
                if parent is None:
                    raise NotFoundException("父级分类")
                ancestor_id = parent.id
                while ancestor_id is not None:
                    if ancestor_id == category_id:
                        raise BusinessException("分类不能移动到自身的子分类下")
                    ancestor = await db.get(QuizCategory, ancestor_id)
                    ancestor_id = ancestor.parent_id if ancestor is not None else None
                depth = parent.depth + 1
                if depth > 3:
                    raise BusinessException("题库分类最多支持三级")
            elif "parent_id" in update_data:
                depth = 1
            else:
                depth = category.depth

            if "name" in update_data:
                try:
                    update_data["name"] = normalize_category_name(update_data["name"])
                except ValueError as exc:
                    raise ValidationException(str(exc)) from exc
                update_data["normalized_name"] = update_data["name"]

            target_parent_id = update_data.get("parent_id", category.parent_id)
            target_name = update_data.get("normalized_name", category.normalized_name)
            if hasattr(db, "execute"):
                duplicate = await db.execute(
                    select(QuizCategory.id)
                    .where(
                        QuizCategory.normalized_name == target_name,
                        QuizCategory.parent_id == target_parent_id,
                        QuizCategory.id != category_id,
                    )
                    .limit(1)
                )
                if duplicate.scalar_one_or_none() is not None:
                    raise ValidationException("同一父级分类下名称不能重复")

            for key, value in update_data.items():
                setattr(category, key, value)
            if "parent_id" in update_data:
                category.depth = depth
                # Validate and update the complete subtree before the first
                # commit so a depth violation cannot leave a partial move.
                await self._recalculate_descendant_depths(db, category.id, category.depth)
            category.updated_by = admin_id
            category.lock_version += 1
            audit_after = {
                key: getattr(category, key)
                for key in audit_before
            }
            self._add_audit(
                db,
                admin_id=admin_id,
                action="category.update",
                object_type="category",
                object_id=category.id,
                changed_fields=self._changed_fields(audit_before, audit_after),
            )
            try:
                await db.commit()
            except IntegrityError as exc:
                await db.rollback()
                raise ValidationException("同一父级分类下名称不能重复") from exc
            await db.refresh(category)
            return category

    async def update_category_status(
        self,
        category_id: int,
        data: AdminQuizCategoryStatusUpdate,
        *,
        admin_id: int | None = None,
    ) -> QuizCategory:
        if admin_id is None:
            raise BusinessException("缺少管理员身份，无法修改题库分类状态")
        async with get_db_ctx() as db:
            category = await self._get_for_update(db, QuizCategory, category_id)
            if category is None:
                raise NotFoundException("题库分类")
            self._check_lock_version(category, data.lock_version)
            target_status = str(self._enum_value(data.status))
            if category.status != target_status:
                before_status = category.status
                category.status = target_status
                category.updated_by = admin_id
                category.lock_version += 1
                self._add_audit(
                    db,
                    admin_id=admin_id,
                    action="category.status",
                    object_type="category",
                    object_id=category.id,
                    changed_fields={
                        "status": {"before": before_status, "after": target_status}
                    },
                )
                await db.commit()
                await db.refresh(category)
            return category

    async def delete_category(
        self,
        category_id: int,
        lock_version: int | None = None,
        *,
        admin_id: int | None = None,
    ) -> None:
        async with get_db_ctx() as db:
            category = await self._get_for_update(db, QuizCategory, category_id)
            if category is None:
                raise NotFoundException("题库分类")
            self._check_lock_version(category, lock_version)
            child_count = (
                await db.execute(
                    select(func.count()).select_from(QuizCategory).where(
                        QuizCategory.parent_id == category_id
                    )
                )
            ).scalar() or 0
            if child_count > 0:
                raise BusinessException("该分类下存在子分类，请先删除子分类")
            if category.ever_had_question:
                raise BusinessException("该分类曾包含题目，不允许物理删除")
            question_count = (
                await db.execute(
                    select(func.count()).select_from(QuizQuestion).where(
                        QuizQuestion.category_id == category_id
                    )
                )
            ).scalar() or 0
            if question_count > 0:
                raise BusinessException("该分类下存在题目，请先删除题目")
            self._add_audit(
                db,
                admin_id=admin_id,
                action="category.delete",
                object_type="category",
                object_id=category.id,
                changed_fields={
                    "name": {"before": category.name, "after": None},
                    "status": {"before": category.status, "after": None},
                },
            )
            await db.delete(category)
            try:
                await db.commit()
            except IntegrityError as exc:
                await db.rollback()
                raise BusinessException(
                    "分类在删除前新增了子分类或题目，无法物理删除"
                ) from exc

    # ── Question CRUD ──

    async def create_question(
        self, data: AdminQuizQuestionCreate, *, admin_id: int | None = None
    ) -> QuizQuestion:
        if admin_id is None:
            raise BusinessException("缺少管理员身份，无法创建题目")
        async with get_db_ctx() as db:
            category = await self._get_for_update(db, QuizCategory, data.category_id)
            if category is None:
                raise NotFoundException("题库分类")
            normalized = self._normalize_admin_question(
                question_type=data.question_type,
                question_text=data.question_text,
                options=data.options,
                correct_answer=data.correct_answer,
                explanation=data.explanation,
                image_urls=data.image_urls,
                option_image_urls=data.option_image_urls,
                require_publishable=False,
            )
            if await self._question_text_taken(
                db,
                category_id=data.category_id,
                question_text_hash=normalized.question_text_hash,
            ):
                raise ValidationException("同一分类内规范化题干不能重复")
            question = QuizQuestion(
                category_id=data.category_id,
                question_type=str(normalized.question_type.value),
                question_text=normalized.question_text,
                normalized_question_text=normalized.normalized_question_text,
                question_text_hash=normalized.question_text_hash,
                status="draft",
                options=normalized.options,
                correct_answer=normalized.correct_answer,
                explanation=normalized.explanation,
                image_urls=normalized.image_urls,
                option_image_urls=normalized.option_image_urls,
                ever_published=False,
                lock_version=1,
                created_by=admin_id,
                updated_by=admin_id,
            )
            db.add(question)
            category.ever_had_question = True
            await self._flush_if_supported(db)
            self._add_audit(
                db,
                admin_id=admin_id,
                action="question.create",
                object_type="question",
                object_id=question.id,
                changed_fields={
                    "category_id": {"before": None, "after": data.category_id},
                    "question_type": {"before": None, "after": normalized.question_type.value},
                    "question_text": {"before": None, "after": normalized.question_text},
                    "options": {"before": None, "after": normalized.options},
                    "correct_answer": {"before": None, "after": normalized.correct_answer},
                    "explanation": {"before": None, "after": normalized.explanation},
                    "image_urls": {"before": None, "after": normalized.image_urls},
                    "status": {"before": None, "after": "draft"},
                },
            )
            try:
                await db.commit()
            except IntegrityError as exc:
                await db.rollback()
                raise ValidationException("题目内容与分类中的已有题目重复") from exc
            await db.refresh(question)
            return question

    async def update_question(
        self,
        question_id: int,
        data: AdminQuizQuestionUpdate,
        *,
        admin_id: int | None = None,
    ) -> QuizQuestion:
        if admin_id is None:
            raise BusinessException("缺少管理员身份，无法编辑题目")
        async with get_db_ctx() as db:
            question = await self._get_for_update(db, QuizQuestion, question_id)
            if question is None:
                raise NotFoundException("题目")
            self._check_lock_version(question, getattr(data, "lock_version", None))
            target_category = None
            if data.category_id is not None:
                target_category = await self._get_for_update(db, QuizCategory, data.category_id)
                if target_category is None:
                    raise NotFoundException("题库分类")
                if question.ever_published and not await self._category_is_active(
                    db, data.category_id
                ):
                    raise BusinessException("已发布题目不能移动到停用分类或其子树")
            fields = data.model_fields_set
            audit_before = {
                key: getattr(question, key)
                for key in (
                    "category_id",
                    "question_type",
                    "question_text",
                    "options",
                    "correct_answer",
                    "explanation",
                    "image_urls",
                    "status",
                )
            }
            normalized = self._normalize_admin_question(
                question_type=(
                    data.question_type
                    if "question_type" in fields
                    else question.question_type
                ),
                question_text=(
                    data.question_text
                    if "question_text" in fields
                    else question.question_text
                ),
                options=data.options if "options" in fields else question.options,
                correct_answer=(
                    data.correct_answer
                    if "correct_answer" in fields
                    else question.correct_answer
                ),
                explanation=(
                    data.explanation
                    if "explanation" in fields
                    else question.explanation
                ),
                image_urls=(
                    data.image_urls
                    if "image_urls" in fields
                    else question.image_urls
                ),
                option_image_urls=(
                    data.option_image_urls
                    if "option_image_urls" in fields
                    else question.option_image_urls
                ),
                require_publishable=question.ever_published,
            )
            target_category_id = data.category_id if data.category_id is not None else question.category_id
            if await self._question_text_taken(
                db,
                category_id=target_category_id,
                question_text_hash=normalized.question_text_hash,
                exclude_id=question.id,
            ):
                raise ValidationException("同一分类内规范化题干不能重复")
            question.question_type = str(normalized.question_type.value)
            question.question_text = normalized.question_text
            question.normalized_question_text = normalized.normalized_question_text
            question.question_text_hash = normalized.question_text_hash
            question.options = normalized.options
            question.correct_answer = normalized.correct_answer
            question.explanation = normalized.explanation
            question.image_urls = normalized.image_urls
            question.option_image_urls = normalized.option_image_urls
            if data.category_id is not None:
                question.category_id = data.category_id
                target_category.ever_had_question = True
            question.updated_by = admin_id
            question.lock_version += 1
            audit_after = {
                key: getattr(question, key)
                for key in audit_before
            }
            self._add_audit(
                db,
                admin_id=admin_id,
                action="question.update",
                object_type="question",
                object_id=question.id,
                changed_fields=self._changed_fields(audit_before, audit_after),
            )
            try:
                await db.commit()
            except IntegrityError as exc:
                await db.rollback()
                raise ValidationException("题目内容与分类中的已有题目重复") from exc
            await db.refresh(question)
            return question

    async def delete_question(
        self,
        question_id: int,
        lock_version: int | None = None,
        *,
        admin_id: int | None = None,
    ) -> None:
        async with get_db_ctx() as db:
            question = await self._get_for_update(db, QuizQuestion, question_id)
            if question is None:
                raise NotFoundException("题目")
            self._check_lock_version(question, lock_version)
            # Historical snapshots/reference rows deliberately keep published
            # questions alive. Only a never-published draft can be physically
            # deleted. Historical answers live in immutable session snapshot
            # tables, so the draft/ever_published lifecycle check is the only
            # deletion guard.
            if question.status != QuizQuestionStatus.DRAFT.value or question.ever_published:
                raise BusinessException("仅未发布草稿题目允许物理删除")
            # A draft normally cannot be referenced by a session, wrong-book
            # row or collection because those rows are created from published
            # questions.  Keep an explicit guard nevertheless: it protects a
            # manually repaired database (and gives the administrator a
            # useful error) before PostgreSQL's RESTRICT foreign keys reject
            # the delete.  ``record_count`` is deliberately a generic
            # historical-reference count; it is not the removed quiz_record
            # table and does not reintroduce that legacy coupling.
            record_count = 0
            if hasattr(db, "execute"):
                for reference_model in (
                    QuizPracticeSessionQuestion,
                    QuizExamQuestion,
                    QuizWrongItem,
                    QuizCollection,
                ):
                    record_count += int(
                        (
                            await db.execute(
                                select(func.count())
                                .select_from(reference_model)
                                .where(reference_model.question_id == question_id)
                            )
                        ).scalar()
                        or 0
                    )
            if record_count > 0:
                raise BusinessException("该题目已有答题记录或历史引用，不可删除")
            self._add_audit(
                db,
                admin_id=admin_id,
                action="question.delete",
                object_type="question",
                object_id=question.id,
                changed_fields={
                    "status": {"before": question.status, "after": None},
                    "question_text": {"before": question.question_text, "after": None},
                },
            )
            await db.delete(question)
            try:
                await db.commit()
            except IntegrityError as exc:
                await db.rollback()
                raise BusinessException("该题目已有答题或历史引用，不可删除") from exc

    async def batch_delete_questions(self, question_ids: list[int]) -> int:
        async with get_db_ctx() as db:
            result = await db.execute(
                select(QuizQuestion).where(QuizQuestion.id.in_(question_ids))
            )
            questions = result.scalars().all()
            for question in questions:
                # Legacy endpoint retained only for old callers. Apply the new
                # draft-only lifecycle rule and count actual deletions.
                if question.status == QuizQuestionStatus.DRAFT.value and not question.ever_published:
                    await db.delete(question)
            await db.commit()
            return sum(
                1
                for question in questions
                if question.status == QuizQuestionStatus.DRAFT.value and not question.ever_published
            )

    async def _transition_question(
        self,
        question_id: int,
        lock_version: int,
        action: str,
        *,
        admin_id: int,
    ) -> QuizQuestion:
        async with get_db_ctx() as db:
            question = await self._get_for_update(db, QuizQuestion, question_id)
            if question is None:
                raise NotFoundException("题目")
            self._check_lock_version(question, lock_version)
            now = datetime.now(timezone.utc)
            before_status = question.status
            before_disabled_at = question.disabled_at
            before_published_at = question.published_at
            if action == "publish":
                if question.status != QuizQuestionStatus.DRAFT.value:
                    raise BusinessException("仅草稿题目可以发布")
                if not await self._category_is_active(db, question.category_id):
                    raise BusinessException("题目所属分类或祖先分类已停用")
                normalized = self._normalize_admin_question(
                    question_type=question.question_type,
                    question_text=question.question_text,
                    options=question.options,
                    correct_answer=question.correct_answer,
                    explanation=question.explanation,
                    image_urls=question.image_urls,
                    option_image_urls=question.option_image_urls,
                    require_publishable=True,
                )
                if await self._question_text_taken(
                    db,
                    category_id=question.category_id,
                    question_text_hash=normalized.question_text_hash,
                    exclude_id=question.id,
                ):
                    raise ValidationException("同一分类内规范化题干不能重复")
                question.options = normalized.options
                question.correct_answer = normalized.correct_answer
                question.explanation = normalized.explanation
                question.image_urls = normalized.image_urls
                question.normalized_question_text = normalized.normalized_question_text
                question.question_text_hash = normalized.question_text_hash
                question.status = QuizQuestionStatus.PUBLISHED.value
                question.ever_published = True
                question.published_at = question.published_at or now
                question.disabled_at = None
            elif action == "disable":
                if question.status != QuizQuestionStatus.PUBLISHED.value:
                    raise BusinessException("仅已发布题目可以停用")
                question.status = QuizQuestionStatus.DISABLED.value
                question.disabled_at = now
            elif action == "restore":
                if question.status != QuizQuestionStatus.DISABLED.value:
                    raise BusinessException("仅已停用题目可以恢复")
                if not await self._category_is_active(db, question.category_id):
                    raise BusinessException("题目所属分类或祖先分类已停用")
                normalized = self._normalize_admin_question(
                    question_type=question.question_type,
                    question_text=question.question_text,
                    options=question.options,
                    correct_answer=question.correct_answer,
                    explanation=question.explanation,
                    image_urls=question.image_urls,
                    option_image_urls=question.option_image_urls,
                    require_publishable=True,
                )
                question.options = normalized.options
                question.correct_answer = normalized.correct_answer
                question.explanation = normalized.explanation
                question.image_urls = normalized.image_urls
                question.normalized_question_text = normalized.normalized_question_text
                question.question_text_hash = normalized.question_text_hash
                question.status = QuizQuestionStatus.PUBLISHED.value
                # Keep the most recent disable timestamp as historical
                # metadata when a disabled question is restored.
            else:
                raise BusinessException("不支持的题目状态操作")
            question.updated_by = admin_id
            question.lock_version += 1
            self._add_audit(
                db,
                admin_id=admin_id,
                action=f"question.{action}",
                object_type="question",
                object_id=question.id,
                changed_fields={
                    "status": {"before": before_status, "after": question.status},
                    "disabled_at": {"before": before_disabled_at, "after": question.disabled_at},
                    "published_at": {"before": before_published_at, "after": question.published_at},
                },
            )
            try:
                await db.commit()
            except IntegrityError as exc:
                await db.rollback()
                raise ValidationException("题目内容不满足发布约束") from exc
            await db.refresh(question)
            return question

    async def publish_question(
        self, question_id: int, data: AdminQuizVersionRequest, *, admin_id: int
    ) -> QuizQuestion:
        return await self._transition_question(
            question_id, data.lock_version, "publish", admin_id=admin_id
        )

    async def disable_question(
        self, question_id: int, data: AdminQuizVersionRequest, *, admin_id: int
    ) -> QuizQuestion:
        return await self._transition_question(
            question_id, data.lock_version, "disable", admin_id=admin_id
        )

    async def restore_question(
        self, question_id: int, data: AdminQuizVersionRequest, *, admin_id: int
    ) -> QuizQuestion:
        return await self._transition_question(
            question_id, data.lock_version, "restore", admin_id=admin_id
        )

    async def _batch_transition_questions(
        self,
        data: AdminQuizBatchRequest,
        action: str,
        *,
        admin_id: int,
    ) -> AdminQuizBatchResponse:
        errors: list[AdminQuizBatchItemError] = []
        async with get_db_ctx() as db:
            questions: dict[int, QuizQuestion] = {}
            pending: dict[int, object] = {}
            audit_before: dict[int, dict[str, object]] = {}
            for item in data.items:
                question = await self._get_for_update(db, QuizQuestion, item.question_id)
                if question is None:
                    errors.append(
                        AdminQuizBatchItemError(
                            question_id=item.question_id,
                            code=40300,
                            field=None,
                            message="题目不存在",
                        )
                    )
                    continue
                questions[item.question_id] = question
                audit_before[item.question_id] = {
                    "status": question.status,
                    "published_at": question.published_at,
                    "disabled_at": question.disabled_at,
                    "lock_version": question.lock_version,
                }
                if question.lock_version != item.lock_version:
                    errors.append(
                        AdminQuizBatchItemError(
                            question_id=item.question_id,
                            code=40201,
                            field="lock_version",
                            message=self._VERSION_CONFLICT_MESSAGE,
                        )
                    )
                    continue
                if action == "publish":
                    if question.status != QuizQuestionStatus.DRAFT.value:
                        errors.append(
                            AdminQuizBatchItemError(
                                question_id=item.question_id,
                                code=40200,
                                field="status",
                                message="仅草稿题目可以发布",
                            )
                        )
                        continue
                    if not await self._category_is_active(db, question.category_id):
                        errors.append(
                            AdminQuizBatchItemError(
                                question_id=item.question_id,
                                code=40200,
                                field="category_id",
                                message="题目所属分类或祖先分类已停用",
                            )
                        )
                        continue
                    try:
                        normalized = self._normalize_admin_question(
                            question_type=question.question_type,
                            question_text=question.question_text,
                            options=question.options,
                            correct_answer=question.correct_answer,
                            explanation=question.explanation,
                            image_urls=question.image_urls,
                            option_image_urls=question.option_image_urls,
                            require_publishable=True,
                        )
                        if await self._question_text_taken(
                            db,
                            category_id=question.category_id,
                            question_text_hash=normalized.question_text_hash,
                            exclude_id=question.id,
                        ):
                            raise ValidationException("同一分类内规范化题干不能重复")
                        pending[item.question_id] = normalized
                    except ValidationException as exc:
                        errors.append(
                            AdminQuizBatchItemError(
                                question_id=item.question_id,
                                code=40001,
                                field=None,
                                message=exc.message,
                            )
                        )
                elif action == "disable" and question.status != QuizQuestionStatus.PUBLISHED.value:
                    errors.append(
                        AdminQuizBatchItemError(
                            question_id=item.question_id,
                            code=40200,
                            field="status",
                            message="仅已发布题目可以停用",
                        )
                    )

            if errors:
                self._add_audit(
                    db,
                    admin_id=admin_id,
                    action=f"question.batch_{action}",
                    object_type="question",
                    result="failed",
                    target_ids=[item.question_id for item in data.items],
                    error_summary="; ".join(
                        f"{error.question_id}: {error.message}" for error in errors
                    )[:4096],
                    permission="quiz:write",
                )
                await db.commit()
                return AdminQuizBatchResponse(succeeded=False, updated_count=0, errors=errors)

            now = datetime.now(timezone.utc)
            for question in questions.values():
                if action == "publish":
                    normalized = pending[question.id]
                    question.options = normalized.options  # type: ignore[union-attr]
                    question.correct_answer = normalized.correct_answer  # type: ignore[union-attr]
                    question.explanation = normalized.explanation  # type: ignore[union-attr]
                    question.image_urls = normalized.image_urls  # type: ignore[union-attr]
                    question.option_image_urls = normalized.option_image_urls  # type: ignore[union-attr]
                    question.normalized_question_text = normalized.normalized_question_text  # type: ignore[union-attr]
                    question.question_text_hash = normalized.question_text_hash  # type: ignore[union-attr]
                    question.status = QuizQuestionStatus.PUBLISHED.value
                    question.ever_published = True
                    question.published_at = question.published_at or now
                    question.disabled_at = None
                else:
                    question.status = QuizQuestionStatus.DISABLED.value
                    question.disabled_at = now
                question.updated_by = admin_id
                question.lock_version += 1
            changed_fields: dict[str, dict[str, object]] = {}
            for question_id, question in questions.items():
                before = audit_before[question_id]
                after = {
                    field: getattr(question, field)
                    for field in before
                }
                for field, values in self._changed_fields(before, after).items():
                    changed_fields[f"{question_id}.{field}"] = values
            self._add_audit(
                db,
                admin_id=admin_id,
                action=f"question.batch_{action}",
                object_type="question",
                target_ids=[item.question_id for item in data.items],
                changed_fields=changed_fields,
            )
            try:
                await db.commit()
            except IntegrityError as exc:
                await db.rollback()
                commit_errors = [
                    AdminQuizBatchItemError(
                        question_id=item.question_id,
                        code=40001,
                        field=None,
                        message="题目状态更新失败，批量操作未提交",
                    )
                    for item in data.items
                ]
                # The business transaction was rolled back, including its
                # optimistic success audit. Persist the failed outcome in a
                # fresh transaction so every batch invocation remains
                # observable without compromising atomicity.
                try:
                    async with get_db_ctx() as audit_db:
                        self._add_audit(
                            audit_db,
                            admin_id=admin_id,
                            action=f"question.batch_{action}",
                            object_type="question",
                            result="failed",
                            target_ids=[item.question_id for item in data.items],
                            error_summary="题目状态更新失败，批量操作未提交",
                            permission="quiz:write",
                        )
                        await audit_db.commit()
                except Exception:
                    # Keep the original, fully rolled-back database failure
                    # observable instead of disguising it as a successful
                    # business-level validation response.
                    raise exc
                return AdminQuizBatchResponse(
                    succeeded=False,
                    updated_count=0,
                    errors=commit_errors,
                )
            return AdminQuizBatchResponse(
                succeeded=True,
                updated_count=len(questions),
                errors=[],
            )

    async def batch_publish_questions(
        self, data: AdminQuizBatchRequest, *, admin_id: int
    ) -> AdminQuizBatchResponse:
        return await self._batch_transition_questions(data, "publish", admin_id=admin_id)

    async def batch_disable_questions(
        self, data: AdminQuizBatchRequest, *, admin_id: int
    ) -> AdminQuizBatchResponse:
        return await self._batch_transition_questions(data, "disable", admin_id=admin_id)

    async def get_question_stats(self, question_id: int) -> AdminQuizQuestionStatsResponse:
        async with get_db_ctx() as db:
            question = await db.get(QuizQuestion, question_id)
            if question is None:
                raise NotFoundException("题目")
            stats = (
                await db.execute(
                    select(QuizQuestionStats).where(
                        QuizQuestionStats.question_id == question_id
                    )
                )
            ).scalar_one_or_none()
        practice_attempts = int(getattr(stats, "practice_first_attempts", 0) or 0)
        practice_correct = int(getattr(stats, "practice_first_correct", 0) or 0)
        exam_answers = int(getattr(stats, "exam_answers", 0) or 0)
        exam_correct = int(getattr(stats, "exam_correct", 0) or 0)
        return AdminQuizQuestionStatsResponse(
            question_id=question_id,
            practice_first_attempts=practice_attempts,
            practice_first_correct=practice_correct,
            practice_first_accuracy=(
                (
                    (Decimal(practice_correct) * Decimal(100))
                    / Decimal(practice_attempts)
                ).quantize(Decimal("0.1"))
                if practice_attempts
                else Decimal("0.0")
            ),
            exam_answers=exam_answers,
            exam_correct=exam_correct,
            exam_accuracy=(
                (
                    (Decimal(exam_correct) * Decimal(100))
                    / Decimal(exam_answers)
                ).quantize(Decimal("0.1"))
                if exam_answers
                else Decimal("0.0")
            ),
            aggregated_through=getattr(stats, "aggregated_through", None),
        )

    @staticmethod
    def _accuracy(correct: int, total: int) -> Decimal:
        if total <= 0:
            return Decimal("0.0")
        return (
            (Decimal(correct) * Decimal(100)) / Decimal(total)
        ).quantize(Decimal("0.1"))

    async def get_stats_overview(self) -> AdminQuizStatsOverviewResponse:
        """Return anonymous aggregate counters for the admin overview."""

        async with get_db_ctx() as db:
            library_row = (
                await db.execute(
                    select(
                        func.count(QuizLibrary.id),
                        func.count(QuizLibrary.id).filter(
                            QuizLibrary.status == "draft"
                        ),
                        func.count(QuizLibrary.id).filter(
                            QuizLibrary.status == "published"
                        ),
                        func.count(QuizLibrary.id).filter(
                            QuizLibrary.status == "suspended"
                        ),
                        func.count(QuizLibrary.id).filter(
                            QuizLibrary.status == "archived"
                        ),
                    )
                    .where(QuizLibrary.status != "deleted")
                )
            ).one()
            module_row = (
                await db.execute(
                    select(
                        func.count(QuizModule.id),
                        func.count(QuizModule.id).filter(
                            QuizModule.status == "active"
                        ),
                        func.count(QuizModule.id).filter(
                            QuizModule.status == "disabled"
                        ),
                    )
                    .select_from(QuizModule)
                    .join(QuizLibrary, QuizLibrary.id == QuizModule.library_id)
                    .where(
                        QuizLibrary.status != "deleted",
                        QuizModule.status != "deleted",
                    )
                )
            ).one()
            knowledge_point_row = (
                await db.execute(
                    select(
                        func.count(QuizKnowledgePoint.id),
                        func.count(QuizKnowledgePoint.id).filter(
                            QuizKnowledgePoint.status == "active"
                        ),
                        func.count(QuizKnowledgePoint.id).filter(
                            QuizKnowledgePoint.status == "disabled"
                        ),
                    )
                    .select_from(QuizKnowledgePoint)
                    .join(
                        QuizLibrary,
                        QuizLibrary.id == QuizKnowledgePoint.library_id,
                    )
                    .where(
                        QuizLibrary.status != "deleted",
                        QuizKnowledgePoint.status != "deleted",
                    )
                )
            ).one()
            question_row = (
                await db.execute(
                    select(
                        func.count(QuizQuestion.id),
                        func.count(QuizQuestion.id).filter(
                            QuizQuestion.status == "draft"
                        ),
                        func.count(QuizQuestion.id).filter(
                            QuizQuestion.status == "published"
                        ),
                        func.count(QuizQuestion.id).filter(
                            QuizQuestion.status == "disabled"
                        ),
                    )
                    .select_from(QuizQuestion)
                    .join(QuizLibrary, QuizLibrary.id == QuizQuestion.library_id)
                    .where(
                        QuizLibrary.status != "deleted",
                        QuizQuestion.status != "deleted",
                    )
                )
            ).one()
            practice_session_count = int(
                await db.scalar(select(func.count(QuizPracticeSession.id))) or 0
            )
            stats_row = (
                await db.execute(
                    select(
                        func.coalesce(func.sum(QuizQuestionStats.practice_first_attempts), 0),
                        func.coalesce(func.sum(QuizQuestionStats.practice_first_correct), 0),
                        func.coalesce(func.sum(QuizQuestionStats.exam_answers), 0),
                        func.coalesce(func.sum(QuizQuestionStats.exam_correct), 0),
                        func.max(QuizQuestionStats.aggregated_through),
                    )
                )
            ).one()
            exam_row = (
                await db.execute(
                    select(
                        func.count(QuizExam.id).filter(QuizExam.status == "completed"),
                        func.count(QuizExam.id).filter(QuizExam.status == "timed_out"),
                    )
                )
            ).one()

        practice_attempts = int(stats_row[0] or 0)
        practice_correct = int(stats_row[1] or 0)
        exam_answers = int(stats_row[2] or 0)
        exam_correct = int(stats_row[3] or 0)
        calculated_at = datetime.now(timezone.utc)
        return AdminQuizStatsOverviewResponse(
            calculated_at=calculated_at,
            aggregated_through=stats_row[4],
            library_count=int(library_row[0] or 0),
            draft_library_count=int(library_row[1] or 0),
            published_library_count=int(library_row[2] or 0),
            suspended_library_count=int(library_row[3] or 0),
            archived_library_count=int(library_row[4] or 0),
            module_count=int(module_row[0] or 0),
            active_module_count=int(module_row[1] or 0),
            disabled_module_count=int(module_row[2] or 0),
            knowledge_point_count=int(knowledge_point_row[0] or 0),
            active_knowledge_point_count=int(knowledge_point_row[1] or 0),
            disabled_knowledge_point_count=int(knowledge_point_row[2] or 0),
            question_count=int(question_row[0] or 0),
            draft_question_count=int(question_row[1] or 0),
            published_question_count=int(question_row[2] or 0),
            disabled_question_count=int(question_row[3] or 0),
            practice_session_count=practice_session_count,
            practice_first_attempts=practice_attempts,
            practice_first_correct=practice_correct,
            practice_first_accuracy=self._accuracy(practice_correct, practice_attempts),
            completed_exam_count=int(exam_row[0] or 0),
            timed_out_exam_count=int(exam_row[1] or 0),
            exam_answers=exam_answers,
            exam_correct=exam_correct,
            exam_accuracy=self._accuracy(exam_correct, exam_answers),
        )

    async def list_question_stats(
        self, query: AdminQuizStatsQuestionQuery
    ) -> PaginatedData[AdminQuizQuestionStatsListItem]:
        """Return filtered per-question aggregates without any user dimension."""

        async with get_db_ctx() as db:
            filters = [QuizLibrary.status != "deleted"]
            if query.library_id is not None:
                filters.append(QuizLibrary.id == query.library_id)
            if query.module_id is not None:
                filters.append(QuizModule.id == query.module_id)
            if query.knowledge_point_id is not None:
                filters.append(
                    QuizKnowledgePoint.id == query.knowledge_point_id
                )
            if query.question_type is not None:
                filters.append(
                    QuizQuestion.question_type == self._enum_value(query.question_type)
                )
            if query.status is not None:
                filters.append(QuizQuestion.status == self._enum_value(query.status))
            elif not query.include_deleted:
                filters.append(QuizQuestion.status != "deleted")
            if query.keyword:
                keyword = f"%{query.keyword.strip()}%"
                filters.append(
                    or_(
                        QuizQuestion.question_text.ilike(keyword),
                        QuizQuestion.normalized_question_text.ilike(keyword),
                    )
                )
            total = int(
                await db.scalar(
                    select(func.count(QuizQuestion.id))
                    .select_from(QuizQuestion)
                    .join(QuizLibrary, QuizLibrary.id == QuizQuestion.library_id)
                    .join(
                        QuizKnowledgePoint,
                        (QuizKnowledgePoint.id == QuizQuestion.knowledge_point_id)
                        & (QuizKnowledgePoint.library_id == QuizQuestion.library_id),
                    )
                    .join(
                        QuizModule,
                        (QuizModule.id == QuizKnowledgePoint.module_id)
                        & (QuizModule.library_id == QuizLibrary.id),
                    )
                    .where(*filters)
                )
                or 0
            )
            rows = (
                await db.execute(
                    select(
                        QuizQuestion,
                        QuizLibrary,
                        QuizModule,
                        QuizKnowledgePoint,
                        QuizQuestionStats,
                    )
                    .select_from(QuizQuestion)
                    .join(QuizLibrary, QuizLibrary.id == QuizQuestion.library_id)
                    .join(
                        QuizKnowledgePoint,
                        (QuizKnowledgePoint.id == QuizQuestion.knowledge_point_id)
                        & (QuizKnowledgePoint.library_id == QuizQuestion.library_id),
                    )
                    .join(
                        QuizModule,
                        (QuizModule.id == QuizKnowledgePoint.module_id)
                        & (QuizModule.library_id == QuizLibrary.id),
                    )
                    .outerjoin(
                        QuizQuestionStats,
                        QuizQuestionStats.question_id == QuizQuestion.id,
                    )
                    .where(*filters)
                    .order_by(*self._question_stats_order(query))
                    .offset((query.page - 1) * query.page_size)
                    .limit(query.page_size)
                )
            ).all()

        items: list[AdminQuizQuestionStatsListItem] = []
        for question, library, module, knowledge_point, stats in rows:
            practice_attempts = int(
                getattr(stats, "practice_first_attempts", 0) or 0
            )
            practice_correct = int(
                getattr(stats, "practice_first_correct", 0) or 0
            )
            exam_answers = int(getattr(stats, "exam_answers", 0) or 0)
            exam_correct = int(getattr(stats, "exam_correct", 0) or 0)
            items.append(
                AdminQuizQuestionStatsListItem(
                    question_id=question.id,
                    question_text=question.question_text,
                    library_id=library.id,
                    library_name=library.name,
                    module_id=module.id,
                    module_name=module.name,
                    knowledge_point_id=knowledge_point.id,
                    knowledge_point_name=knowledge_point.name,
                    question_type=question.question_type,
                    status=question.status,
                    practice_first_attempts=practice_attempts,
                    practice_first_correct=practice_correct,
                    practice_first_accuracy=self._accuracy(
                        practice_correct, practice_attempts
                    ),
                    exam_answers=exam_answers,
                    exam_correct=exam_correct,
                    exam_accuracy=self._accuracy(exam_correct, exam_answers),
                    aggregated_through=getattr(stats, "aggregated_through", None),
                )
            )
        return PaginatedData[AdminQuizQuestionStatsListItem](
            items=items,
            total=total,
            page=query.page,
            page_size=query.page_size,
        )

    @staticmethod
    def _question_stats_order(query: AdminQuizStatsQuestionQuery) -> list:
        attempts = func.coalesce(QuizQuestionStats.practice_first_attempts, 0)
        correct = func.coalesce(QuizQuestionStats.practice_first_correct, 0)
        if query.sort == "practice_wrong_count":
            primary = attempts - correct
        elif query.sort == "practice_first_attempts":
            primary = attempts
        else:
            primary = QuizQuestion.updated_at
        direction = primary.desc() if query.order == "desc" else primary.asc()
        id_direction = (
            QuizQuestion.id.desc() if query.order == "desc" else QuizQuestion.id.asc()
        )
        return [direction, id_direction]

    async def get_daily_stats(
        self, query: AdminQuizDailyStatsQuery
    ) -> list[AdminQuizDailyStatsItem]:
        """Daily practice volume and active users from the check-in ledger."""

        async with get_db_ctx() as db:
            today = datetime.now(ZoneInfo(settings.APP_TIMEZONE)).date()
            start = today - timedelta(days=query.days - 1)
            rows = (
                await db.execute(
                    select(
                        QuizCheckin.checkin_date,
                        func.sum(QuizCheckin.questions_completed),
                        func.count(func.distinct(QuizCheckin.user_id)),
                    )
                    .where(
                        QuizCheckin.checkin_date >= start,
                        QuizCheckin.checkin_date <= today,
                    )
                    .group_by(QuizCheckin.checkin_date)
                )
            ).all()
        by_date = {
            row[0]: (int(row[1] or 0), int(row[2] or 0)) for row in rows
        }
        items: list[AdminQuizDailyStatsItem] = []
        for offset in range(query.days):
            day = start + timedelta(days=offset)
            attempts, active_users = by_date.get(day, (0, 0))
            items.append(
                AdminQuizDailyStatsItem(
                    date=day,
                    practice_attempts=attempts,
                    active_users=active_users,
                )
            )
        return items

    async def list_user_stats(
        self, query: AdminQuizUserStatsQuery
    ) -> PaginatedData[AdminQuizUserStatsListItem]:
        """Leaderboard of users ranked by cumulative practice attempts."""

        async with get_db_ctx() as db:
            base = (
                select(QuizUserStats, User, UserProfile)
                .select_from(QuizUserStats)
                .join(User, User.id == QuizUserStats.user_id)
                .outerjoin(UserProfile, UserProfile.user_id == QuizUserStats.user_id)
            )
            total = int(
                await db.scalar(select(func.count()).select_from(base.subquery()))
                or 0
            )
            rows = (
                await db.execute(
                    base.order_by(
                        QuizUserStats.practice_total_attempts.desc(),
                        QuizUserStats.user_id.asc(),
                    )
                    .offset((query.page - 1) * query.page_size)
                    .limit(query.page_size)
                )
            ).all()
        items = [
            AdminQuizUserStatsListItem(
                user_id=int(stats.user_id),
                nickname=getattr(profile, "nickname", None),
                phone_masked=self._mask_phone(
                    getattr(user, "phone", None)
                    or getattr(profile, "phone", None)
                ),
                practice_total_attempts=int(stats.practice_total_attempts),
                practice_first_attempts=int(stats.practice_first_attempts),
                practice_first_correct=int(stats.practice_first_correct),
                practice_answered_questions=int(stats.practice_answered_questions),
                checkin_days=int(stats.checkin_days),
                consecutive_days=int(stats.consecutive_days),
            )
            for stats, user, profile in rows
        ]
        return PaginatedData[AdminQuizUserStatsListItem](
            items=items,
            total=total,
            page=query.page,
            page_size=query.page_size,
        )

    async def get_user_practice_stats(
        self, query: AdminQuizUserPracticeQuery
    ) -> AdminQuizUserPracticeStats:
        """One student's practice activity in one library for a date range.

        Attempts (including retries) drive the practice numbers; settled exam
        rounds are listed in their own section.  Days are bucketed in the
        application timezone so the chart matches the check-in calendar.
        """

        timezone_name = settings.APP_TIMEZONE
        start_at = datetime.combine(query.date_from, time.min, tzinfo=ZoneInfo(timezone_name))
        end_at = datetime.combine(
            query.date_to + timedelta(days=1), time.min, tzinfo=ZoneInfo(timezone_name)
        )
        practice_day = func.date(
            func.timezone(timezone_name, QuizPracticeAttempt.submitted_at)
        ).label("practice_day")

        async with get_db_ctx() as db:
            user = (
                await db.execute(select(User).where(User.id == query.user_id))
            ).scalar_one_or_none()
            if user is None:
                raise NotFoundException("用户")
            library = (
                await db.execute(
                    select(QuizLibrary).where(QuizLibrary.id == query.library_id)
                )
            ).scalar_one_or_none()
            if library is None:
                raise NotFoundException("题库")

            base = (
                select()
                .select_from(QuizPracticeAttempt)
                .join(
                    QuizPracticeSessionQuestion,
                    QuizPracticeSessionQuestion.id
                    == QuizPracticeAttempt.session_question_id,
                )
                .join(
                    QuizQuestion,
                    QuizQuestion.id == QuizPracticeSessionQuestion.question_id,
                )
                .where(
                    QuizPracticeAttempt.user_id == query.user_id,
                    QuizQuestion.library_id == query.library_id,
                    QuizPracticeAttempt.submitted_at >= start_at,
                    QuizPracticeAttempt.submitted_at < end_at,
                )
            )
            summary = (
                await db.execute(
                    base.with_only_columns(
                        func.count(QuizPracticeAttempt.id),
                        func.count(func.distinct(QuizPracticeSessionQuestion.question_id)),
                        func.count(QuizPracticeAttempt.id).filter(
                            QuizPracticeAttempt.is_first_attempt.is_(True)
                        ),
                        func.count(QuizPracticeAttempt.id).filter(
                            QuizPracticeAttempt.is_first_attempt.is_(True),
                            QuizPracticeAttempt.is_correct.is_(True),
                        ),
                    )
                )
            ).one()
            daily_rows = (
                await db.execute(
                    base.with_only_columns(
                        practice_day,
                        func.count(QuizPracticeAttempt.id),
                        func.sum(
                            case(
                                (QuizPracticeAttempt.is_correct.is_(True), 1),
                                else_=0,
                            )
                        ),
                    )
                    .group_by(practice_day)
                    .order_by(practice_day.asc())
                )
            ).all()
            exam_rows = (
                await db.execute(
                    select(QuizExam)
                    .where(
                        QuizExam.user_id == query.user_id,
                        QuizExam.library_id == query.library_id,
                        QuizExam.started_at >= start_at,
                        QuizExam.started_at < end_at,
                    )
                    .order_by(QuizExam.started_at.desc(), QuizExam.id.desc())
                )
            ).scalars().all()

        total_attempts = int(summary[0] or 0)
        answered_questions = int(summary[1] or 0)
        first_attempts = int(summary[2] or 0)
        first_correct = int(summary[3] or 0)
        exam_rounds: list[AdminQuizUserExamRound] = []
        for exam in exam_rows:
            exam_rounds.append(
                AdminQuizUserExamRound(
                    exam_id=int(exam.id),
                    status=str(exam.status),
                    started_at=exam.started_at,
                    settled_at=(exam.submitted_at or exam.timed_out_at),
                    question_count=int(exam.question_count),
                    correct_count=(
                        int(exam.correct_count)
                        if exam.correct_count is not None
                        else None
                    ),
                    wrong_count=(
                        int(exam.wrong_count) if exam.wrong_count is not None else None
                    ),
                    unanswered_count=(
                        int(exam.unanswered_count)
                        if exam.unanswered_count is not None
                        else None
                    ),
                    score=exam.score,
                )
            )
        settled_scores = [
            (round.settled_at, round.score)
            for round in exam_rounds
            if round.settled_at is not None and round.score is not None
        ]
        daily: list[AdminQuizUserPracticeDay] = []
        for row_day, attempts, correct in daily_rows:
            attempts = int(attempts or 0)
            correct = int(correct or 0)
            daily.append(
                AdminQuizUserPracticeDay(
                    date=row_day,
                    attempts=attempts,
                    correct=correct,
                    accuracy=(
                        (Decimal(correct) * Decimal("100") / Decimal(attempts)).quantize(
                            Decimal("0.1")
                        )
                        if attempts
                        else Decimal("0.0")
                    ),
                )
            )
        return AdminQuizUserPracticeStats(
            user_id=query.user_id,
            library_id=query.library_id,
            date_from=query.date_from,
            date_to=query.date_to,
            total_attempts=total_attempts,
            answered_questions=answered_questions,
            first_attempts=first_attempts,
            first_correct=first_correct,
            first_accuracy=(
                (Decimal(first_correct) * Decimal("100") / Decimal(first_attempts)).quantize(
                    Decimal("0.1")
                )
                if first_attempts
                else Decimal("0.0")
            ),
            active_days=len(daily),
            daily=daily,
            exam_rounds=exam_rounds,
            exam_settled_count=len(settled_scores),
            exam_average_score=(
                (
                    sum(item[1] for item in settled_scores) / len(settled_scores)
                ).quantize(Decimal("0.1"))
                if settled_scores
                else None
            ),
            exam_highest_score=(
                max(item[1] for item in settled_scores) if settled_scores else None
            ),
            exam_latest_score=(max(settled_scores)[1] if settled_scores else None),
        )

    @staticmethod
    def _mask_phone(phone: str | None) -> str | None:
        if not phone or len(phone) < 7:
            return phone
        return f"{phone[:3]}****{phone[-4:]}"

    async def aggregate_question_stats(
        self,
        *,
        now: datetime | None = None,
        question_ids: list[int] | None = None,
    ) -> bool:
        """Refresh dirty per-question practice and settled-exam counters.

        The worker uses a short overlap on the last aggregate timestamp. This
        handles transactions that update an answer and its parent exam close
        together, while keeping the normal query set bounded to recently
        changed questions. Each affected question is recomputed from the
        immutable attempt/answer history, so retries are idempotent.
        """
        cutoff = now or datetime.now(timezone.utc)
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=timezone.utc)
        scoped_question_ids = (
            sorted({int(question_id) for question_id in question_ids})
            if question_ids is not None
            else None
        )
        if scoped_question_ids == []:
            return False

        async with get_db_ctx() as db:
            # Multiple API processes may run the embedded quiz worker. A
            # transaction-scoped advisory lock makes aggregation single-writer
            # without adding another schema table.
            bind = getattr(db, "bind", None)
            dialect = getattr(getattr(bind, "dialect", None), "name", None)
            if dialect == "postgresql":
                lock_result = await db.execute(
                    text("SELECT pg_try_advisory_xact_lock(:lock_key)"),
                    {"lock_key": 0x7175697A5F737461},
                )
                if not bool(lock_result.scalar()):
                    return False

            watermark_stmt = select(func.max(QuizQuestionStats.aggregated_through))
            if scoped_question_ids is not None:
                watermark_stmt = watermark_stmt.where(
                    QuizQuestionStats.question_id.in_(scoped_question_ids)
                )
            watermark = await db.scalar(watermark_stmt)
            if watermark is None:
                since = datetime(1970, 1, 1, tzinfo=timezone.utc)
            else:
                if watermark.tzinfo is None:
                    watermark = watermark.replace(tzinfo=timezone.utc)
                # Overlap avoids missing rows committed at the same timestamp
                # as a prior worker run or updated during a settlement batch.
                since = watermark - timedelta(seconds=60)

            practice_dirty = (
                select(QuizPracticeSessionQuestion.question_id.label("question_id"))
                .join(
                    QuizPracticeAttempt,
                    (QuizPracticeAttempt.session_question_id == QuizPracticeSessionQuestion.id)
                    & (QuizPracticeAttempt.session_id == QuizPracticeSessionQuestion.session_id),
                )
                .where(
                    QuizPracticeAttempt.submitted_at > since,
                    QuizPracticeAttempt.submitted_at <= cutoff,
                )
                .distinct()
            )
            if scoped_question_ids is not None:
                practice_dirty = practice_dirty.where(
                    QuizPracticeSessionQuestion.question_id.in_(scoped_question_ids)
                )
            settled_statuses = ("completed", "timed_out")
            exam_answer_dirty = (
                select(QuizExamQuestion.question_id.label("question_id"))
                .join(
                    QuizExamAnswer,
                    (QuizExamAnswer.exam_question_id == QuizExamQuestion.id)
                    & (QuizExamAnswer.exam_id == QuizExamQuestion.exam_id),
                )
                .join(QuizExam, QuizExam.id == QuizExamQuestion.exam_id)
                .where(
                    QuizExam.status.in_(settled_statuses),
                    QuizExamAnswer.updated_at > since,
                    QuizExamAnswer.updated_at <= cutoff,
                )
                .distinct()
            )
            exam_settlement_dirty = (
                select(QuizExamQuestion.question_id.label("question_id"))
                .join(QuizExam, QuizExam.id == QuizExamQuestion.exam_id)
                .where(
                    QuizExam.status.in_(settled_statuses),
                    QuizExam.updated_at > since,
                    QuizExam.updated_at <= cutoff,
                )
                .distinct()
            )
            if scoped_question_ids is not None:
                exam_answer_dirty = exam_answer_dirty.where(
                    QuizExamQuestion.question_id.in_(scoped_question_ids)
                )
                exam_settlement_dirty = exam_settlement_dirty.where(
                    QuizExamQuestion.question_id.in_(scoped_question_ids)
                )
            dirty = union(
                practice_dirty,
                exam_answer_dirty,
                exam_settlement_dirty,
            ).cte("dirty_question_ids")

            practice_counts = (
                select(
                    QuizPracticeSessionQuestion.question_id.label("question_id"),
                    func.count(QuizPracticeAttempt.id).label("practice_first_attempts"),
                    func.count(QuizPracticeAttempt.id)
                    .filter(QuizPracticeAttempt.is_correct.is_(True))
                    .label("practice_first_correct"),
                )
                .join(
                    QuizPracticeAttempt,
                    (QuizPracticeAttempt.session_question_id == QuizPracticeSessionQuestion.id)
                    & (QuizPracticeAttempt.session_id == QuizPracticeSessionQuestion.session_id),
                )
                .join(
                    dirty,
                    dirty.c.question_id == QuizPracticeSessionQuestion.question_id,
                )
                .where(QuizPracticeAttempt.is_first_attempt.is_(True))
                .group_by(QuizPracticeSessionQuestion.question_id)
                .cte("practice_question_counts")
            )
            exam_counts = (
                select(
                    QuizExamQuestion.question_id.label("question_id"),
                    func.count(QuizExamAnswer.id).label("exam_answers"),
                    func.count(QuizExamAnswer.id)
                    .filter(QuizExamAnswer.is_correct.is_(True))
                    .label("exam_correct"),
                )
                .join(
                    QuizExamAnswer,
                    (QuizExamAnswer.exam_question_id == QuizExamQuestion.id)
                    & (QuizExamAnswer.exam_id == QuizExamQuestion.exam_id),
                )
                .join(QuizExam, QuizExam.id == QuizExamQuestion.exam_id)
                .join(dirty, dirty.c.question_id == QuizExamQuestion.question_id)
                .where(
                    QuizExam.status.in_(settled_statuses),
                    QuizExamAnswer.is_correct.is_not(None),
                )
                .group_by(QuizExamQuestion.question_id)
                .cte("exam_question_counts")
            )
            combined = (
                select(
                    dirty.c.question_id,
                    func.coalesce(practice_counts.c.practice_first_attempts, 0).label(
                        "practice_first_attempts"
                    ),
                    func.coalesce(practice_counts.c.practice_first_correct, 0).label(
                        "practice_first_correct"
                    ),
                    func.coalesce(exam_counts.c.exam_answers, 0).label("exam_answers"),
                    func.coalesce(exam_counts.c.exam_correct, 0).label("exam_correct"),
                )
                .select_from(dirty)
                .outerjoin(
                    practice_counts,
                    practice_counts.c.question_id == dirty.c.question_id,
                )
                .outerjoin(
                    exam_counts,
                    exam_counts.c.question_id == dirty.c.question_id,
                )
            )
            rows = (await db.execute(combined)).mappings().all()
            if not rows:
                return False

            values = [
                {
                    "question_id": int(row["question_id"]),
                    "practice_first_attempts": int(row["practice_first_attempts"]),
                    "practice_first_correct": int(row["practice_first_correct"]),
                    "exam_answers": int(row["exam_answers"]),
                    "exam_correct": int(row["exam_correct"]),
                    "aggregated_through": cutoff,
                }
                for row in rows
            ]
            statement = pg_insert(QuizQuestionStats).values(values)
            statement = statement.on_conflict_do_update(
                index_elements=[QuizQuestionStats.question_id],
                set_={
                    "practice_first_attempts": statement.excluded.practice_first_attempts,
                    "practice_first_correct": statement.excluded.practice_first_correct,
                    "exam_answers": statement.excluded.exam_answers,
                    "exam_correct": statement.excluded.exam_correct,
                    "aggregated_through": statement.excluded.aggregated_through,
                    "updated_at": cutoff,
                },
            )
            await db.execute(statement)
            await db.commit()
            return True

    async def list_audit_logs(
        self,
        query: AdminQuizAuditQuery | None = None,
    ) -> PaginatedData[AdminQuizAuditLogResponse]:
        """Return immutable quiz-management audit rows with stable pagination."""
        query = query or AdminQuizAuditQuery()
        async with get_db_ctx() as db:
            stmt = select(QuizAdminAuditLog)
            if query.admin_id is not None:
                stmt = stmt.where(QuizAdminAuditLog.admin_id == query.admin_id)
            if query.action is not None:
                stmt = stmt.where(QuizAdminAuditLog.action == query.action)
            if query.object_type is not None:
                stmt = stmt.where(QuizAdminAuditLog.object_type == query.object_type)
            if query.object_id is not None:
                stmt = stmt.where(QuizAdminAuditLog.object_id == query.object_id)
            if query.result is not None:
                stmt = stmt.where(QuizAdminAuditLog.result == query.result)
            if query.request_id is not None:
                stmt = stmt.where(QuizAdminAuditLog.request_id == query.request_id)
            if query.start_at is not None:
                stmt = stmt.where(QuizAdminAuditLog.created_at >= query.start_at)
            if query.end_at is not None:
                stmt = stmt.where(QuizAdminAuditLog.created_at <= query.end_at)

            total = (await db.execute(
                select(func.count()).select_from(stmt.subquery())
            )).scalar() or 0
            result = await db.execute(
                stmt.order_by(
                    QuizAdminAuditLog.created_at.desc(),
                    QuizAdminAuditLog.id.desc(),
                )
                .offset((query.page - 1) * query.page_size)
                .limit(query.page_size)
            )
            rows = list(result.scalars().all())

        return PaginatedData[AdminQuizAuditLogResponse](
            items=[AdminQuizAuditLogResponse.model_validate(row) for row in rows],
            total=total,
            page=query.page,
            page_size=query.page_size,
        )

    # ── Import ──

    @staticmethod
    def _import_object_key(batch_key: str, suffix: str) -> str:
        prefix = settings.QUIZ_OSS_PREFIX.strip("/") or "quiz-imports"
        return f"{prefix}/{batch_key}.{suffix}"

    @staticmethod
    def _terminal_import_expiry(finished_at: datetime) -> datetime:
        return finished_at + timedelta(days=settings.QUIZ_IMPORT_RETENTION_DAYS)

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

    @staticmethod
    def _import_error(
        *,
        row: int | None,
        question_index: int | None,
        field: str | None,
        error_code: str,
        message: str,
    ) -> dict[str, object]:
        return {
            "row": row,
            "question_index": question_index,
            "field": field,
            "error_code": error_code,
            "message": message,
        }

    @staticmethod
    def _impact_version(payload: dict[str, object]) -> str:
        # ``calculated_at`` is presentation metadata. Recalculating an
        # otherwise identical tree must not force the administrator to confirm
        # it again.
        payload = {
            key: value for key, value in payload.items() if key != "calculated_at"
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hmac.new(
            settings.JWT_SECRET.encode("utf-8"), canonical, hashlib.sha256
        ).hexdigest()

    @classmethod
    def _impact_snapshot_payload(
        cls,
        *,
        tree: list[dict[str, object]],
        new_category_count: int,
        reused_category_count: int,
        affected_question_count: int,
        blocking_reasons: list[str],
        calculated_at: datetime,
    ) -> dict[str, object]:
        return {
            "tree": tree,
            "new_category_count": new_category_count,
            "reused_category_count": reused_category_count,
            "affected_question_count": affected_question_count,
            "blocking_reasons": blocking_reasons,
            "calculated_at": calculated_at.isoformat(),
        }

    @staticmethod
    async def _replace_import_errors(
        db,
        *,
        job_id: int,
        validation_version: int,
        errors: list[dict[str, object]],
    ) -> None:
        await db.execute(delete(QuizImportError).where(QuizImportError.job_id == job_id))
        for item in errors:
            db.add(
                QuizImportError(
                    job_id=job_id,
                    validation_version=validation_version,
                    row=item.get("row"),
                    question_index=item.get("question_index"),
                    field=item.get("field"),
                    error_code=str(item.get("error_code") or "validation_error"),
                    message=str(item.get("message") or "参数校验失败")[:1024],
                )
            )

    @staticmethod
    def _local_import_path(object_key: str) -> Path:
        root = (Path(settings.UPLOAD_DIR).resolve() / "private").resolve()
        target = (root / object_key).resolve()
        if root not in target.parents:
            raise ValidationException("题库导入对象键无效")
        return target

    @staticmethod
    def _quiz_oss_bucket():
        if not all(
            (
                settings.QUIZ_OSS_ENDPOINT,
                settings.QUIZ_OSS_BUCKET,
                settings.QUIZ_OSS_ACCESS_KEY_ID,
                settings.QUIZ_OSS_ACCESS_KEY_SECRET,
            )
        ):
            raise ThirdPartyException("阿里云 OSS 配置不完整")
        try:
            import oss2
        except ImportError as exc:
            raise ThirdPartyException("阿里云 OSS SDK 未安装") from exc
        auth = oss2.Auth(
            settings.QUIZ_OSS_ACCESS_KEY_ID,
            settings.QUIZ_OSS_ACCESS_KEY_SECRET,
        )
        return oss2.Bucket(
            auth,
            settings.QUIZ_OSS_ENDPOINT,
            settings.QUIZ_OSS_BUCKET,
        )

    async def _put_import_object(
        self, object_key: str, data: bytes, content_type: str
    ) -> None:
        if settings.QUIZ_IMPORT_STORAGE_TYPE == "local":
            path = self._local_import_path(object_key)

            def _write() -> None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)

            try:
                await asyncio.to_thread(_write)
            except OSError as exc:
                # Keep local filesystem details out of the debug response and
                # expose this as an infrastructure failure, not an unexpected
                # application 500. Readiness probes report the same condition
                # before an administrator starts an import.
                raise ThirdPartyException("题库导入存储不可用") from exc
            return
        if settings.QUIZ_IMPORT_STORAGE_TYPE != "aliyun_oss":
            if settings.QUIZ_IMPORT_STORAGE_TYPE == "disabled":
                raise ThirdPartyException("题库 OSS 未配置，导入功能不可用")
            raise ThirdPartyException("未知的题库导入存储类型")

        def _upload() -> None:
            try:
                result = self._quiz_oss_bucket().put_object(
                    object_key,
                    data,
                    headers={"Content-Type": content_type},
                )
                if result.status // 100 != 2:
                    raise ThirdPartyException("阿里云 OSS 上传导入文件失败")
            except ThirdPartyException:
                raise
            except Exception as exc:
                raise ThirdPartyException("阿里云 OSS 上传导入文件失败") from exc

        await asyncio.to_thread(_upload)

    async def _get_import_object(self, object_key: str) -> bytes:
        if settings.QUIZ_IMPORT_STORAGE_TYPE == "local":
            path = self._local_import_path(object_key)
            try:
                if not path.is_file():
                    raise ValidationException("导入源文件不存在")
                return await asyncio.to_thread(path.read_bytes)
            except ValidationException:
                raise
            except OSError as exc:
                raise ThirdPartyException("题库导入存储不可用") from exc
        if settings.QUIZ_IMPORT_STORAGE_TYPE != "aliyun_oss":
            if settings.QUIZ_IMPORT_STORAGE_TYPE == "disabled":
                raise ThirdPartyException("题库 OSS 未配置，导入功能不可用")
            raise ThirdPartyException("未知的题库导入存储类型")

        def _download() -> bytes:
            try:
                return self._quiz_oss_bucket().get_object(object_key).read()
            except Exception as exc:
                raise ThirdPartyException("阿里云 OSS 读取导入文件失败") from exc

        return await asyncio.to_thread(_download)

    async def _delete_import_object(self, object_key: str | None) -> None:
        if not object_key:
            return
        if settings.QUIZ_IMPORT_STORAGE_TYPE == "local":
            path = self._local_import_path(object_key)
            try:
                if path.is_file():
                    await asyncio.to_thread(path.unlink)
            except OSError as exc:
                raise ThirdPartyException("题库导入存储不可用") from exc
            return
        if settings.QUIZ_IMPORT_STORAGE_TYPE != "aliyun_oss":
            return

        def _delete() -> None:
            try:
                result = self._quiz_oss_bucket().delete_object(object_key)
                status = int(getattr(result, "status", 204) or 204)
                if status // 100 != 2:
                    raise ThirdPartyException("阿里云 OSS 删除导入文件失败")
            except ThirdPartyException:
                raise
            except Exception as exc:
                raise ThirdPartyException("阿里云 OSS 删除导入文件失败") from exc

        await asyncio.to_thread(_delete)

    async def _signed_import_url(
        self,
        job: QuizImportJob,
        *,
        expires_at: datetime,
        object_kind: str = "report",
        accessor_admin_id: int | None = None,
    ) -> str:
        if object_kind not in {"source", "report"}:
            raise ValidationException("不支持的题库导入对象类型")
        object_key = (
            job.source_object_key if object_kind == "source" else job.report_object_key
        )
        resource_name = "导入源文件" if object_kind == "source" else "错误报告"
        if not object_key:
            raise NotFoundException(resource_name)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            raise ValidationException("错误报告链接已过期")
        if settings.QUIZ_IMPORT_STORAGE_TYPE == "aliyun_oss":
            expires = max(1, int((expires_at - datetime.now(timezone.utc)).total_seconds()))
            if object_kind == "source":
                extension = ".csv" if job.source_type == "csv" else ".json"
                download_name = f"quiz-import-{job.id}{extension}"
            else:
                download_name = f"quiz-import-{job.id}-errors.json"

            def _sign() -> str:
                try:
                    return self._quiz_oss_bucket().sign_url(
                        "GET",
                        object_key,
                        expires,
                        params={
                            "response-content-disposition": (
                                f'attachment; filename="{download_name}"'
                            )
                        },
                    )
                except ThirdPartyException:
                    raise
                except Exception as exc:
                    raise ThirdPartyException(
                        f"阿里云 OSS 生成{resource_name}地址失败"
                    ) from exc

            return await asyncio.to_thread(_sign)

        if settings.QUIZ_IMPORT_STORAGE_TYPE == "disabled":
            raise ThirdPartyException("题库 OSS 未配置，导入功能不可用")
        if settings.QUIZ_IMPORT_STORAGE_TYPE != "local":
            raise ThirdPartyException("未知的题库导入存储类型")

        # Development/local storage uses a short HMAC URL so a browser download
        # does not need to forward the admin's bearer token.
        expires_unix = int(expires_at.timestamp())
        signed_admin_id = accessor_admin_id or job.admin_id or 0
        payload = f"{job.id}:{object_kind}:{expires_unix}:{signed_admin_id}"
        token = hmac.new(
            settings.JWT_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return (
            f"/admin/quiz/imports/{job.id}/{object_kind}"
            f"?expires={expires_unix}&admin_id={signed_admin_id}&token={token}"
        )

    async def create_import_job(
        self,
        *,
        source_type: str,
        content: bytes,
        admin_id: int,
        filename: str,
        library_id: int | None = None,
    ) -> QuizImportJob:
        if source_type not in {"csv", "json"}:
            raise ValidationException("导入类型必须为 csv 或 json")
        expected_suffix = f".{source_type}"
        if not Path(filename).name.lower().endswith(expected_suffix):
            raise ValidationException(f"文件扩展名必须为 {expected_suffix}")
        if not 1 <= len(content) <= settings.QUIZ_IMPORT_MAX_FILE_BYTES:
            raise ValidationException("导入文件大小必须在 1 B 至 10 MiB 之间")
        batch_key = uuid.uuid4().hex
        suffix = "csv" if source_type == "csv" else "json"
        source_key = self._import_object_key(batch_key, suffix)
        await self._put_import_object(
            source_key,
            content,
            "text/csv; charset=utf-8" if source_type == "csv" else "application/json",
        )
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=settings.QUIZ_IMPORT_RETENTION_DAYS)
        persisted = False
        try:
            async with get_db_ctx() as db:
                if library_id is not None:
                    library = await db.get(QuizLibrary, library_id)
                    if library is None:
                        raise NotFoundException("题库")
                    if library.status in {"archived", "deleted"}:
                        raise BusinessException("已归档或删除题库不可导入")
                job = QuizImportJob(
                    admin_id=admin_id,
                    library_id=library_id,
                    import_batch_key=batch_key,
                    source_type=source_type,
                    status="queued",
                    source_object_key=source_key,
                    source_size_bytes=len(content),
                    expires_at=expires_at,
                    heartbeat_at=now,
                )
                db.add(job)
                await db.flush()
                self._add_audit(
                    db,
                    admin_id=admin_id,
                    action="import.create",
                    object_type="import_job",
                    object_id=job.id,
                    changed_fields={
                        "source_type": {"before": None, "after": source_type},
                        "library_id": {"before": None, "after": library_id},
                        "source_size_bytes": {"before": None, "after": len(content)},
                        "status": {"before": None, "after": "queued"},
                    },
                    permission="quiz:import",
                )
                await db.commit()
                persisted = True
                await db.refresh(job)
                return job
        except IntegrityError as exc:
            raise ValidationException("导入任务创建失败，请重试") from exc
        except Exception:
            # The source object is uploaded before the DB row so the worker can
            # consume it asynchronously. If row creation fails for any reason
            # (not only a uniqueness violation), remove the orphan immediately;
            # the seven-day cleanup is a safety net, not the primary rollback.
            raise
        finally:
            if not persisted:
                try:
                    await self._delete_import_object(source_key)
                except Exception:
                    # Preserve the original database/storage exception. The
                    # cleanup worker will retry object deletion when metadata
                    # exists; a row-less orphan is also safe to remove by the
                    # provider's lifecycle policy.
                    pass

    async def list_import_jobs(
        self,
        query: AdminQuizImportJobQuery | None = None,
        *,
        admin_id: int | None = None,
    ) -> PaginatedData[AdminQuizImportJobResponse]:
        query = query or AdminQuizImportJobQuery()
        async with get_db_ctx() as db:
            stmt = select(QuizImportJob)
            if query.status is not None:
                stmt = stmt.where(QuizImportJob.status == self._enum_value(query.status))
            if query.source_type is not None:
                stmt = stmt.where(QuizImportJob.source_type == self._enum_value(query.source_type))
            total = (await db.execute(
                select(func.count()).select_from(stmt.subquery())
            )).scalar() or 0
            result = await db.execute(
                stmt.order_by(QuizImportJob.created_at.desc(), QuizImportJob.id.desc())
                .offset((query.page - 1) * query.page_size)
                .limit(query.page_size)
            )
            rows = list(result.scalars().all())
        return PaginatedData[AdminQuizImportJobResponse](
            items=[AdminQuizImportJobResponse.model_validate(row) for row in rows],
            total=total,
            page=query.page,
            page_size=query.page_size,
        )

    async def get_import_job(
        self,
        job_id: int,
        *,
        admin_id: int | None = None,
    ) -> QuizImportJob:
        async with get_db_ctx() as db:
            job = await db.get(QuizImportJob, job_id)
            if job is None:
                raise NotFoundException("导入任务")
            if admin_id is not None:
                self._add_audit(
                    db,
                    admin_id=admin_id,
                    action="import.detail_view",
                    object_type="import_job",
                    object_id=job.id,
                    changed_fields={
                        "status": {"before": None, "after": job.status}
                    },
                    permission="quiz:list",
                )
                await db.commit()
            return job

    async def list_import_errors(
        self,
        job_id: int,
        query: AdminQuizImportErrorQuery,
        *,
        admin_id: int,
    ) -> AdminQuizImportErrorPage:
        async with get_db_ctx() as db:
            job = await db.get(QuizImportJob, job_id)
            if job is None:
                raise NotFoundException("导入任务")
            expires_at = self._aware(job.expires_at)
            if expires_at <= datetime.now(timezone.utc):
                self._add_audit(
                    db,
                    admin_id=admin_id,
                    action="import.errors_view",
                    object_type="import_job",
                    object_id=job.id,
                    result="failed",
                    error_summary="导入错误明细已过期",
                    permission="quiz:list",
                )
                await db.commit()
                raise BusinessException("导入错误明细已过期")

            base = select(QuizImportError).where(
                QuizImportError.job_id == job.id,
                QuizImportError.validation_version == job.validation_version,
            )
            if query.field is not None:
                base = base.where(QuizImportError.field == query.field)
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
                        base.order_by(QuizImportError.id.asc())
                        .offset((query.page - 1) * 50)
                        .limit(50)
                    )
                ).scalars().all()
            )
            available_fields = list(
                (
                    await db.execute(
                        select(QuizImportError.field)
                        .where(
                            QuizImportError.job_id == job.id,
                            QuizImportError.validation_version
                            == job.validation_version,
                            QuizImportError.field.is_not(None),
                        )
                        .distinct()
                        .order_by(QuizImportError.field.asc())
                    )
                ).scalars().all()
            )
            self._add_audit(
                db,
                admin_id=admin_id,
                action="import.errors_view",
                object_type="import_job",
                object_id=job.id,
                changed_fields={
                    "page": {"before": None, "after": query.page},
                    "field_filter": {"before": None, "after": query.field},
                    "returned_count": {"before": None, "after": len(rows)},
                },
                permission="quiz:list",
            )
            await db.commit()
        return AdminQuizImportErrorPage(
            items=[
                {
                    "row": row.row,
                    "question_index": row.question_index,
                    "field": row.field,
                    "error_code": row.error_code,
                    "message": row.message,
                }
                for row in rows
            ],
            total=total,
            page=query.page,
            page_size=50,
            available_fields=available_fields,
            validation_version=job.validation_version,
        )

    async def get_import_category_impact(
        self,
        job_id: int,
        *,
        admin_id: int,
    ) -> AdminQuizImportCategoryImpactResponse:
        async with get_db_ctx() as db:
            job = await db.get(QuizImportJob, job_id)
            if job is None:
                raise NotFoundException("导入任务")
            if job.status != "awaiting_category_confirmation" or not job.category_impact:
                self._add_audit(
                    db,
                    admin_id=admin_id,
                    action="import.category_impact_view",
                    object_type="import_job",
                    object_id=job.id,
                    result="failed",
                    error_summary="导入任务不在等待分类确认状态",
                    permission="quiz:list",
                )
                await db.commit()
                raise BusinessException("导入任务不在等待分类确认状态")
            impact = AdminQuizImportCategoryImpactResponse.model_validate(
                {
                    **job.category_impact,
                    "job_id": job.id,
                    "status": job.status,
                    "lock_version": job.lock_version,
                    "impact_version": job.impact_version,
                }
            )
            self._add_audit(
                db,
                admin_id=admin_id,
                action="import.category_impact_view",
                object_type="import_job",
                object_id=job.id,
                changed_fields={
                    "new_category_count": {
                        "before": None,
                        "after": impact.new_category_count,
                    },
                    "affected_question_count": {
                        "before": None,
                        "after": impact.affected_question_count,
                    },
                },
                permission="quiz:list",
            )
            await db.commit()
            return impact

    async def confirm_import_categories(
        self,
        job_id: int,
        data: AdminQuizImportConfirmCategoriesRequest,
        *,
        admin_id: int,
    ) -> QuizImportJob:
        now = datetime.now(timezone.utc)
        async with get_db_ctx() as db:
            job = await self._get_for_update(db, QuizImportJob, job_id)
            if job is None:
                raise NotFoundException("导入任务")
            if job.status != "awaiting_category_confirmation":
                raise ConflictException("导入任务已被确认、取消或过期")
            if job.lock_version != data.lock_version:
                raise ConflictException(self._VERSION_CONFLICT_MESSAGE)
            if not job.impact_version or not hmac.compare_digest(
                job.impact_version, data.impact_version
            ):
                raise ConflictException("分类影响已变化，请刷新后重新确认")
            impact = AdminQuizImportCategoryImpactResponse.model_validate(
                {
                    **(job.category_impact or {}),
                    "job_id": job.id,
                    "status": job.status,
                    "lock_version": job.lock_version,
                    "impact_version": job.impact_version,
                }
            )
            if impact.blocking_reasons:
                raise BusinessException("分类影响存在阻断原因，不能确认")
            before_version = job.lock_version
            # Requeue for the standalone Worker. The claim transaction changes
            # this to ``importing``; the HTTP request never performs the 5,000
            # row write itself.
            job.status = "queued"
            job.confirmed_by = admin_id
            job.confirmed_at = now
            job.execution_protected_until = now + timedelta(minutes=30)
            job.heartbeat_at = now
            job.finished_at = None
            job.lock_version += 1
            self._add_audit(
                db,
                admin_id=admin_id,
                action="import.categories_confirm",
                object_type="import_job",
                object_id=job.id,
                changed_fields={
                    "status": {
                        "before": "awaiting_category_confirmation",
                        "after": "queued",
                    },
                    "lock_version": {
                        "before": before_version,
                        "after": job.lock_version,
                    },
                    "new_category_count": {
                        "before": None,
                        "after": impact.new_category_count,
                    },
                    "affected_question_count": {
                        "before": None,
                        "after": impact.affected_question_count,
                    },
                },
                permission="quiz:write",
            )
            await db.commit()
            await db.refresh(job)
            return job

    async def cancel_import_job(
        self,
        job_id: int,
        data: AdminQuizImportCancelRequest,
        *,
        admin_id: int,
    ) -> QuizImportJob:
        now = datetime.now(timezone.utc)
        async with get_db_ctx() as db:
            job = await self._get_for_update(db, QuizImportJob, job_id)
            if job is None:
                raise NotFoundException("导入任务")
            if job.status != "awaiting_category_confirmation":
                raise ConflictException("导入任务已被确认、取消或过期")
            if job.lock_version != data.lock_version:
                raise ConflictException(self._VERSION_CONFLICT_MESSAGE)
            before_version = job.lock_version
            job.status = "cancelled"
            job.finished_at = now
            job.heartbeat_at = now
            job.expires_at = self._terminal_import_expiry(now)
            job.lock_version += 1
            self._add_audit(
                db,
                admin_id=admin_id,
                action="import.cancel",
                object_type="import_job",
                object_id=job.id,
                changed_fields={
                    "status": {
                        "before": "awaiting_category_confirmation",
                        "after": "cancelled",
                    },
                    "lock_version": {
                        "before": before_version,
                        "after": job.lock_version,
                    },
                },
                permission="quiz:import",
            )
            await db.commit()
            await db.refresh(job)
            return job

    async def get_import_report_url(
        self,
        job_id: int,
        *,
        admin_id: int,
    ) -> AdminQuizSignedUrlResponse:
        job = await self.get_import_job(
            job_id,
            admin_id=admin_id,
        )
        now = datetime.now(timezone.utc)
        if job.expires_at <= now:
            await self._audit_import_report_access(
                job_id=job.id,
                admin_id=admin_id,
                action="import.report_download_url",
                result="failed",
                error_summary="错误报告已过期",
            )
            raise BusinessException("错误报告已过期")
        if not job.report_object_key or job.error_count == 0:
            await self._audit_import_report_access(
                job_id=job.id,
                admin_id=admin_id,
                action="import.report_download_url",
                result="failed",
                error_summary="该导入任务没有错误报告",
            )
            raise BusinessException("该导入任务没有错误报告")
        expires_at = min(
            job.expires_at,
            now + timedelta(seconds=settings.QUIZ_OSS_SIGNED_URL_TTL_SECONDS),
        )
        try:
            url = await self._signed_import_url(
                job,
                expires_at=expires_at,
                accessor_admin_id=admin_id,
            )
        except Exception as exc:
            await self._audit_import_report_access(
                job_id=job.id,
                admin_id=admin_id,
                action="import.report_download_url",
                result="failed",
                error_summary="错误报告地址生成失败",
            )
            raise exc
        await self._audit_import_report_access(
            job_id=job.id,
            admin_id=admin_id,
            action="import.report_download_url",
            result="succeeded",
        )
        return AdminQuizSignedUrlResponse(url=url, expires_at=expires_at)

    async def get_import_source_url(
        self,
        job_id: int,
        *,
        admin_id: int,
    ) -> AdminQuizSignedUrlResponse:
        job = await self.get_import_job(
            job_id,
            admin_id=admin_id,
        )
        now = datetime.now(timezone.utc)
        if job.expires_at <= now:
            await self._audit_import_access(
                job_id=job.id,
                admin_id=admin_id,
                action="import.source_download_url",
                result="failed",
                error_summary="导入源文件已过期",
            )
            raise BusinessException("导入源文件已过期")
        expires_at = min(
            job.expires_at,
            now + timedelta(seconds=settings.QUIZ_OSS_SIGNED_URL_TTL_SECONDS),
        )
        try:
            url = await self._signed_import_url(
                job,
                expires_at=expires_at,
                object_kind="source",
                accessor_admin_id=admin_id,
            )
        except Exception:
            await self._audit_import_access(
                job_id=job.id,
                admin_id=admin_id,
                action="import.source_download_url",
                result="failed",
                error_summary="导入源文件地址生成失败",
            )
            raise
        await self._audit_import_access(
            job_id=job.id,
            admin_id=admin_id,
            action="import.source_download_url",
        )
        return AdminQuizSignedUrlResponse(url=url, expires_at=expires_at)

    async def _audit_import_access(
        self,
        *,
        job_id: int,
        admin_id: int | None,
        action: str,
        result: str = "succeeded",
        error_summary: str | None = None,
    ) -> None:
        async with get_db_ctx() as db:
            self._add_audit(
                db,
                admin_id=admin_id,
                action=action,
                object_type="import_job",
                object_id=job_id,
                result=result,
                changed_fields=(
                    {"download_access": {"before": None, "after": result}}
                    if result == "succeeded"
                    else None
                ),
                error_summary=error_summary,
                permission="quiz:list",
            )
            await db.commit()

    async def _audit_import_report_access(self, **kwargs) -> None:
        """Backward-compatible internal alias used by report download paths."""

        await self._audit_import_access(**kwargs)

    async def _read_local_signed_import_object(
        self,
        job_id: int,
        *,
        object_kind: str,
        expires: int,
        admin_id: int,
        token: str,
    ) -> bytes:
        if expires < int(datetime.now(timezone.utc).timestamp()):
            raise ValidationException("题库导入文件链接已过期")
        payload = f"{job_id}:{object_kind}:{expires}:{admin_id}"
        expected = hmac.new(
            settings.JWT_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, token):
            raise ValidationException("题库导入文件链接无效")
        job = await self.get_import_job(
            job_id,
            admin_id=admin_id,
        )
        now = datetime.now(timezone.utc)
        if job.expires_at <= now:
            await self._audit_import_access(
                job_id=job.id,
                admin_id=admin_id,
                action=f"import.{object_kind}_download",
                result="failed",
                error_summary="题库导入文件已过期",
            )
            raise BusinessException("题库导入文件已过期")
        object_key = (
            job.source_object_key if object_kind == "source" else job.report_object_key
        )
        if not object_key:
            raise NotFoundException("导入源文件" if object_kind == "source" else "错误报告")
        raw = await self._get_import_object(object_key)
        await self._audit_import_access(
            job_id=job.id,
            admin_id=admin_id,
            action=f"import.{object_kind}_download",
        )
        return raw

    async def read_import_report(
        self, job_id: int, *, expires: int, admin_id: int, token: str
    ) -> AdminQuizImportReportResponse:
        raw = await self._read_local_signed_import_object(
            job_id,
            object_kind="report",
            expires=expires,
            admin_id=admin_id,
            token=token,
        )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BusinessException("错误报告内容损坏") from exc
        try:
            return AdminQuizImportReportResponse.model_validate(payload)
        except PydanticValidationError as exc:
            raise BusinessException("错误报告内容损坏") from exc

    async def read_import_source(
        self, job_id: int, *, expires: int, admin_id: int, token: str
    ) -> LocalImportDownload:
        job = await self.get_import_job(
            job_id,
            admin_id=admin_id,
        )
        raw = await self._read_local_signed_import_object(
            job_id,
            object_kind="source",
            expires=expires,
            admin_id=admin_id,
            token=token,
        )
        if job.source_type == "csv":
            return LocalImportDownload(
                data=raw,
                media_type="text/csv; charset=utf-8",
                extension="csv",
            )
        return LocalImportDownload(
            data=raw,
            media_type="application/json; charset=utf-8",
            extension="json",
        )

    async def retry_import_job(self, job_id: int, *, admin_id: int) -> QuizImportJob:
        """Manually requeue only a terminal infrastructure failure."""

        async with get_db_ctx() as db:
            job = await self._get_for_update(db, QuizImportJob, job_id)
            if job is None:
                raise NotFoundException("导入任务")
            if job.status != "failed":
                self._add_audit(
                    db,
                    admin_id=admin_id,
                    action="import.manual_retry",
                    object_type="import_job",
                    object_id=job.id,
                    result="failed",
                    error_summary="仅 failed 导入任务允许人工重试",
                    permission="quiz:import",
                )
                await db.commit()
                raise BusinessException("仅 failed 导入任务允许人工重试")
            if (job.retry_count or 0) >= settings.QUIZ_WORKER_MAX_RETRIES:
                self._add_audit(
                    db,
                    admin_id=admin_id,
                    action="import.manual_retry",
                    object_type="import_job",
                    object_id=job.id,
                    result="failed",
                    error_summary="导入任务重试次数已耗尽",
                    permission="quiz:import",
                )
                await db.commit()
                raise BusinessException("导入任务重试次数已耗尽")
            expires_at = job.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= datetime.now(timezone.utc):
                self._add_audit(
                    db,
                    admin_id=admin_id,
                    action="import.manual_retry",
                    object_type="import_job",
                    object_id=job.id,
                    result="failed",
                    error_summary="导入源文件已过期",
                    permission="quiz:import",
                )
                await db.commit()
                raise BusinessException("导入源文件已过期，无法重试")
            before_status = job.status
            job.status = "queued"
            job.error_message = None
            job.started_at = None
            job.finished_at = None
            job.heartbeat_at = datetime.now(timezone.utc)
            self._add_audit(
                db,
                admin_id=admin_id,
                action="import.manual_retry",
                object_type="import_job",
                object_id=job.id,
                changed_fields={
                    "status": {"before": before_status, "after": "queued"},
                    "import_batch_key": {
                        "before": job.import_batch_key,
                        "after": job.import_batch_key,
                    },
                },
                permission="quiz:import",
            )
            await db.commit()
            await db.refresh(job)
            return job

    async def expire_awaiting_import_job(
        self,
        *,
        now: datetime | None = None,
        job_id: int | None = None,
    ) -> bool:
        cutoff = now or datetime.now(timezone.utc)
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=timezone.utc)
        waiting_before = cutoff - timedelta(days=settings.QUIZ_IMPORT_RETENTION_DAYS)
        async with get_db_ctx() as db:
            stmt = select(QuizImportJob).where(
                QuizImportJob.status == "awaiting_category_confirmation",
                QuizImportJob.updated_at <= waiting_before,
            )
            if job_id is not None:
                stmt = stmt.where(QuizImportJob.id == job_id)
            job = (
                await db.execute(
                    stmt.order_by(QuizImportJob.updated_at.asc(), QuizImportJob.id.asc())
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if job is None:
                return False
            job.status = "expired"
            job.finished_at = cutoff
            job.heartbeat_at = cutoff
            job.expires_at = cutoff
            job.lock_version += 1
            self._add_audit(
                db,
                admin_id=None,
                action="import.expire",
                object_type="import_job",
                object_id=job.id,
                changed_fields={
                    "status": {
                        "before": "awaiting_category_confirmation",
                        "after": "expired",
                    }
                },
                permission="quiz:import",
            )
            await db.commit()
            return True

    async def cleanup_expired_import_job(
        self,
        *,
        now: datetime | None = None,
        job_id: int | None = None,
    ) -> bool:
        """Delete expired source/report objects while retaining audit metadata."""
        cutoff = now or datetime.now(timezone.utc)
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=timezone.utc)
        if await self.expire_awaiting_import_job(now=cutoff, job_id=job_id):
            return True
        async with get_db_ctx() as db:
            already_cleaned = exists(
                select(1).where(
                    QuizAdminAuditLog.object_type == "import_job",
                    QuizAdminAuditLog.object_id == QuizImportJob.id,
                    QuizAdminAuditLog.action == "import.cleanup",
                    QuizAdminAuditLog.result == "succeeded",
                )
            )
            stmt = (
                select(QuizImportJob)
                .where(
                    QuizImportJob.expires_at <= cutoff,
                    QuizImportJob.status.in_(
                        (
                            "validation_failed",
                            "succeeded",
                            "failed",
                            "cancelled",
                            "expired",
                        )
                    ),
                    ~already_cleaned,
                )
            )
            if job_id is not None:
                stmt = stmt.where(QuizImportJob.id == job_id)
            stmt = (
                stmt.order_by(QuizImportJob.expires_at.asc(), QuizImportJob.id.asc())
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            job = (await db.execute(stmt)).scalar_one_or_none()
            if job is None:
                return False
            try:
                await self._delete_import_object(job.source_object_key)
                await self._delete_import_object(job.report_object_key)
            except Exception:
                job.retry_count = (job.retry_count or 0) + 1
                job.heartbeat_at = cutoff
                self._add_audit(
                    db,
                    admin_id=None,
                    action="import.cleanup",
                    object_type="import_job",
                    object_id=job.id,
                    result="failed",
                    changed_fields={
                        "retry_count": {
                            "before": job.retry_count - 1,
                            "after": job.retry_count,
                        }
                    },
                    error_summary="题库导入对象清理失败",
                    permission="quiz:import",
                )
                await db.commit()
                # Preserve the failed audit/retry counter, then let the task
                # registry observe this run as a failure. The shared loop will
                # retry the same expired job on a later poll.
                raise
            self._add_audit(
                db,
                admin_id=None,
                action="import.cleanup",
                object_type="import_job",
                object_id=job.id,
                changed_fields={
                    "source_object": {"before": "retained", "after": "deleted"},
                    "report_object": {
                        "before": "retained" if job.report_object_key else "none",
                        "after": "deleted" if job.report_object_key else "none",
                    },
                    "expires_at": {"before": job.expires_at, "after": cutoff},
                },
                permission="quiz:import",
            )
            await db.execute(
                delete(QuizImportError).where(QuizImportError.job_id == job.id)
            )
            await db.commit()
            return True

    @classmethod
    def _validation_errors(
        cls,
        exc: PydanticValidationError,
        *,
        source_type: str,
        locator: int,
    ) -> list[dict[str, object]]:
        errors: list[dict[str, object]] = []
        for item in exc.errors():
            location = [str(part) for part in item.get("loc", ())]
            context_error = (item.get("ctx") or {}).get("error")
            if isinstance(context_error, QuizRuleViolation):
                # Field validators already provide the public request field in
                # ``loc`` (for example ``category_path`` while the underlying
                # normalizer reports ``name``). Model-level validators have an
                # empty location, so retain the domain error's precise field
                # such as ``options`` or ``correct_answer`` there.
                field = ".".join(location) or context_error.field
                message = context_error.message
            else:
                field = ".".join(location) or None
                message = str(item.get("msg", "参数校验失败"))
            code = "schema_validation"
            if isinstance(context_error, QuizRuleViolation) and context_error.code:
                code = context_error.code
            errors.append(
                cls._import_error(
                    # Reports use a user-facing one-based row locator for both
                    # JSON question arrays and CSV files. question_index keeps
                    # the source-specific machine locator for compatibility.
                    row=locator,
                    question_index=locator - 1 if source_type == "csv" else locator,
                    field=field,
                    error_code=code,
                    message=message,
                )
            )
        return errors

    def _parse_import_rows(
        self, source_type: str, content: bytes
    ) -> tuple[list[tuple[int, AdminQuizImportQuestion]], list[dict[str, object]]]:
        """Parse every row without touching the database."""
        rows: list[tuple[int, AdminQuizImportQuestion]] = []
        errors: list[dict[str, object]] = []
        if source_type == "json":
            try:
                payload = json.loads(content.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                return [], [
                    self._import_error(
                        row=None,
                        question_index=None,
                        field=None,
                        error_code="invalid_json",
                        message=f"JSON 文件无效: {exc}",
                    )
                ]
            if not isinstance(payload, dict) or set(payload) != {"questions"}:
                return [], [
                    self._import_error(
                        row=None,
                        question_index=None,
                        field=None,
                        error_code="invalid_document",
                        message="JSON 顶层必须仅包含 questions 字段",
                    )
                ]
            raw_questions = payload.get("questions")
            if not isinstance(raw_questions, list):
                return [], [
                    self._import_error(
                        row=None,
                        question_index=None,
                        field="questions",
                        error_code="invalid_document",
                        message="questions 必须是数组",
                    )
                ]
            if not 1 <= len(raw_questions) <= settings.QUIZ_IMPORT_MAX_QUESTIONS:
                return [], [
                    self._import_error(
                        row=None,
                        question_index=None,
                        field="questions",
                        error_code="question_count_out_of_range",
                        message="题目数量必须在 1 至 5,000 之间",
                    )
                ]
            for row_no, raw in enumerate(raw_questions, start=1):
                try:
                    rows.append((row_no, AdminQuizImportQuestion.model_validate(raw)))
                except PydanticValidationError as exc:
                    errors.extend(
                        self._validation_errors(
                            exc, source_type="json", locator=row_no
                        )
                    )
            return rows, errors

        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            return [], [
                self._import_error(
                    row=None,
                    question_index=None,
                    field=None,
                    error_code="invalid_encoding",
                    message=f"CSV 必须使用 UTF-8 编码: {exc}",
                )
            ]
        reader = csv.DictReader(io.StringIO(text, newline=""))
        expected_headers = [
            "category_path",
            "question_type",
            "question_text",
            "options",
            "correct_answer",
            "explanation",
        ]
        extended_headers = expected_headers + [
            f"option_image_{letter}" for letter in "abcd"
        ]
        if reader.fieldnames not in (expected_headers, extended_headers):
            return [], [
                self._import_error(
                    row=None,
                    question_index=None,
                    field=None,
                    error_code="invalid_csv_header",
                    message="CSV 表头必须为固定六列或含选项图片列（option_image_a-d）的十列",
                )
            ]
        data_row_count = 0
        limit_error_added = False
        for row_no, raw in enumerate(reader, start=2):
            data_row_count += 1
            if data_row_count > settings.QUIZ_IMPORT_MAX_QUESTIONS:
                if not limit_error_added:
                    errors.append(
                        self._import_error(
                            row=row_no,
                            question_index=data_row_count,
                            field=None,
                            error_code="question_count_out_of_range",
                            message=(
                                "CSV 题目数量超过限制，单批最多 "
                                f"{settings.QUIZ_IMPORT_MAX_QUESTIONS} 道"
                            ),
                        )
                    )
                    limit_error_added = True
                continue
            try:
                path = json.loads((raw.get("category_path") or "").strip())
                options_text = (raw.get("options") or "").strip()
                options = json.loads(options_text) if options_text else None
                answer_text = (raw.get("correct_answer") or "").strip()
                type_text = (raw.get("question_type") or "").strip()
                normalized_type_text = QUESTION_TYPE_IMPORT_ALIASES.get(
                    type_text, type_text
                )
                answer: object
                if answer_text.startswith("["):
                    answer = json.loads(answer_text)
                elif normalized_type_text == "fill_blank" and answer_text:
                    parsed_groups: list[list[str]] = []
                    delimiter_conflict = False
                    for group in answer_text.split("|"):
                        candidates = group.split(";;")
                        if any(not candidate.strip() for candidate in candidates):
                            delimiter_conflict = True
                            break
                        parsed_groups.append(candidates)
                    if delimiter_conflict:
                        errors.append(
                            self._import_error(
                                row=row_no,
                                question_index=data_row_count,
                                field="correct_answer",
                                error_code="answer_delimiter_conflict",
                                message=(
                                    "填空题答案分隔符使用无效：空与空之间用 | 分隔、"
                                    "同一空的候选之间用 ;; 分隔且候选不能为空；"
                                    "答案文本本身包含分隔符时请改用 JSON 导入"
                                ),
                            )
                        )
                        continue
                    answer = parsed_groups
                else:
                    answer = answer_text
                option_image_urls = {
                    letter.upper(): url.strip()
                    for letter in "abcd"
                    if (url := (raw.get(f"option_image_{letter}") or "")).strip()
                }
                item = {
                    "category_path": path,
                    "question_type": type_text,
                    "question_text": raw.get("question_text") or "",
                    "options": options,
                    "correct_answer": answer,
                    "explanation": (raw.get("explanation") or "") or None,
                    "option_image_urls": option_image_urls,
                }
                rows.append((row_no, AdminQuizImportQuestion.model_validate(item)))
            except (ValueError, TypeError, json.JSONDecodeError, PydanticValidationError) as exc:
                if isinstance(exc, PydanticValidationError):
                    errors.extend(
                        self._validation_errors(
                            exc, source_type="csv", locator=row_no
                        )
                    )
                else:
                    errors.append(
                        self._import_error(
                            row=row_no,
                            question_index=data_row_count,
                            field=None,
                            error_code="invalid_csv_row",
                            message=f"CSV 行无效: {exc}",
                        )
                    )
        return rows, errors

    async def _validate_import_rows(
        self,
        db,
        rows: list[tuple[int, AdminQuizImportQuestion]],
        initial_errors: list[dict[str, object]],
        *,
        source_type: str,
        job_id: int,
        lock_version: int,
        lock_categories: bool = False,
    ) -> ImportValidationResult:
        job = await db.get(QuizImportJob, job_id)
        if job is not None and job.library_id is not None:
            return await self._validate_v2_import_rows(
                db,
                rows,
                initial_errors,
                source_type=source_type,
                job_id=job_id,
                lock_version=lock_version,
                library_id=int(job.library_id),
                lock_content=lock_categories,
            )
        valid: list[tuple[int, AdminQuizImportQuestion, int | tuple[str, ...]]] = []
        errors = list(initial_errors)
        missing_errors: list[dict[str, object]] = []

        category_stmt = select(QuizCategory).order_by(
            QuizCategory.depth.asc(), QuizCategory.id.asc()
        )
        if lock_categories:
            category_stmt = category_stmt.with_for_update()
        categories = list((await db.execute(category_stmt)).scalars().all())
        by_parent_name = {
            (item.parent_id, item.normalized_name): item for item in categories
        }
        category_by_prefix: dict[tuple[str, ...], QuizCategory] = {}
        missing_prefixes: set[tuple[str, ...]] = set()
        path_rows: list[tuple[tuple[str, ...], int]] = []
        seen: set[tuple[object, str]] = set()
        candidates: list[
            tuple[
                int,
                AdminQuizImportQuestion,
                int | tuple[str, ...],
                str,
            ]
        ] = []

        def location(locator: int) -> tuple[int | None, int]:
            if source_type == "csv":
                return locator, locator - 1
            return locator, locator

        for locator, item in rows:
            path_key = tuple(item.category_path)
            path_rows.append((path_key, locator))
            parent_id: int | None = None
            category_id: int | None = None
            missing = False
            blocked: QuizCategory | None = None
            for depth, part in enumerate(path_key, start=1):
                prefix = path_key[:depth]
                if missing:
                    missing_prefixes.add(prefix)
                    continue
                category = by_parent_name.get((parent_id, part))
                if category is None:
                    missing = True
                    missing_prefixes.add(prefix)
                    continue
                category_by_prefix[prefix] = category
                if category.status != QuizCategoryStatus.ACTIVE.value:
                    blocked = category
                    break
                parent_id = category.id
                category_id = category.id

            row_no, question_index = location(locator)
            if blocked is not None:
                errors.append(
                    self._import_error(
                        row=row_no,
                        question_index=question_index,
                        field="category_path",
                        error_code="category_disabled",
                        message=f"分类已停用: {blocked.name}",
                    )
                )
                continue

            target: int | tuple[str, ...]
            if missing:
                target = path_key
                first_missing = next(
                    (
                        path_key[index - 1]
                        for index in range(1, len(path_key) + 1)
                        if path_key[:index] in missing_prefixes
                        and path_key[:index] not in category_by_prefix
                    ),
                    path_key[-1],
                )
                missing_errors.append(
                    self._import_error(
                        row=row_no,
                        question_index=question_index,
                        field="category_path",
                        error_code="category_missing",
                        message=f"分类不存在: {first_missing}",
                    )
                )
            else:
                assert category_id is not None
                target = category_id

            try:
                normalized = normalize_question_payload(
                    question_type=item.question_type,
                    question_text=item.question_text,
                    options=item.options,
                    correct_answer=item.correct_answer,
                    explanation=item.explanation,
                    image_urls=item.image_urls,
                    option_image_urls=item.option_image_urls,
                    require_publishable=False,
                )
            except QuizRuleViolation as exc:
                errors.append(
                    self._import_error(
                        row=row_no,
                        question_index=question_index,
                        field=exc.field,
                        error_code=exc.code or "question_validation",
                        message=exc.message,
                    )
                )
                continue
            target_key: object = (
                ("category", target) if isinstance(target, int) else ("path", *target)
            )
            key = (target_key, normalized.question_text_hash)
            if key in seen:
                errors.append(
                    self._import_error(
                        row=row_no,
                        question_index=question_index,
                        field="question_text",
                        error_code="duplicate_question_in_batch",
                        message="本批次题干重复",
                    )
                )
                continue
            seen.add(key)
            candidates.append((locator, item, target, normalized.question_text_hash))

        too_many_missing_categories = len(missing_prefixes) > 500
        if too_many_missing_categories:
            errors.append(
                self._import_error(
                    row=None,
                    question_index=None,
                    field="category_path",
                    error_code="category_creation_limit_exceeded",
                    message="单个导入任务最多新建 500 个分类节点",
                )
            )

        # A 5,000-row fixture can contain 5,000 distinct category paths. Once
        # the hard 500-node limit is known to be violated, skip the remaining
        # duplicate queries; the task is already a content validation failure.
        if too_many_missing_categories:
            return ImportValidationResult(
                valid=[],
                errors=errors,
                missing_errors=missing_errors,
                impact=None,
            )

        # Resolve existing hashes once per category instead of issuing one
        # round-trip per row. This keeps the 5,000-row task bounded on the
        # PostgreSQL baseline while preserving row-level duplicate errors.
        hashes_by_category: dict[int, set[str]] = {}
        for _, _, target, question_hash in candidates:
            if isinstance(target, int):
                hashes_by_category.setdefault(target, set()).add(question_hash)
        existing_by_category: dict[int, set[str]] = {}
        for category_id, hashes in hashes_by_category.items():
            result = await db.execute(
                select(QuizQuestion.question_text_hash).where(
                    QuizQuestion.category_id == category_id,
                    QuizQuestion.question_text_hash.in_(hashes),
                )
            )
            existing_by_category[category_id] = set(result.scalars().all())
        for locator, item, target, question_hash in candidates:
            row_no, question_index = location(locator)
            if isinstance(target, int) and question_hash in existing_by_category.get(
                target, set()
            ):
                errors.append(
                    self._import_error(
                        row=row_no,
                        question_index=question_index,
                        field="question_text",
                        error_code="duplicate_question",
                        message="同一分类内规范化题干已存在",
                    )
                )
                continue
            valid.append((locator, item, target))

        impact = (
            self._build_import_category_impact(
                job_id=job_id,
                lock_version=lock_version,
                categories=categories,
                category_by_prefix=category_by_prefix,
                missing_prefixes=missing_prefixes,
                path_rows=path_rows,
            )
            # A confirmed run must also snapshot the all-existing tree. If
            # another administrator creates the formerly missing categories
            # between preview and execution, that is a material impact change
            # and requires a fresh explicit confirmation.
            if (missing_prefixes or lock_categories)
            and not too_many_missing_categories
            else None
        )
        return ImportValidationResult(
            valid=valid,
            errors=errors,
            missing_errors=missing_errors,
            impact=impact,
        )

    async def _validate_v2_import_rows(
        self,
        db,
        rows: list[tuple[int, AdminQuizImportQuestion]],
        initial_errors: list[dict[str, object]],
        *,
        source_type: str,
        job_id: int,
        lock_version: int,
        library_id: int,
        lock_content: bool,
    ) -> ImportValidationResult:
        """Validate a fixed two-level module/knowledge-point import."""

        errors = list(initial_errors)
        missing_errors: list[dict[str, object]] = []
        valid: list[tuple[int, AdminQuizImportQuestion, int | tuple[str, ...]]] = []

        library_stmt = select(QuizLibrary).where(QuizLibrary.id == library_id)
        if lock_content:
            library_stmt = library_stmt.with_for_update()
        library = (await db.execute(library_stmt)).scalar_one_or_none()
        if library is None or library.status in {"archived", "deleted"}:
            errors.append(
                self._import_error(
                    row=None,
                    question_index=None,
                    field="library_id",
                    error_code="library_unavailable",
                    message="目标题库不存在或已归档",
                )
            )
            return ImportValidationResult(
                valid=[], errors=errors, missing_errors=[], impact=None
            )

        module_stmt = select(QuizModule).where(QuizModule.library_id == library_id)
        point_stmt = select(QuizKnowledgePoint).where(
            QuizKnowledgePoint.library_id == library_id
        )
        if lock_content:
            module_stmt = module_stmt.with_for_update()
            point_stmt = point_stmt.with_for_update()
        modules = list((await db.execute(module_stmt)).scalars())
        points = list((await db.execute(point_stmt)).scalars())
        module_by_name = {
            item.normalized_name: item for item in modules if item.status != "deleted"
        }
        point_by_parent_name = {
            (int(item.module_id), item.normalized_name): item
            for item in points
            if item.status != "deleted"
        }
        entity_by_prefix: dict[tuple[str, ...], object] = {}
        missing_prefixes: set[tuple[str, ...]] = set()
        path_rows: list[tuple[tuple[str, ...], int]] = []
        candidates: list[
            tuple[int, AdminQuizImportQuestion, int | tuple[str, ...], str]
        ] = []
        seen_hashes: set[str] = set()

        def location(locator: int) -> tuple[int, int]:
            return (locator, locator - 1) if source_type == "csv" else (locator, locator)

        for locator, item in rows:
            row_no, question_index = location(locator)
            path = tuple(item.category_path)
            if len(path) != 2:
                errors.append(
                    self._import_error(
                        row=row_no,
                        question_index=question_index,
                        field="category_path",
                        error_code="invalid_v2_path",
                        message="V2 导入路径必须严格为模块、知识点两级",
                    )
                )
                continue
            path_rows.append((path, locator))
            module = module_by_name.get(path[0])
            point = None
            if module is None:
                missing_prefixes.update({path[:1], path})
                target: int | tuple[str, ...] = path
                first_missing = path[0]
            elif module.status != "active":
                errors.append(
                    self._import_error(
                        row=row_no,
                        question_index=question_index,
                        field="category_path",
                        error_code="module_disabled",
                        message=f"模块已停用: {module.name}",
                    )
                )
                continue
            else:
                entity_by_prefix[path[:1]] = module
                point = point_by_parent_name.get((int(module.id), path[1]))
                if point is None:
                    missing_prefixes.add(path)
                    target = path
                    first_missing = path[1]
                elif point.status != "active":
                    errors.append(
                        self._import_error(
                            row=row_no,
                            question_index=question_index,
                            field="category_path",
                            error_code="knowledge_point_disabled",
                            message=f"知识点已停用: {point.name}",
                        )
                    )
                    continue
                else:
                    entity_by_prefix[path] = point
                    target = int(point.id)
                    first_missing = ""
            if isinstance(target, tuple):
                missing_errors.append(
                    self._import_error(
                        row=row_no,
                        question_index=question_index,
                        field="category_path",
                        error_code="category_missing",
                        message=f"结构不存在: {first_missing}",
                    )
                )
            try:
                normalized = normalize_question_payload(
                    question_type=item.question_type,
                    question_text=item.question_text,
                    options=item.options,
                    correct_answer=item.correct_answer,
                    explanation=item.explanation,
                    image_urls=item.image_urls,
                    option_image_urls=item.option_image_urls,
                    require_publishable=False,
                )
            except QuizRuleViolation as exc:
                errors.append(
                    self._import_error(
                        row=row_no,
                        question_index=question_index,
                        field=exc.field,
                        error_code=exc.code or "question_validation",
                        message=exc.message,
                    )
                )
                continue
            if normalized.question_text_hash in seen_hashes:
                errors.append(
                    self._import_error(
                        row=row_no,
                        question_index=question_index,
                        field="question_text",
                        error_code="duplicate_question_in_batch",
                        message="同一题库内本批次题干重复",
                    )
                )
                continue
            seen_hashes.add(normalized.question_text_hash)
            candidates.append(
                (locator, item, target, normalized.question_text_hash)
            )

        if len(missing_prefixes) > 500:
            errors.append(
                self._import_error(
                    row=None,
                    question_index=None,
                    field="category_path",
                    error_code="category_creation_limit_exceeded",
                    message="单个导入任务最多新建 500 个模块或知识点",
                )
            )
            return ImportValidationResult(
                valid=[], errors=errors, missing_errors=missing_errors, impact=None
            )

        candidate_hashes = {item[3] for item in candidates}
        existing_hashes: set[str] = set()
        if candidate_hashes:
            existing_hashes = set(
                (
                    await db.execute(
                        select(QuizQuestion.question_text_hash).where(
                            QuizQuestion.library_id == library_id,
                            QuizQuestion.stem_reserved.is_(True),
                            QuizQuestion.question_text_hash.in_(candidate_hashes),
                        )
                    )
                ).scalars()
            )
        for locator, item, target, question_hash in candidates:
            row_no, question_index = location(locator)
            if question_hash in existing_hashes:
                errors.append(
                    self._import_error(
                        row=row_no,
                        question_index=question_index,
                        field="question_text",
                        error_code="duplicate_question",
                        message="同一题库内规范化题干已存在",
                    )
                )
                continue
            valid.append((locator, item, target))

        impact = (
            self._build_v2_import_impact(
                job_id=job_id,
                lock_version=lock_version,
                entity_by_prefix=entity_by_prefix,
                missing_prefixes=missing_prefixes,
                path_rows=path_rows,
            )
            if missing_prefixes or lock_content
            else None
        )
        return ImportValidationResult(
            valid=valid,
            errors=errors,
            missing_errors=missing_errors,
            impact=impact,
        )

    def _build_v2_import_impact(
        self,
        *,
        job_id: int,
        lock_version: int,
        entity_by_prefix: dict[tuple[str, ...], object],
        missing_prefixes: set[tuple[str, ...]],
        path_rows: list[tuple[tuple[str, ...], int]],
    ) -> AdminQuizImportCategoryImpactResponse:
        direct_counts: dict[tuple[str, ...], int] = {}
        subtree_counts: dict[tuple[str, ...], int] = {}
        prefixes: set[tuple[str, ...]] = set()
        for path, _locator in path_rows:
            direct_counts[path] = direct_counts.get(path, 0) + 1
            for depth in (1, 2):
                prefix = path[:depth]
                prefixes.add(prefix)
                subtree_counts[prefix] = subtree_counts.get(prefix, 0) + 1
        nodes: dict[tuple[str, ...], dict[str, object]] = {}
        for prefix in sorted(prefixes, key=lambda item: (len(item), item)):
            entity = entity_by_prefix.get(prefix)
            nodes[prefix] = {
                "name": prefix[-1],
                "path": list(prefix),
                "depth": len(prefix),
                "status": (
                    "will_create"
                    if prefix in missing_prefixes or entity is None
                    else "existing"
                ),
                "category_id": getattr(entity, "id", None),
                "direct_question_count": direct_counts.get(prefix, 0),
                "subtree_question_count": subtree_counts.get(prefix, 0),
                "blocking_reasons": [],
                "children": [],
            }
        roots: list[dict[str, object]] = []
        for prefix in sorted(nodes, key=lambda item: (len(item), item)):
            if len(prefix) == 1:
                roots.append(nodes[prefix])
            else:
                parent_children = nodes[prefix[:1]]["children"]
                assert isinstance(parent_children, list)
                parent_children.append(nodes[prefix])
        calculated_at = datetime.now(timezone.utc)
        new_count = sum(
            1 for node in nodes.values() if node["status"] == "will_create"
        )
        reused_count = len(nodes) - new_count
        payload = self._impact_snapshot_payload(
            tree=roots,
            new_category_count=new_count,
            reused_category_count=reused_count,
            affected_question_count=len(path_rows),
            blocking_reasons=[],
            calculated_at=calculated_at,
        )
        return AdminQuizImportCategoryImpactResponse(
            job_id=job_id,
            status="awaiting_category_confirmation",
            tree=roots,
            new_category_count=new_count,
            reused_category_count=reused_count,
            affected_question_count=len(path_rows),
            blocking_reasons=[],
            lock_version=lock_version,
            impact_version=self._impact_version(payload),
            calculated_at=calculated_at,
        )

    def _build_import_category_impact(
        self,
        *,
        job_id: int,
        lock_version: int,
        categories: list[QuizCategory],
        category_by_prefix: dict[tuple[str, ...], QuizCategory],
        missing_prefixes: set[tuple[str, ...]],
        path_rows: list[tuple[tuple[str, ...], int]],
    ) -> AdminQuizImportCategoryImpactResponse:
        direct_counts: dict[tuple[str, ...], int] = {}
        subtree_counts: dict[tuple[str, ...], int] = {}
        all_prefixes: set[tuple[str, ...]] = set()
        for path, _ in path_rows:
            direct_counts[path] = direct_counts.get(path, 0) + 1
            for depth in range(1, len(path) + 1):
                prefix = path[:depth]
                all_prefixes.add(prefix)
                subtree_counts[prefix] = subtree_counts.get(prefix, 0) + 1

        disabled_prefixes = {
            prefix
            for prefix, category in category_by_prefix.items()
            if category.status != QuizCategoryStatus.ACTIVE.value
        }
        blocking_reasons: list[str] = []
        nodes: dict[tuple[str, ...], dict[str, object]] = {}
        for prefix in sorted(all_prefixes, key=lambda item: (len(item), item)):
            category = category_by_prefix.get(prefix)
            blocked_ancestor = next(
                (
                    ancestor
                    for depth in range(1, len(prefix) + 1)
                    if (ancestor := prefix[:depth]) in disabled_prefixes
                ),
                None,
            )
            reasons: list[str] = []
            if blocked_ancestor is not None:
                disabled = category_by_prefix[blocked_ancestor]
                reasons.append(f"分类已停用: {disabled.name}")
                status = "blocked"
            elif prefix in missing_prefixes or category is None:
                status = "will_create"
            else:
                status = "existing"
            for reason in reasons:
                if reason not in blocking_reasons:
                    blocking_reasons.append(reason)
            nodes[prefix] = {
                "name": prefix[-1],
                "path": list(prefix),
                "depth": len(prefix),
                "status": status,
                "category_id": category.id if category is not None else None,
                "direct_question_count": direct_counts.get(prefix, 0),
                "subtree_question_count": subtree_counts.get(prefix, 0),
                "blocking_reasons": reasons,
                "children": [],
            }

        roots: list[dict[str, object]] = []
        for prefix in sorted(nodes, key=lambda item: (len(item), item)):
            node = nodes[prefix]
            if len(prefix) == 1:
                roots.append(node)
            else:
                children = nodes[prefix[:-1]]["children"]
                assert isinstance(children, list)
                children.append(node)

        calculated_at = datetime.now(timezone.utc)
        new_count = sum(
            1 for node in nodes.values() if node["status"] == "will_create"
        )
        reused_count = sum(
            1 for node in nodes.values() if node["status"] == "existing"
        )
        payload = self._impact_snapshot_payload(
            tree=roots,
            new_category_count=new_count,
            reused_category_count=reused_count,
            affected_question_count=len(path_rows),
            blocking_reasons=blocking_reasons,
            calculated_at=calculated_at,
        )
        impact_version = self._impact_version(payload)
        return AdminQuizImportCategoryImpactResponse(
            job_id=job_id,
            status="awaiting_category_confirmation",
            tree=roots,
            new_category_count=new_count,
            reused_category_count=reused_count,
            affected_question_count=len(path_rows),
            blocking_reasons=blocking_reasons,
            lock_version=lock_version,
            impact_version=impact_version,
            calculated_at=calculated_at,
        )

    @staticmethod
    def _import_total_rows(
        *,
        source_type: str,
        rows: list[tuple[int, AdminQuizImportQuestion]],
        errors: list[dict[str, object]],
    ) -> int:
        indexes = {locator - 1 if source_type == "csv" else locator for locator, _ in rows}
        indexes.update(
            int(item["question_index"])
            for item in errors
            if item.get("question_index") is not None
        )
        return min(settings.QUIZ_IMPORT_MAX_QUESTIONS, len(indexes))

    @staticmethod
    def _report_errors(errors: list[dict[str, object]]) -> list[dict[str, object]]:
        return [
            {
                key: value
                for key, value in item.items()
                if key in {"row", "question_index", "field", "error_code", "message"}
            }
            for item in errors
        ]

    async def _write_import_report(
        self,
        job: QuizImportJob,
        errors: list[dict[str, object]],
    ) -> str:
        report_key = self._import_object_key(job.import_batch_key, "report.json")
        report = json.dumps(
            {"job_id": job.id, "errors": self._report_errors(errors)},
            ensure_ascii=False,
        ).encode("utf-8")
        await self._put_import_object(report_key, report, "application/json")
        return report_key

    async def _create_import_questions(
        self,
        db,
        *,
        current: QuizImportJob,
        valid: list[tuple[int, AdminQuizImportQuestion, int | tuple[str, ...]]],
    ) -> None:
        assert current.admin_id is not None
        if current.library_id is not None:
            await self._create_v2_import_questions(db, current=current, valid=valid)
            return
        path_categories: dict[tuple[str, ...], int] = {}
        for _, item, target in valid:
            if not isinstance(target, int):
                raise ValidationException("导入分类尚未确认")
            normalized = normalize_question_payload(
                question_type=item.question_type,
                question_text=item.question_text,
                options=item.options,
                correct_answer=item.correct_answer,
                explanation=item.explanation,
                image_urls=item.image_urls,
                option_image_urls=item.option_image_urls,
                require_publishable=False,
            )
            db.add(
                QuizQuestion(
                    category_id=target,
                    question_type=normalized.question_type.value,
                    status=QuizQuestionStatus.DRAFT.value,
                    question_text=normalized.question_text,
                    normalized_question_text=normalized.normalized_question_text,
                    question_text_hash=normalized.question_text_hash,
                    options=normalized.options,
                    correct_answer=normalized.correct_answer,
                    explanation=normalized.explanation,
                    image_urls=normalized.image_urls,
                    option_image_urls=normalized.option_image_urls,
                    ever_published=False,
                    lock_version=1,
                    created_by=current.admin_id,
                    updated_by=current.admin_id,
                )
            )
            path_categories[tuple(item.category_path)] = target
        for category_id in set(path_categories.values()):
            category = await db.get(QuizCategory, category_id)
            if category is not None:
                category.ever_had_question = True

    async def _create_v2_import_questions(
        self,
        db,
        *,
        current: QuizImportJob,
        valid: list[tuple[int, AdminQuizImportQuestion, int | tuple[str, ...]]],
    ) -> None:
        assert current.admin_id is not None and current.library_id is not None
        for _, item, target in valid:
            if not isinstance(target, int):
                raise ValidationException("导入模块和知识点尚未确认")
            point = await db.get(QuizKnowledgePoint, target)
            if (
                point is None
                or int(point.library_id) != int(current.library_id)
                or point.status != "active"
            ):
                raise ValidationException("导入目标知识点不可用")
            normalized = normalize_question_payload(
                question_type=item.question_type,
                question_text=item.question_text,
                options=item.options,
                correct_answer=item.correct_answer,
                explanation=item.explanation,
                image_urls=item.image_urls,
                option_image_urls=item.option_image_urls,
                require_publishable=False,
            )
            question = QuizQuestion(
                library_id=current.library_id,
                knowledge_point_id=target,
                category_id=None,
                question_type=normalized.question_type.value,
                status="draft",
                question_text=normalized.question_text,
                normalized_question_text=normalized.normalized_question_text,
                question_text_hash=normalized.question_text_hash,
                options=normalized.options,
                correct_answer=normalized.correct_answer,
                explanation=normalized.explanation,
                image_urls=normalized.image_urls,
                option_image_urls=normalized.option_image_urls,
                ever_published=False,
                stem_reserved=True,
                lock_version=1,
                created_by=current.admin_id,
                updated_by=current.admin_id,
            )
            db.add(question)
            await db.flush()
            revision = QuizQuestionRevision(
                question_id=question.id,
                revision_no=1,
                status="draft",
                question_type=normalized.question_type.value,
                question_text=normalized.question_text,
                normalized_question_text=normalized.normalized_question_text,
                question_text_hash=normalized.question_text_hash,
                options=normalized.options,
                correct_answer=normalized.correct_answer,
                explanation=normalized.explanation,
                image_urls=normalized.image_urls,
                option_image_urls=normalized.option_image_urls,
                created_by=current.admin_id,
            )
            db.add(revision)
            await db.flush()
            question.pending_revision_id = revision.id

    async def _create_confirmed_categories_and_questions(
        self,
        db,
        *,
        current: QuizImportJob,
        valid: list[tuple[int, AdminQuizImportQuestion, int | tuple[str, ...]]],
    ) -> list[tuple[int, AdminQuizImportQuestion, int]]:
        assert current.admin_id is not None
        if current.library_id is not None:
            return await self._create_confirmed_v2_content_and_questions(
                db, current=current, valid=valid
            )
        categories = list(
            (
                await db.execute(
                    select(QuizCategory)
                    .order_by(QuizCategory.depth.asc(), QuizCategory.id.asc())
                    .with_for_update()
                )
            ).scalars().all()
        )
        by_parent_name = {
            (category.parent_id, category.normalized_name): category
            for category in categories
        }
        resolved: dict[tuple[str, ...], QuizCategory] = {}
        requested_paths = sorted(
            {tuple(item.category_path) for _, item, _ in valid},
            key=lambda path: (len(path), path),
        )
        created_count = 0
        for path in requested_paths:
            parent_id: int | None = None
            for depth, name in enumerate(path, start=1):
                prefix = path[:depth]
                category = by_parent_name.get((parent_id, name))
                if category is None:
                    created_count += 1
                    if created_count > 500:
                        raise ValidationException("单个导入任务最多新建 500 个分类节点")
                    category = QuizCategory(
                        name=name,
                        normalized_name=name,
                        parent_id=parent_id,
                        depth=depth,
                        description=None,
                        status=QuizCategoryStatus.ACTIVE.value,
                        sort_order=0,
                        ever_had_question=False,
                        lock_version=1,
                        created_by=current.admin_id,
                        updated_by=current.admin_id,
                    )
                    db.add(category)
                    await db.flush()
                    by_parent_name[(parent_id, name)] = category
                if category.status != QuizCategoryStatus.ACTIVE.value:
                    raise ValidationException(f"分类已停用: {category.name}")
                resolved[prefix] = category
                parent_id = category.id

        final: list[tuple[int, AdminQuizImportQuestion, int]] = []
        for locator, item, _ in valid:
            category = resolved[tuple(item.category_path)]
            final.append((locator, item, category.id))
        await self._create_import_questions(db, current=current, valid=final)
        return final

    async def _create_confirmed_v2_content_and_questions(
        self,
        db,
        *,
        current: QuizImportJob,
        valid: list[tuple[int, AdminQuizImportQuestion, int | tuple[str, ...]]],
    ) -> list[tuple[int, AdminQuizImportQuestion, int]]:
        assert current.admin_id is not None and current.library_id is not None
        library = (
            await db.execute(
                select(QuizLibrary)
                .where(QuizLibrary.id == current.library_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if library is None or library.status in {"archived", "deleted"}:
            raise ValidationException("目标题库不可用")
        modules = list(
            (
                await db.execute(
                    select(QuizModule)
                    .where(QuizModule.library_id == current.library_id)
                    .with_for_update()
                )
            ).scalars()
        )
        points = list(
            (
                await db.execute(
                    select(QuizKnowledgePoint)
                    .where(QuizKnowledgePoint.library_id == current.library_id)
                    .with_for_update()
                )
            ).scalars()
        )
        module_by_name = {
            item.normalized_name: item for item in modules if item.status != "deleted"
        }
        point_by_parent_name = {
            (int(item.module_id), item.normalized_name): item
            for item in points
            if item.status != "deleted"
        }
        created_count = 0
        resolved: dict[tuple[str, ...], QuizKnowledgePoint] = {}
        for path in sorted(
            {tuple(item.category_path) for _, item, _ in valid}
        ):
            if len(path) != 2:
                raise ValidationException("V2 导入路径必须严格为模块、知识点两级")
            module = module_by_name.get(path[0])
            if module is None:
                created_count += 1
                module = QuizModule(
                    library_id=current.library_id,
                    name=path[0],
                    normalized_name=path[0],
                    status="active",
                    system_kind="none",
                    name_reserved=True,
                    sort_order=0,
                    lock_version=1,
                    created_by=current.admin_id,
                    updated_by=current.admin_id,
                )
                db.add(module)
                await db.flush()
                module_by_name[path[0]] = module
            if module.status != "active":
                raise ValidationException(f"模块已停用: {module.name}")
            point = point_by_parent_name.get((int(module.id), path[1]))
            if point is None:
                created_count += 1
                point = QuizKnowledgePoint(
                    library_id=current.library_id,
                    module_id=module.id,
                    name=path[1],
                    normalized_name=path[1],
                    status="active",
                    system_kind="none",
                    name_reserved=True,
                    sort_order=0,
                    lock_version=1,
                    created_by=current.admin_id,
                    updated_by=current.admin_id,
                )
                db.add(point)
                await db.flush()
                point_by_parent_name[(int(module.id), path[1])] = point
            if point.status != "active":
                raise ValidationException(f"知识点已停用: {point.name}")
            resolved[path] = point
        if created_count > 500:
            raise ValidationException("单个导入任务最多新建 500 个模块或知识点")
        final = [
            (locator, item, int(resolved[tuple(item.category_path)].id))
            for locator, item, _target in valid
        ]
        await self._create_v2_import_questions(db, current=current, valid=final)
        return final

    @staticmethod
    def _integrity_import_error(
        exc: IntegrityError,
        *,
        source_type: str,
    ) -> dict[str, object] | None:
        constraint = str(
            getattr(getattr(exc, "orig", None), "constraint_name", "") or ""
        )
        if not constraint:
            constraint = str(exc)
        if "uq_quiz_question_category_text_hash" in constraint:
            return AdminQuizService._import_error(
                row=None,
                question_index=None,
                field="question_text",
                error_code="duplicate_question",
                message="确认执行时发现同一分类内规范化题干已存在",
            )
        if (
            "uq_quiz_category_root_name" in constraint
            or "uq_quiz_category_sibling_name" in constraint
        ):
            return AdminQuizService._import_error(
                row=None,
                question_index=None,
                field="category_path",
                error_code="category_conflict",
                message="确认执行时分类树发生并发变化，请重新导入",
            )
        return None

    async def process_import_job(
        self,
        job_id: int,
        *,
        already_claimed: bool = False,
    ) -> bool:
        """Validate and atomically import one queued or confirmed job."""
        now = datetime.now(timezone.utc)
        stale_before = now - timedelta(seconds=settings.QUIZ_WORKER_STALE_SECONDS)
        async with get_db_ctx() as db:
            job = (
                await db.execute(
                    select(QuizImportJob)
                    .where(QuizImportJob.id == job_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            terminal = {
                "succeeded",
                "validation_failed",
                "failed",
                "awaiting_category_confirmation",
                "cancelled",
                "expired",
            }
            if job is None or job.status in terminal:
                return False
            before_status = job.status
            confirmed_run = (
                job.confirmed_at is not None and job.status in {"queued", "importing"}
            )
            if already_claimed:
                if job.status not in {"validating", "importing"}:
                    return False
            elif job.status in {"validating", "importing"}:
                heartbeat = job.heartbeat_at
                if heartbeat is not None and heartbeat.tzinfo is None:
                    heartbeat = heartbeat.replace(tzinfo=timezone.utc)
                protected_until = job.execution_protected_until
                if protected_until is not None and protected_until.tzinfo is None:
                    protected_until = protected_until.replace(tzinfo=timezone.utc)
                if protected_until is not None and protected_until > now:
                    return False
                if heartbeat is not None and heartbeat >= stale_before:
                    return False
                before_retry_count = job.retry_count or 0
                job.retry_count = before_retry_count + 1
                if job.retry_count >= settings.QUIZ_WORKER_MAX_RETRIES:
                    before_status = job.status
                    job.status = "failed"
                    job.error_message = "导入任务重试次数已耗尽"
                    job.finished_at = now
                    job.heartbeat_at = now
                    job.expires_at = self._terminal_import_expiry(now)
                    self._add_audit(
                        db,
                        admin_id=None,
                        action="import.retry_exhausted",
                        object_type="import_job",
                        object_id=job.id,
                        result="failed",
                        changed_fields={
                            "status": {"before": before_status, "after": "failed"},
                            "retry_count": {
                                "before": before_retry_count,
                                "after": job.retry_count,
                            },
                        },
                        error_summary=job.error_message,
                        permission="quiz:import",
                    )
                    await db.commit()
                    return True
            job.status = "importing" if confirmed_run else "validating"
            job.started_at = job.started_at or now
            job.heartbeat_at = now
            job.finished_at = None
            job.error_message = None
            self._add_audit(
                db,
                admin_id=None,
                action="import.claim",
                object_type="import_job",
                object_id=job.id,
                changed_fields={
                    "status": {"before": before_status, "after": job.status},
                },
                permission="quiz:import",
            )
            await db.commit()

        try:
            content = await self._get_import_object(job.source_object_key)
            rows, parse_errors = self._parse_import_rows(job.source_type, content)
            async with get_db_ctx() as db:
                current = (
                    await db.execute(
                        select(QuizImportJob)
                        .where(QuizImportJob.id == job_id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if current is None:
                    return False
                if confirmed_run:
                    if current.status != "importing" or current.confirmed_at is None:
                        return False
                elif current.status != "validating":
                    return False
                if current.admin_id is None:
                    raise ValidationException("导入任务所属管理员不存在")

                next_validation_version = current.validation_version + 1
                current.total_rows = self._import_total_rows(
                    source_type=current.source_type,
                    rows=rows,
                    errors=parse_errors,
                )
                current.heartbeat_at = datetime.now(timezone.utc)
                validation = await self._validate_import_rows(
                    db,
                    rows,
                    parse_errors,
                    source_type=current.source_type,
                    job_id=current.id,
                    lock_version=current.lock_version,
                    lock_categories=confirmed_run,
                )
                errors = validation.errors
                missing_errors = validation.missing_errors
                current.validation_version = next_validation_version
                current.validated_rows = len(validation.valid)

                # Missing categories are a confirmation state only when there
                # are no independent content/category blockers.
                if errors:
                    all_errors = [*errors, *missing_errors]
                    report_key = await self._write_import_report(current, all_errors)
                    current.report_object_key = report_key
                    current.status = "validation_failed"
                    current.error_count = len(all_errors)
                    current.error_message = f"共 {len(all_errors)} 项校验错误"
                    current.category_impact = None
                    current.impact_version = None
                    current.missing_category_count = 0
                    current.affected_question_count = 0
                    current.finished_at = datetime.now(timezone.utc)
                    current.heartbeat_at = current.finished_at
                    current.expires_at = self._terminal_import_expiry(
                        current.finished_at
                    )
                    self._add_audit(
                        db,
                        admin_id=current.admin_id,
                        action="import.validation_failed",
                        object_type="import_job",
                        object_id=current.id,
                        result="failed",
                        changed_fields={
                            "status": {
                                "before": "validating",
                                "after": "validation_failed",
                            },
                            "error_count": {
                                "before": 0,
                                "after": len(all_errors),
                            },
                            "report_object_key": {
                                "before": None,
                                "after": report_key,
                            },
                        },
                        error_summary=current.error_message,
                        permission="quiz:import",
                    )
                    await self._replace_import_errors(
                        db,
                        job_id=current.id,
                        validation_version=next_validation_version,
                        errors=all_errors,
                    )
                    await db.commit()
                    return True

                if missing_errors and not confirmed_run:
                    assert validation.impact is not None
                    impact = validation.impact
                    snapshot = impact.model_dump(mode="json")
                    current.status = "awaiting_category_confirmation"
                    current.error_count = 0
                    current.report_object_key = None
                    current.error_message = None
                    current.category_impact = snapshot
                    current.impact_version = impact.impact_version
                    current.missing_category_count = impact.new_category_count
                    current.affected_question_count = impact.affected_question_count
                    current.finished_at = None
                    current.lock_version += 1
                    snapshot["lock_version"] = current.lock_version
                    current.category_impact = snapshot
                    self._add_audit(
                        db,
                        admin_id=current.admin_id,
                        action="import.awaiting_category_confirmation",
                        object_type="import_job",
                        object_id=current.id,
                        changed_fields={
                            "status": {
                                "before": "validating",
                                "after": current.status,
                            },
                            "missing_category_count": {
                                "before": 0,
                                "after": current.missing_category_count,
                            },
                            "affected_question_count": {
                                "before": 0,
                                "after": current.affected_question_count,
                            },
                        },
                        permission="quiz:import",
                    )
                    await self._replace_import_errors(
                        db,
                        job_id=current.id,
                        validation_version=next_validation_version,
                        errors=[],
                    )
                    await db.commit()
                    return True

                if (
                    confirmed_run
                    and validation.impact is not None
                    and validation.impact.impact_version != current.impact_version
                ):
                    # The administrator confirmed an earlier impact but the
                    # category tree has since changed. Publish the refreshed
                    # snapshot and require an explicit second confirmation.
                    assert validation.impact is not None
                    impact = validation.impact
                    snapshot = impact.model_dump(mode="json")
                    current.status = "awaiting_category_confirmation"
                    current.confirmed_by = None
                    current.confirmed_at = None
                    current.execution_protected_until = None
                    current.category_impact = snapshot
                    current.impact_version = impact.impact_version
                    current.missing_category_count = impact.new_category_count
                    current.affected_question_count = impact.affected_question_count
                    current.lock_version += 1
                    snapshot["lock_version"] = current.lock_version
                    current.category_impact = snapshot
                    self._add_audit(
                        db,
                        admin_id=current.admin_id,
                        action="import.category_impact_changed",
                        object_type="import_job",
                        object_id=current.id,
                        changed_fields={
                            "status": {
                                "before": "importing",
                                "after": current.status,
                            }
                        },
                        permission="quiz:import",
                    )
                    await db.commit()
                    return True

                current.status = "importing"
                if confirmed_run:
                    final = await self._create_confirmed_categories_and_questions(
                        db,
                        current=current,
                        valid=validation.valid,
                    )
                else:
                    final = [
                        (locator, item, target)
                        for locator, item, target in validation.valid
                        if isinstance(target, int)
                    ]
                    await self._create_import_questions(
                        db,
                        current=current,
                        valid=final,
                    )
                current.created_count = len(final)
                current.status = "succeeded"
                current.error_count = 0
                current.report_object_key = None
                current.error_message = None
                current.finished_at = datetime.now(timezone.utc)
                current.heartbeat_at = current.finished_at
                current.expires_at = self._terminal_import_expiry(
                    current.finished_at
                )
                self._add_audit(
                    db,
                    admin_id=current.admin_id,
                    action="import.complete",
                    object_type="import_job",
                    object_id=current.id,
                    target_ids=[],
                    changed_fields={
                        "created_count": {"before": 0, "after": len(final)},
                        "status": {"before": "importing", "after": "succeeded"},
                    },
                    permission="quiz:import",
                )
                await self._replace_import_errors(
                    db,
                    job_id=current.id,
                    validation_version=next_validation_version,
                    errors=[],
                )
                await db.commit()
                return True
        except Exception as exc:
            # A failed import never leaves a partial question batch behind.
            # Infrastructure failures enter ``failed`` and wait for the
            # explicit admin retry endpoint. Validation failures are handled
            # in the earlier terminal branch. Manual retry retains the same
            # import_batch_key and the question transaction is all-or-nothing,
            # so a retry cannot duplicate a successful batch.
            validation_error = (
                self._integrity_import_error(exc, source_type=job.source_type)
                if isinstance(exc, IntegrityError)
                else None
            )
            async with get_db_ctx() as db:
                current = await db.get(QuizImportJob, job_id)
                if current is not None and current.status not in {
                    "succeeded",
                    "validation_failed",
                    "awaiting_category_confirmation",
                    "cancelled",
                    "expired",
                }:
                    now = datetime.now(timezone.utc)
                    before_status = current.status
                    if validation_error is not None:
                        now = datetime.now(timezone.utc)
                        next_validation_version = current.validation_version + 1
                        report_key = await self._write_import_report(
                            current, [validation_error]
                        )
                        current.validation_version = next_validation_version
                        current.status = "validation_failed"
                        current.created_count = 0
                        current.error_count = 1
                        current.report_object_key = report_key
                        current.error_message = "共 1 项校验错误"
                        current.finished_at = now
                        current.heartbeat_at = now
                        current.expires_at = self._terminal_import_expiry(now)
                        await self._replace_import_errors(
                            db,
                            job_id=current.id,
                            validation_version=next_validation_version,
                            errors=[validation_error],
                        )
                        self._add_audit(
                            db,
                            admin_id=current.admin_id,
                            action="import.validation_failed",
                            object_type="import_job",
                            object_id=current.id,
                            result="failed",
                            changed_fields={
                                "status": {
                                    "before": before_status,
                                    "after": "validation_failed",
                                },
                                "error_count": {"before": 0, "after": 1},
                            },
                            error_summary=current.error_message,
                            permission="quiz:import",
                        )
                        await db.commit()
                        return True
                    before_retry_count = current.retry_count or 0
                    current.retry_count = before_retry_count + 1
                    current.status = "failed"
                    current.error_message = "导入任务执行失败，可由管理员重试"
                    current.finished_at = now
                    current.heartbeat_at = now
                    current.expires_at = self._terminal_import_expiry(now)
                    self._add_audit(
                        db,
                        admin_id=current.admin_id,
                        action="import.failed",
                        object_type="import_job",
                        object_id=current.id,
                        result="failed",
                        changed_fields={
                            "status": {
                                "before": before_status,
                                "after": current.status,
                            },
                            "retry_count": {
                                "before": before_retry_count,
                                "after": current.retry_count,
                            },
                        },
                        error_summary="导入任务执行失败",
                        permission="quiz:import",
                    )
                    await db.commit()
            # Propagate the infrastructure exception so the task registry
            # records a failed run rather than a false success. A later manual
            # retry requeues the same row and batch key.
            raise

    async def process_next_import_job(self) -> bool:
        """Claim one queued/stale job; safe for multiple workers."""
        now = datetime.now(timezone.utc)
        stale_before = now - timedelta(seconds=settings.QUIZ_WORKER_STALE_SECONDS)
        async with get_db_ctx() as db:
            stmt = (
                select(QuizImportJob)
                .where(
                    or_(
                        QuizImportJob.status == "queued",
                        (
                            QuizImportJob.status.in_(
                                ("validating", "importing")
                            )
                            & (
                                QuizImportJob.execution_protected_until.is_(None)
                                | (QuizImportJob.execution_protected_until <= now)
                            )
                            & (
                                QuizImportJob.heartbeat_at.is_(None)
                                | (QuizImportJob.heartbeat_at < stale_before)
                            )
                        ),
                    )
                )
                .order_by(QuizImportJob.created_at.asc(), QuizImportJob.id.asc())
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            job = (await db.execute(stmt)).scalar_one_or_none()
            if job is None:
                return False
            if (job.retry_count or 0) >= settings.QUIZ_WORKER_MAX_RETRIES:
                before_status = job.status
                job.status = "failed"
                job.error_message = "导入任务重试次数已耗尽"
                job.finished_at = now
                job.expires_at = self._terminal_import_expiry(now)
                self._add_audit(
                    db,
                    admin_id=None,
                    action="import.retry_exhausted",
                    object_type="import_job",
                    object_id=job.id,
                    result="failed",
                    changed_fields={
                        "status": {"before": before_status, "after": "failed"},
                        "retry_count": {
                            "before": job.retry_count,
                            "after": job.retry_count,
                        },
                    },
                    error_summary=job.error_message,
                    permission="quiz:import",
                )
                await db.commit()
                return True
            was_stale = bool(
                job.status in {"validating", "importing"}
                and (
                    job.execution_protected_until is None
                    or self._aware(job.execution_protected_until) <= now
                )
                and (
                    job.heartbeat_at is None
                    or job.heartbeat_at < stale_before
                )
            )
            before_status = job.status
            before_retry_count = job.retry_count or 0
            job.retry_count = before_retry_count + (1 if was_stale else 0)
            job.heartbeat_at = now
            if job.retry_count >= settings.QUIZ_WORKER_MAX_RETRIES:
                job.status = "failed"
                job.error_message = "导入任务重试次数已耗尽"
                job.finished_at = now
                job.expires_at = self._terminal_import_expiry(now)
                self._add_audit(
                    db,
                    admin_id=None,
                    action="import.retry_exhausted",
                    object_type="import_job",
                    object_id=job.id,
                    result="failed",
                    changed_fields={
                        "status": {"before": before_status, "after": "failed"},
                        "retry_count": {
                            "before": before_retry_count,
                            "after": job.retry_count,
                        },
                    },
                    error_summary=job.error_message,
                    permission="quiz:import",
                )
                await db.commit()
                return True
            # Publish the claim before reading OSS/local storage. Other
            # workers only select queued or stale rows, so this closes the
            # previous window where two workers could process one queued job.
            job.status = "importing" if job.confirmed_at is not None else "validating"
            job.started_at = job.started_at or now
            job.finished_at = None
            job.error_message = None
            self._add_audit(
                db,
                admin_id=None,
                action="import.retry" if was_stale else "import.claim",
                object_type="import_job",
                object_id=job.id,
                changed_fields={
                    "status": {"before": before_status, "after": job.status},
                    "retry_count": {
                        "before": before_retry_count,
                        "after": job.retry_count,
                    },
                },
                permission="quiz:import",
            )
            await db.commit()
            job_id = job.id
        return await self.process_import_job(job_id, already_claimed=True)

    async def import_questions_csv(
        self,
        content: bytes,
        create_missing_categories: bool = False,
        *,
        admin_id: int | None = None,
    ) -> dict[str, Any]:
        if admin_id is None:
            raise ValidationException("导入任务缺少管理员身份")
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = content.decode("gbk")
            except UnicodeDecodeError:
                raise ValidationException("文件编码不支持，请使用 UTF-8 或 GBK 编码的 CSV 文件")

        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None:
            raise ValidationException("CSV 文件缺少表头")

        # Normalize headers (handle Chinese/English aliases)
        header_map = self._build_header_map(reader.fieldnames)
        required = ["question_type", "question_text", "correct_answer"]
        for field in required:
            if field not in header_map:
                raise ValidationException(f"CSV 缺少必要列: {field}")

        async with get_db_ctx() as db:
            created = 0
            skipped = 0
            errors: list[dict[str, Any]] = []

            for row_num, row in enumerate(reader, start=2):
                try:
                    category_path = row.get(header_map.get("category_path", ""), "").strip()
                    question_type = row.get(header_map["question_type"], "").strip()
                    question_text = row.get(header_map["question_text"], "").strip()
                    correct_answer = row.get(header_map["correct_answer"], "").strip()
                    explanation = row.get(header_map.get("explanation", ""), "").strip() or None
                    image_urls = row.get(header_map.get("image_urls", ""), "").strip()
                    option_image_urls = {
                        suffix.upper(): url.strip()
                        for suffix in ("a", "b", "c", "d")
                        if (url := row.get(header_map.get(f"option_image_{suffix}", ""), "")).strip()
                    }

                    if not question_type or not question_text or not correct_answer:
                        skipped += 1
                        continue

                    question_type = QUESTION_TYPE_IMPORT_ALIASES.get(
                        question_type, question_type
                    )
                    if question_type not in (
                        "single_choice",
                        "multiple_choice",
                        "judge",
                        "fill_blank",
                        "essay",
                    ):
                        errors.append({"row": row_num, "reason": f"无效题型: {question_type}"})
                        skipped += 1
                        continue
                    if question_type == "fill_blank" and not correct_answer.startswith("["):
                        correct_answer = [
                            group.split(";;") for group in correct_answer.split("|")
                        ]

                    # Parse options
                    options = None
                    options_col = header_map.get("options")
                    if options_col and row.get(options_col, "").strip():
                        try:
                            options = json.loads(row[options_col])
                        except (json.JSONDecodeError, ValueError):
                            options = {"raw": row[options_col]}
                    else:
                        opts = {}
                        for key_suffix in ("a", "b", "c", "d", "e", "f", "g", "h"):
                            col = header_map.get(f"option_{key_suffix}")
                            if col and row.get(col, "").strip():
                                opts[key_suffix.upper()] = row[col].strip()
                        if opts:
                            options = opts

                    category_id = await self._resolve_category(
                        db,
                        category_path,
                        create_missing_categories,
                        admin_id=admin_id,
                    )
                    normalized = self._normalize_admin_question(
                        question_type=question_type,
                        question_text=question_text,
                        options=options,
                        correct_answer=correct_answer,
                        explanation=explanation,
                        image_urls=image_urls,
                        option_image_urls=option_image_urls,
                        require_publishable=False,
                    )
                    if await self._question_text_taken(
                        db,
                        category_id=category_id,
                        question_text_hash=normalized.question_text_hash,
                    ):
                        raise ValidationException("同一分类内规范化题干不能重复")
                    question = QuizQuestion(
                        category_id=category_id,
                        question_type=normalized.question_type.value,
                        status=QuizQuestionStatus.DRAFT.value,
                        question_text=normalized.question_text,
                        normalized_question_text=normalized.normalized_question_text,
                        question_text_hash=normalized.question_text_hash,
                        options=normalized.options,
                        correct_answer=normalized.correct_answer,
                        explanation=normalized.explanation,
                        image_urls=normalized.image_urls,
                        option_image_urls=normalized.option_image_urls,
                        ever_published=False,
                        lock_version=1,
                        created_by=admin_id,
                        updated_by=admin_id,
                    )
                    db.add(question)
                    category = await db.get(QuizCategory, category_id)
                    if category is not None:
                        category.ever_had_question = True
                    created += 1
                except Exception as exc:
                    errors.append({"row": row_num, "reason": str(exc)})
                    skipped += 1

            # The legacy endpoint is retained only for source compatibility;
            # it still follows the frozen all-or-nothing import rule.  Any row
            # error rolls back both questions and category flags.
            if errors:
                await db.rollback()
                created = 0
            else:
                await db.commit()

        return {"created": created, "skipped": skipped, "errors": errors}

    async def import_questions_json(
        self,
        data: LegacyAdminQuizImportJsonRequest,
        *,
        admin_id: int | None = None,
    ) -> dict:
        if admin_id is None:
            raise ValidationException("导入任务缺少管理员身份")
        async with get_db_ctx() as db:
            category = await db.get(QuizCategory, data.category_id)
            if category is None:
                raise NotFoundException("题库分类")

            created = 0
            skipped = 0
            errors: list[dict] = []

            for idx, item in enumerate(data.questions):
                try:
                    if item.question_type not in (
                        "single_choice",
                        "multiple_choice",
                        "judge",
                        "fill_blank",
                        "essay",
                    ):
                        errors.append({"index": idx, "reason": f"无效题型: {item.question_type}"})
                        skipped += 1
                        continue

                    normalized = self._normalize_admin_question(
                        question_type=item.question_type,
                        question_text=item.question_text,
                        options=item.options,
                        correct_answer=item.correct_answer,
                        explanation=item.explanation,
                        image_urls=item.image_urls,
                        option_image_urls=item.option_image_urls,
                        require_publishable=False,
                    )
                    if await self._question_text_taken(
                        db,
                        category_id=data.category_id,
                        question_text_hash=normalized.question_text_hash,
                    ):
                        raise ValidationException("同一分类内规范化题干不能重复")
                    question = QuizQuestion(
                        category_id=data.category_id,
                        question_type=normalized.question_type.value,
                        status=QuizQuestionStatus.DRAFT.value,
                        question_text=normalized.question_text,
                        normalized_question_text=normalized.normalized_question_text,
                        question_text_hash=normalized.question_text_hash,
                        options=normalized.options,
                        correct_answer=normalized.correct_answer,
                        explanation=normalized.explanation,
                        image_urls=normalized.image_urls,
                        option_image_urls=normalized.option_image_urls,
                        ever_published=False,
                        lock_version=1,
                        created_by=admin_id,
                        updated_by=admin_id,
                    )
                    db.add(question)
                    category.ever_had_question = True
                    created += 1
                except Exception as exc:
                    errors.append({"index": idx, "reason": str(exc)})
                    skipped += 1

            if errors:
                await db.rollback()
                created = 0
            else:
                await db.commit()
            return {"created": created, "skipped": skipped, "errors": errors}

    async def _resolve_category(
        self,
        db,
        path: str,
        create_missing: bool,
        *,
        admin_id: int | None = None,
    ) -> int:
        """Resolve a category path like 'H3C/网络基础' to a category ID."""
        if not path:
            raise ValidationException("分类路径不能为空")

        raw_parts = path.replace("\\", "/").split("/")
        parts = [p.strip() for p in raw_parts]
        if not parts or any(not part for part in parts):
            raise ValidationException("分类路径必须是非空的斜杠分隔路径")
        if len(parts) > 3:
            raise ValidationException("题库分类最多支持三级")
        parent_id = None
        category_id = None

        for part in parts:
            normalized_part = normalize_category_name(part)
            stmt = select(QuizCategory).where(
                QuizCategory.normalized_name == normalized_part,
                QuizCategory.parent_id == parent_id,
            )
            category = (await db.execute(stmt)).scalar_one_or_none()
            if category is not None:
                if category.status != QuizCategoryStatus.ACTIVE.value:
                    raise ValidationException(f"分类已停用: {part}")
                category_id = category.id
            elif create_missing:
                # The frozen import contract requires categories to exist
                # before a batch is accepted. Keep the legacy flag from ever
                # constructing an incomplete ORM row (which used to violate
                # normalized_name/depth/admin FK constraints).
                raise ValidationException(f"分类不存在: {part}，请先由管理员创建分类")
            else:
                raise ValidationException(f"分类不存在: {part}")
            parent_id = category_id

        if category_id is None:
            raise ValidationException(f"分类路径无法解析: {path}")
        return category_id

    @staticmethod
    def _build_header_map(fieldnames: list[str]) -> dict[str, str]:
        aliases = {
            "category_path": ["category_path", "category", "category_name", "分类路径", "分类", "题目分类"],
            "question_type": ["question_type", "type", "题型"],
            "question_text": ["question_text", "question", "text", "题干", "题目"],
            "correct_answer": ["correct_answer", "answer", "正确答案", "答案"],
            "explanation": ["explanation", "解析"],
            "image_urls": ["image_urls", "images", "image", "题干图片", "图片", "图片URL"],
            "options": ["options", "选项"],
        }
        for letter in "abcdefgh":
            for template in (f"option_{letter}", f"{letter}", f"选项{letter.upper()}"):
                aliases.setdefault(f"option_{letter}", []).append(template)
        for letter in "abcd":
            aliases[f"option_image_{letter}"] = [
                f"option_image_{letter}",
                f"option_image_{letter.upper()}",
                f"选项图片{letter.upper()}",
                f"选项图{letter.upper()}",
            ]

        header_map: dict[str, str] = {}
        for field in fieldnames:
            field_lower = field.strip().lower()
            for target, candidates in aliases.items():
                if field_lower in candidates or field.strip() in candidates:
                    header_map[target] = field
                    break
        return header_map
