import asyncio
import csv
import hashlib
import hmac
import io
import json
import re
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import exists, func, or_, select, text, union
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
    QuizImportJob,
    QuizPracticeAttempt,
    QuizPracticeSessionQuestion,
    QuizQuestion,
    QuizQuestionStats,
)
from app.domain.community.src.rule.quiz import (
    QuizCategoryStatus,
    QuizQuestionStatus,
    QuizRuleViolation,
    normalize_category_name,
    normalize_question_payload,
)
from app.schemas.admin_quiz import (
    AdminQuizImportJsonRequest as LegacyAdminQuizImportJsonRequest,
)
from app.schemas.common import PaginatedData
from app.schemas.admin_quiz_contract import (
    AdminQuizBatchItemError,
    AdminQuizBatchRequest,
    AdminQuizCategoryCreate,
    AdminQuizCategoryUpdate,
    AdminQuizBatchResponse,
    AdminQuizCategoryStatusUpdate,
    AdminQuizCategoryQuery,
    AdminQuizQuestionCreate,
    AdminQuizQuestionUpdate,
    AdminQuizQuestionQuery,
    AdminQuizQuestionResponse,
    AdminQuizQuestionStatsResponse,
    AdminQuizVersionRequest,
    AdminQuizAuditLogResponse,
    AdminQuizAuditQuery,
    AdminQuizImportJobQuery,
    AdminQuizImportJobResponse,
    AdminQuizJsonImportRequest,
    AdminQuizSignedUrlResponse,
    AdminQuizImportQuestion,
)
from app.port.config import settings


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
            return {str(key): AdminQuizService._audit_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [AdminQuizService._audit_value(item) for item in value]
        return getattr(value, "value", value)

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
                error_summary=error_summary,
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

    # ── Question queries ──

    async def list_questions(
        self,
        *,
        category_id: int | None = None,
        question_type: str | None = None,
        status: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedData[AdminQuizQuestionResponse]:
        async with get_db_ctx() as db:
            base = select(QuizQuestion)
            if category_id is not None:
                base = base.where(QuizQuestion.category_id == category_id)
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
            # questions alive. Only a never-published draft can be physical
            # deleted; no legacy quiz_record lookup is valid after QB-00.
            # ``record_count`` was part of the removed legacy quiz_record
            # check. Historical答题记录 now live in immutable snapshot tables,
            # so the draft/ever_published lifecycle check is the only deletion
            # guard.
            if question.status != QuizQuestionStatus.DRAFT.value or question.ever_published:
                raise BusinessException("仅未发布草稿题目允许物理删除")
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
                    require_publishable=True,
                )
                question.options = normalized.options
                question.correct_answer = normalized.correct_answer
                question.explanation = normalized.explanation
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
                return AdminQuizBatchResponse(
                    succeeded=False,
                    updated_count=0,
                    errors=[
                        AdminQuizBatchItemError(
                            question_id=item.question_id,
                            code=40001,
                            field=None,
                            message="题目状态更新失败，批量操作未提交",
                        )
                        for item in data.items
                    ],
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

            await asyncio.to_thread(_write)
            return
        if settings.QUIZ_IMPORT_STORAGE_TYPE != "aliyun_oss":
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
            if not path.is_file():
                raise ValidationException("导入源文件不存在")
            return await asyncio.to_thread(path.read_bytes)
        if settings.QUIZ_IMPORT_STORAGE_TYPE != "aliyun_oss":
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
            if path.is_file():
                await asyncio.to_thread(path.unlink)
            return
        if settings.QUIZ_IMPORT_STORAGE_TYPE != "aliyun_oss":
            return

        def _delete() -> None:
            try:
                self._quiz_oss_bucket().delete_object(object_key)
            except Exception as exc:
                raise ThirdPartyException("阿里云 OSS 删除导入文件失败") from exc

        await asyncio.to_thread(_delete)

    async def _signed_import_url(
        self, job: QuizImportJob, *, expires_at: datetime
    ) -> str:
        if settings.QUIZ_IMPORT_STORAGE_TYPE == "aliyun_oss":
            expires = max(1, int((expires_at - datetime.now(timezone.utc)).total_seconds()))

            def _sign() -> str:
                try:
                    return self._quiz_oss_bucket().sign_url(
                        "GET", job.report_object_key, expires
                    )
                except ThirdPartyException:
                    raise
                except Exception as exc:
                    raise ThirdPartyException(
                        "阿里云 OSS 生成错误报告地址失败"
                    ) from exc

            return await asyncio.to_thread(_sign)

        # Development/local storage uses a short HMAC URL so a browser download
        # does not need to forward the admin's bearer token.
        expires_unix = int(expires_at.timestamp())
        payload = f"{job.id}:{expires_unix}:{job.admin_id or 0}"
        token = hmac.new(
            settings.JWT_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return (
            f"/admin/quiz/imports/{job.id}/report"
            f"?expires={expires_unix}&admin_id={job.admin_id or 0}&token={token}"
        )

    async def create_import_job(
        self,
        *,
        source_type: str,
        content: bytes,
        admin_id: int,
        filename: str,
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
        async with get_db_ctx() as db:
            job = QuizImportJob(
                admin_id=admin_id,
                import_batch_key=batch_key,
                source_type=source_type,
                status="queued",
                source_object_key=source_key,
                source_size_bytes=len(content),
                expires_at=expires_at,
                heartbeat_at=now,
            )
            db.add(job)
            try:
                await db.flush()
                self._add_audit(
                    db,
                    admin_id=admin_id,
                    action="import.create",
                    object_type="import_job",
                    object_id=job.id,
                    changed_fields={
                        "source_type": {"before": None, "after": source_type},
                        "source_size_bytes": {"before": None, "after": len(content)},
                        "status": {"before": None, "after": "queued"},
                    },
                    permission="quiz:import",
                )
                await db.commit()
            except IntegrityError as exc:
                await db.rollback()
                await self._delete_import_object(source_key)
                raise ValidationException("导入任务创建失败，请重试") from exc
            await db.refresh(job)
            return job

    async def list_import_jobs(
        self, query: AdminQuizImportJobQuery | None = None
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
        self, job_id: int, *, admin_id: int | None = None
    ) -> QuizImportJob:
        async with get_db_ctx() as db:
            job = await db.get(QuizImportJob, job_id)
            if job is None or (admin_id is not None and job.admin_id != admin_id):
                raise NotFoundException("导入任务")
            return job

    async def get_import_report_url(
        self, job_id: int, *, admin_id: int
    ) -> AdminQuizSignedUrlResponse:
        job = await self.get_import_job(job_id, admin_id=admin_id)
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
            url = await self._signed_import_url(job, expires_at=expires_at)
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

    async def _audit_import_report_access(
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
                    {"report_access": {"before": None, "after": result}}
                    if result == "succeeded"
                    else None
                ),
                error_summary=error_summary,
                permission="quiz:list",
            )
            await db.commit()

    async def read_import_report(
        self, job_id: int, *, expires: int, admin_id: int, token: str
    ) -> dict[str, object]:
        if expires < int(time.time()):
            raise ValidationException("错误报告链接已过期")
        payload = f"{job_id}:{expires}:{admin_id}"
        expected = hmac.new(
            settings.JWT_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, token):
            raise ValidationException("错误报告链接无效")
        job = await self.get_import_job(job_id, admin_id=admin_id)
        now = datetime.now(timezone.utc)
        if job.expires_at <= now:
            await self._audit_import_report_access(
                job_id=job.id,
                admin_id=admin_id,
                action="import.report_download",
                result="failed",
                error_summary="错误报告已过期",
            )
            raise BusinessException("错误报告已过期")
        if not job.report_object_key:
            raise NotFoundException("错误报告")
        raw = await self._get_import_object(job.report_object_key)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BusinessException("错误报告内容损坏") from exc
        await self._audit_import_report_access(
            job_id=job.id,
            admin_id=admin_id,
            action="import.report_download",
            result="succeeded",
        )
        return payload

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
                    QuizImportJob.status.in_(
                        ("validation_failed", "succeeded", "failed")
                    ),
                    QuizImportJob.expires_at <= cutoff,
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
            except Exception as exc:
                self._add_audit(
                    db,
                    admin_id=None,
                    action="import.cleanup",
                    object_type="import_job",
                    object_id=job.id,
                    result="failed",
                    error_summary="题库导入对象清理失败",
                    permission="quiz:import",
                )
                await db.commit()
                return False
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
            await db.commit()
            return True

    @staticmethod
    def _validation_errors(exc: PydanticValidationError, row: int) -> list[dict[str, object]]:
        errors: list[dict[str, object]] = []
        for item in exc.errors():
            location = [str(part) for part in item.get("loc", ())]
            errors.append(
                {
                    "row": row,
                    "field": ".".join(location) or None,
                    "message": str(item.get("msg", "参数校验失败")),
                }
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
                return [], [{"row": 1, "field": None, "message": f"JSON 文件无效: {exc}"}]
            if not isinstance(payload, dict) or set(payload) != {"questions"}:
                return [], [{"row": 1, "field": None, "message": "JSON 顶层必须仅包含 questions 字段"}]
            raw_questions = payload.get("questions")
            if not isinstance(raw_questions, list):
                return [], [{"row": 1, "field": "questions", "message": "questions 必须是数组"}]
            if not 1 <= len(raw_questions) <= settings.QUIZ_IMPORT_MAX_QUESTIONS:
                return [], [{"row": 1, "field": "questions", "message": "题目数量必须在 1 至 5,000 之间"}]
            for row_no, raw in enumerate(raw_questions, start=1):
                try:
                    rows.append((row_no, AdminQuizImportQuestion.model_validate(raw)))
                except PydanticValidationError as exc:
                    errors.extend(self._validation_errors(exc, row_no))
            return rows, errors

        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            return [], [{"row": 1, "field": None, "message": f"CSV 必须使用 UTF-8 编码: {exc}"}]
        reader = csv.DictReader(io.StringIO(text, newline=""))
        expected_headers = [
            "category_path",
            "question_type",
            "question_text",
            "options",
            "correct_answer",
            "explanation",
        ]
        if reader.fieldnames != expected_headers:
            return [], [{"row": 1, "field": None, "message": "CSV 表头必须严格为固定六列"}]
        data_row_count = 0
        limit_error_added = False
        for row_no, raw in enumerate(reader, start=2):
            data_row_count += 1
            if data_row_count > settings.QUIZ_IMPORT_MAX_QUESTIONS:
                if not limit_error_added:
                    errors.append(
                        {
                            "row": row_no,
                            "field": None,
                            "message": (
                                "CSV 题目数量超过限制，单批最多 "
                                f"{settings.QUIZ_IMPORT_MAX_QUESTIONS} 道"
                            ),
                        }
                    )
                    limit_error_added = True
                continue
            try:
                path = json.loads((raw.get("category_path") or "").strip())
                options_text = (raw.get("options") or "").strip()
                options = json.loads(options_text) if options_text else None
                answer_text = (raw.get("correct_answer") or "").strip()
                answer: object = json.loads(answer_text) if answer_text.startswith("[") else answer_text
                item = {
                    "category_path": path,
                    "question_type": (raw.get("question_type") or "").strip(),
                    "question_text": raw.get("question_text") or "",
                    "options": options,
                    "correct_answer": answer,
                    "explanation": (raw.get("explanation") or "") or None,
                }
                rows.append((row_no, AdminQuizImportQuestion.model_validate(item)))
            except (ValueError, TypeError, json.JSONDecodeError, PydanticValidationError) as exc:
                if isinstance(exc, PydanticValidationError):
                    errors.extend(self._validation_errors(exc, row_no))
                else:
                    errors.append({"row": row_no, "field": None, "message": f"CSV 行无效: {exc}"})
        return rows, errors

    async def _validate_import_rows(
        self,
        db,
        rows: list[tuple[int, AdminQuizImportQuestion]],
        initial_errors: list[dict[str, object]],
    ) -> tuple[list[tuple[int, AdminQuizImportQuestion, int]], list[dict[str, object]]]:
        valid: list[tuple[int, AdminQuizImportQuestion, int]] = []
        errors = list(initial_errors)
        category_cache: dict[tuple[str, ...], int | None] = {}
        category_error_cache: dict[tuple[str, ...], str] = {}
        seen: set[tuple[int, str]] = set()
        candidates: list[tuple[int, AdminQuizImportQuestion, int, str]] = []
        for row_no, item in rows:
            path_key = tuple(item.category_path)
            category_id = category_cache.get(path_key)
            if path_key not in category_cache:
                parent_id: int | None = None
                category_id = None
                for part in path_key:
                    category = (
                        await db.execute(
                            select(QuizCategory)
                            .where(
                                QuizCategory.parent_id == parent_id,
                                QuizCategory.normalized_name == part,
                            )
                            .limit(1)
                        )
                    ).scalar_one_or_none()
                    if category is None:
                        category_id = None
                        category_error_cache[path_key] = f"分类不存在: {part}"
                        break
                    if category.status != QuizCategoryStatus.ACTIVE.value:
                        category_id = None
                        category_error_cache[path_key] = f"分类已停用: {part}"
                        break
                    parent_id = category.id
                    category_id = category.id
                category_cache[path_key] = category_id
            if category_id is None:
                errors.append(
                    {
                        "row": row_no,
                        "field": "category_path",
                        "message": category_error_cache.get(path_key, "分类路径无效"),
                    }
                )
                continue

            try:
                normalized = normalize_question_payload(
                    question_type=item.question_type,
                    question_text=item.question_text,
                    options=item.options,
                    correct_answer=item.correct_answer,
                    explanation=item.explanation,
                    require_publishable=False,
                )
            except QuizRuleViolation as exc:
                errors.append(
                    {"row": row_no, "field": exc.field, "message": exc.message}
                )
                continue
            key = (category_id, normalized.question_text_hash)
            if key in seen:
                errors.append({"row": row_no, "field": "question_text", "message": "本批次题干重复"})
                continue
            seen.add(key)
            candidates.append((row_no, item, category_id, normalized.question_text_hash))

        # Resolve existing hashes once per category instead of issuing one
        # round-trip per row. This keeps the 5,000-row task bounded on the
        # PostgreSQL baseline while preserving row-level duplicate errors.
        hashes_by_category: dict[int, set[str]] = {}
        for _, _, category_id, question_hash in candidates:
            hashes_by_category.setdefault(category_id, set()).add(question_hash)
        existing_by_category: dict[int, set[str]] = {}
        for category_id, hashes in hashes_by_category.items():
            result = await db.execute(
                select(QuizQuestion.question_text_hash).where(
                    QuizQuestion.category_id == category_id,
                    QuizQuestion.question_text_hash.in_(hashes),
                )
            )
            existing_by_category[category_id] = set(result.scalars().all())
        for row_no, item, category_id, question_hash in candidates:
            if question_hash in existing_by_category.get(category_id, set()):
                errors.append(
                    {
                        "row": row_no,
                        "field": "question_text",
                        "message": "同一分类内规范化题干已存在",
                    }
                )
                continue
            valid.append((row_no, item, category_id))
        return valid, errors

    async def process_import_job(self, job_id: int) -> bool:
        """Validate and atomically import one queued job."""
        async with get_db_ctx() as db:
            job = await db.get(QuizImportJob, job_id)
            if job is None or job.status in {"succeeded", "validation_failed", "failed"}:
                return False
            job.status = "validating"
            job.started_at = job.started_at or datetime.now(timezone.utc)
            job.heartbeat_at = datetime.now(timezone.utc)
            await db.commit()

        try:
            content = await self._get_import_object(job.source_object_key)
            rows, parse_errors = self._parse_import_rows(job.source_type, content)
            async with get_db_ctx() as db:
                current = await db.get(QuizImportJob, job_id)
                if current is None:
                    return False
                current.total_rows = min(
                    settings.QUIZ_IMPORT_MAX_QUESTIONS,
                    len(rows)
                    + len(
                        {
                            int(error.get("row", 0))
                            for error in parse_errors
                            if int(error.get("row", 0)) <= settings.QUIZ_IMPORT_MAX_QUESTIONS + 1
                        }
                    ),
                )
                current.heartbeat_at = datetime.now(timezone.utc)
                valid, errors = await self._validate_import_rows(db, rows, parse_errors)
                current.validated_rows = len(valid)
                current.error_count = len(errors)
                if errors:
                    report_key = self._import_object_key(current.import_batch_key, "report.json")
                    report = json.dumps(
                        {"job_id": current.id, "errors": errors},
                        ensure_ascii=False,
                    ).encode("utf-8")
                    await self._put_import_object(report_key, report, "application/json")
                    current.report_object_key = report_key
                    current.status = "validation_failed"
                    current.error_message = f"共 {len(errors)} 项校验错误"
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
                                "after": len(errors),
                            },
                            "report_object_key": {
                                "before": None,
                                "after": report_key,
                            },
                        },
                        error_summary=current.error_message,
                        permission="quiz:import",
                    )
                    await db.commit()
                    return True

                current.status = "importing"
                current.heartbeat_at = datetime.now(timezone.utc)
                await db.commit()

            async with get_db_ctx() as db:
                current = await db.get(QuizImportJob, job_id)
                if current is None:
                    return False
                if current.admin_id is None:
                    raise ValidationException("导入任务所属管理员不存在")
                for _, item, category_id in valid:
                    normalized = normalize_question_payload(
                        question_type=item.question_type,
                        question_text=item.question_text,
                        options=item.options,
                        correct_answer=item.correct_answer,
                        explanation=item.explanation,
                        require_publishable=False,
                    )
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
                        ever_published=False,
                        lock_version=1,
                        created_by=current.admin_id,
                        updated_by=current.admin_id,
                    )
                    db.add(question)
                    category = await db.get(QuizCategory, category_id)
                    if category is not None:
                        category.ever_had_question = True
                current.created_count = len(valid)
                current.status = "succeeded"
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
                    changed_fields={"created_count": {"before": 0, "after": len(valid)}, "status": {"before": "importing", "after": "succeeded"}},
                    permission="quiz:import",
                )
                await db.commit()
                return True
        except Exception as exc:
            # A failed import never leaves a partial question batch behind.
            async with get_db_ctx() as db:
                current = await db.get(QuizImportJob, job_id)
                if current is not None and current.status not in {"succeeded", "validation_failed"}:
                    current.status = "failed"
                    current.error_message = "导入任务执行失败"
                    current.finished_at = datetime.now(timezone.utc)
                    current.heartbeat_at = current.finished_at
                    current.expires_at = self._terminal_import_expiry(
                        current.finished_at
                    )
                    current.retry_count = (current.retry_count or 0) + 1
                    self._add_audit(
                        db,
                        admin_id=current.admin_id,
                        action="import.failed",
                        object_type="import_job",
                        object_id=current.id,
                        result="failed",
                        error_summary="导入任务执行失败",
                        permission="quiz:import",
                    )
                    await db.commit()
            return False

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
                        (QuizImportJob.status.in_(("validating", "importing"))
                         & (QuizImportJob.heartbeat_at < stale_before)),
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
                and job.heartbeat_at
                and job.heartbeat_at < stale_before
            )
            before_status = job.status
            before_retry_count = job.retry_count or 0
            job.status = "queued"
            job.retry_count = before_retry_count + (1 if was_stale else 0)
            job.heartbeat_at = now
            if was_stale:
                self._add_audit(
                    db,
                    admin_id=None,
                    action="import.retry",
                    object_type="import_job",
                    object_id=job.id,
                    changed_fields={
                        "status": {"before": before_status, "after": "queued"},
                        "retry_count": {
                            "before": before_retry_count,
                            "after": job.retry_count,
                        },
                    },
                    permission="quiz:import",
                )
            await db.commit()
            job_id = job.id
        await self.process_import_job(job_id)
        return True

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

                    if not question_type or not question_text or not correct_answer:
                        skipped += 1
                        continue

                    if question_type not in ("single_choice", "multiple_choice", "judge"):
                        errors.append({"row": row_num, "reason": f"无效题型: {question_type}"})
                        skipped += 1
                        continue

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
                    if item.question_type not in ("single_choice", "multiple_choice", "judge"):
                        errors.append({"index": idx, "reason": f"无效题型: {item.question_type}"})
                        skipped += 1
                        continue

                    normalized = self._normalize_admin_question(
                        question_type=item.question_type,
                        question_text=item.question_text,
                        options=item.options,
                        correct_answer=item.correct_answer,
                        explanation=item.explanation,
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
            "options": ["options", "选项"],
        }
        for letter in "abcdefgh":
            for template in (f"option_{letter}", f"{letter}", f"选项{letter.upper()}"):
                aliases.setdefault(f"option_{letter}", []).append(template)

        header_map: dict[str, str] = {}
        for field in fieldnames:
            field_lower = field.strip().lower()
            for target, candidates in aliases.items():
                if field_lower in candidates or field.strip() in candidates:
                    header_map[target] = field
                    break
        return header_map
