import csv
import io
import json
from typing import Any

from sqlalchemy import func, select

from app.core.database import get_db_ctx
from app.core.exceptions import BusinessException, NotFoundException, ValidationException
from app.models.quiz import QuizCategory, QuizQuestion, QuizRecord
from app.schemas.admin_quiz import (
    AdminQuizCategoryCreate,
    AdminQuizCategoryUpdate,
    AdminQuizQuestionCreate,
    AdminQuizQuestionUpdate,
)
from app.schemas.common import PaginatedData
from app.schemas.quiz import QuizCategoryResponse, QuizQuestionResponse


class AdminQuizService:

    # ── Category CRUD ──

    async def create_category(self, data: AdminQuizCategoryCreate) -> QuizCategoryResponse:
        async with get_db_ctx() as db:
            if data.parent_id is not None:
                parent = await db.get(QuizCategory, data.parent_id)
                if parent is None:
                    raise NotFoundException("父级分类")
            category = QuizCategory(**data.model_dump())
            db.add(category)
            await db.commit()
            await db.refresh(category)
            return QuizCategoryResponse.model_validate(category)

    async def update_category(self, category_id: int, data: AdminQuizCategoryUpdate) -> QuizCategoryResponse:
        async with get_db_ctx() as db:
            category = await db.get(QuizCategory, category_id)
            if category is None:
                raise NotFoundException("题库分类")
            if data.parent_id is not None:
                parent = await db.get(QuizCategory, data.parent_id)
                if parent is None:
                    raise NotFoundException("父级分类")
            update_data = data.model_dump(exclude_unset=True)
            for key, value in update_data.items():
                setattr(category, key, value)
            await db.commit()
            await db.refresh(category)
            return QuizCategoryResponse.model_validate(category)

    async def delete_category(self, category_id: int) -> None:
        async with get_db_ctx() as db:
            category = await db.get(QuizCategory, category_id)
            if category is None:
                raise NotFoundException("题库分类")
            child_count = (
                await db.execute(
                    select(func.count()).select_from(QuizCategory).where(
                        QuizCategory.parent_id == category_id
                    )
                )
            ).scalar() or 0
            if child_count > 0:
                raise BusinessException("该分类下存在子分类，请先删除子分类")
            question_count = (
                await db.execute(
                    select(func.count()).select_from(QuizQuestion).where(
                        QuizQuestion.category_id == category_id
                    )
                )
            ).scalar() or 0
            if question_count > 0:
                raise BusinessException("该分类下存在题目，请先删除题目")
            await db.delete(category)
            await db.commit()

    # ── Question CRUD ──

    async def create_question(self, data: AdminQuizQuestionCreate) -> QuizQuestionResponse:
        async with get_db_ctx() as db:
            category = await db.get(QuizCategory, data.category_id)
            if category is None:
                raise NotFoundException("题库分类")
            question = QuizQuestion(**data.model_dump())
            db.add(question)
            await db.commit()
            await db.refresh(question)
            return QuizQuestionResponse.model_validate(question)

    async def update_question(self, question_id: int, data: AdminQuizQuestionUpdate) -> QuizQuestionResponse:
        async with get_db_ctx() as db:
            question = await db.get(QuizQuestion, question_id)
            if question is None:
                raise NotFoundException("题目")
            if data.category_id is not None:
                category = await db.get(QuizCategory, data.category_id)
                if category is None:
                    raise NotFoundException("题库分类")
            update_data = data.model_dump(exclude_unset=True)
            for key, value in update_data.items():
                setattr(question, key, value)
            await db.commit()
            await db.refresh(question)
            return QuizQuestionResponse.model_validate(question)

    async def delete_question(self, question_id: int) -> None:
        async with get_db_ctx() as db:
            question = await db.get(QuizQuestion, question_id)
            if question is None:
                raise NotFoundException("题目")
            record_count = (
                await db.execute(
                    select(func.count()).select_from(QuizRecord).where(
                        QuizRecord.question_id == question_id
                    )
                )
            ).scalar() or 0
            if record_count > 0:
                raise BusinessException("该题目已有答题记录，不可删除")
            await db.delete(question)
            await db.commit()

    # ── Import ──

    async def import_questions_csv(
        self, content: bytes, create_missing_categories: bool = False
    ) -> dict[str, Any]:
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

                    category_id = await self._resolve_category(db, category_path, create_missing_categories)

                    question = QuizQuestion(
                        category_id=category_id,
                        question_type=question_type,
                        question_text=question_text,
                        options=options,
                        correct_answer=correct_answer,
                        explanation=explanation,
                    )
                    db.add(question)
                    created += 1
                except Exception as exc:
                    errors.append({"row": row_num, "reason": str(exc)})
                    skipped += 1

            await db.commit()

        return {"created": created, "skipped": skipped, "errors": errors}

    async def _resolve_category(
        self, db, path: str, create_missing: bool
    ) -> int:
        """Resolve a category path like 'H3C/网络基础' to a category ID."""
        if not path:
            raise ValidationException("分类路径不能为空")

        parts = [p.strip() for p in path.replace("\\", "/").split("/") if p.strip()]
        parent_id = None
        category_id = None

        for part in parts:
            stmt = select(QuizCategory).where(
                QuizCategory.name == part,
                QuizCategory.parent_id == parent_id,
            )
            category = (await db.execute(stmt)).scalar_one_or_none()
            if category is not None:
                category_id = category.id
            elif create_missing:
                category = QuizCategory(name=part, parent_id=parent_id)
                db.add(category)
                await db.flush()
                category_id = category.id
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
