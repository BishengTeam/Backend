"""Import quiz categories and questions from CSV files.

Usage examples:
    python scripts/import_quiz.py --categories categories.csv
    python scripts/import_quiz.py --questions questions.csv --create-missing-categories
    python scripts/import_quiz.py --categories categories.csv --questions questions.csv --dry-run

Category CSV columns:
    path,name,parent_path,parent_name,description

Question CSV columns:
    category_path,question_type,question_text,correct_answer,explanation,
    options or option_a/option_b/option_c/option_d...

Allowed question_type values:
    single_choice,multiple_choice,judge
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.domain.community.src.rule.quiz import (
    normalize_category_name,
    normalize_question_payload,
)

ALLOWED_QUESTION_TYPES = {"single_choice", "multiple_choice", "judge"}
MAX_CATEGORY_NAME_LENGTH = 128
MAX_CATEGORY_DESCRIPTION_LENGTH = 256
MAX_QUESTION_TEXT_LENGTH = 1024
MAX_CORRECT_ANSWER_LENGTH = 256
MAX_EXPLANATION_LENGTH = 1024

HEADER_ALIASES = {
    "path": "path",
    "category_path": "category_path",
    "category": "category_path",
    "category_name": "category_path",
    "name": "name",
    "parent": "parent_path",
    "parent_path": "parent_path",
    "parent_name": "parent_name",
    "description": "description",
    "question_type": "question_type",
    "type": "question_type",
    "question_text": "question_text",
    "question": "question_text",
    "text": "question_text",
    "options": "options",
    "correct_answer": "correct_answer",
    "answer": "correct_answer",
    "explanation": "explanation",
    # Common Chinese template headers.
    "分类路径": "category_path",
    "分类": "category_path",
    "题目分类": "category_path",
    "路径": "path",
    "名称": "name",
    "分类名称": "name",
    "父级": "parent_path",
    "父级路径": "parent_path",
    "父级名称": "parent_name",
    "描述": "description",
    "题型": "question_type",
    "题干": "question_text",
    "题目": "question_text",
    "选项": "options",
    "正确答案": "correct_answer",
    "答案": "correct_answer",
    "解析": "explanation",
}

JUDGE_TRUE_VALUES = {"true", "t", "yes", "y", "1", "正确", "对", "是", "a"}
JUDGE_FALSE_VALUES = {"false", "f", "no", "n", "0", "错误", "错", "否", "b"}
ANSWER_SPLIT_RE = re.compile(r"[,，;；|/\s]+")

QuizCategory: Any = None
QuizQuestion: Any = None
AdminUser: Any = None


def _require_loaded_models() -> None:
    """Fail clearly when a helper is called before model loading.

    The CLI loads settings-dependent models lazily.  Keeping this guard at the
    helper boundary prevents a standalone import (or an embedding script)
    from turning an uninitialized global into an opaque ``NoneType`` error.
    """
    if any(
        value is None
        for value in (
            QuizCategory,
            QuizQuestion,
            AdminUser,
        )
    ):
        raise RuntimeError(
            "题库导入模型尚未加载，请先调用 load_models_and_session_factory()"
        )


@dataclass(frozen=True)
class CategoryInput:
    line_no: int
    path: tuple[str, ...]
    description: str | None


@dataclass(frozen=True)
class QuestionInput:
    line_no: int
    category_path: tuple[str, ...]
    question_type: str
    question_text: str
    options: dict[str, str] | None
    correct_answer: str
    explanation: str | None


@dataclass
class ImportStats:
    categories_created: int = 0
    categories_existing: int = 0
    questions_created: int = 0
    questions_skipped: int = 0


def canonical_header(header: str) -> str:
    key = header.strip().lstrip("\ufeff").lower().replace(" ", "_").replace("-", "_")
    return HEADER_ALIASES.get(key, key)


def normalize_row(raw_row: dict[str | None, str | None]) -> tuple[dict[str, str], bool]:
    has_extra_columns = None in raw_row
    row: dict[str, str] = {}
    for raw_key, raw_value in raw_row.items():
        if raw_key is None:
            continue
        key = canonical_header(raw_key)
        value = (raw_value or "").strip()
        row[key] = value
    return row, has_extra_columns


def read_csv_rows(path: Path, label: str, errors: list[str]) -> list[tuple[int, dict[str, str]]]:
    if not path.exists():
        errors.append(f"{label}: file not found: {path}")
        return []
    if not path.is_file():
        errors.append(f"{label}: not a file: {path}")
        return []

    rows: list[tuple[int, dict[str, str]]] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fp:
            reader = csv.DictReader(fp)
            if not reader.fieldnames:
                errors.append(f"{label}: missing CSV header")
                return []
            for line_no, raw_row in enumerate(reader, start=2):
                row, has_extra_columns = normalize_row(raw_row)
                if has_extra_columns:
                    errors.append(f"{label}:{line_no}: column count does not match header")
                    continue
                if all(not value for value in row.values()):
                    continue
                rows.append((line_no, row))
    except UnicodeDecodeError as exc:
        errors.append(f"{label}: expected UTF-8/UTF-8-BOM CSV: {exc}")
    except OSError as exc:
        errors.append(f"{label}: failed to read file: {exc}")

    if not rows:
        errors.append(f"{label}: no data rows")
    return rows


def split_path(value: str, *, field: str, label: str, line_no: int, errors: list[str]) -> tuple[str, ...] | None:
    normalized = value.replace("\\", "/")
    raw_parts = normalized.split("/")
    parts = tuple(part.strip() for part in raw_parts)
    if not parts or any(not part for part in parts):
        errors.append(f"{label}:{line_no}: {field} must be a non-empty slash-separated path")
        return None
    if len(parts) > 3:
        errors.append(f"{label}:{line_no}: {field} supports at most three category levels")
        return None
    for part in parts:
        if len(part) > MAX_CATEGORY_NAME_LENGTH:
            errors.append(
                f"{label}:{line_no}: category name '{part}' exceeds {MAX_CATEGORY_NAME_LENGTH} characters"
            )
            return None
    return parts


def validate_length(
    value: str,
    *,
    max_length: int,
    field: str,
    label: str,
    line_no: int,
    errors: list[str],
) -> None:
    if len(value) > max_length:
        errors.append(f"{label}:{line_no}: {field} exceeds {max_length} characters")


def parse_categories(path: Path, errors: list[str]) -> list[CategoryInput]:
    label = str(path)
    category_rows: list[CategoryInput] = []
    seen_paths: set[tuple[str, ...]] = set()

    for line_no, row in read_csv_rows(path, label, errors):
        description = row.get("description") or None
        if description:
            validate_length(
                description,
                max_length=MAX_CATEGORY_DESCRIPTION_LENGTH,
                field="description",
                label=label,
                line_no=line_no,
                errors=errors,
            )

        if row.get("path"):
            category_path = split_path(row["path"], field="path", label=label, line_no=line_no, errors=errors)
        else:
            name = row.get("name")
            if not name:
                errors.append(f"{label}:{line_no}: either path or name is required")
                continue
            validate_length(
                name,
                max_length=MAX_CATEGORY_NAME_LENGTH,
                field="name",
                label=label,
                line_no=line_no,
                errors=errors,
            )
            parent_path: tuple[str, ...] = ()
            if row.get("parent_path"):
                parsed_parent = split_path(
                    row["parent_path"], field="parent_path", label=label, line_no=line_no, errors=errors
                )
                if parsed_parent is None:
                    continue
                parent_path = parsed_parent
            elif row.get("parent_name"):
                validate_length(
                    row["parent_name"],
                    max_length=MAX_CATEGORY_NAME_LENGTH,
                    field="parent_name",
                    label=label,
                    line_no=line_no,
                    errors=errors,
                )
                parent_path = (row["parent_name"],)
            category_path = (*parent_path, name)

        if category_path is None:
            continue
        if category_path in seen_paths:
            errors.append(f"{label}:{line_no}: duplicated category path: {'/'.join(category_path)}")
            continue
        seen_paths.add(category_path)
        category_rows.append(CategoryInput(line_no=line_no, path=category_path, description=description))

    return category_rows


def option_key_from_header(header: str) -> str | None:
    key = header.strip().lower()
    if len(key) == 1 and key.isalpha():
        return key.upper()
    if key.startswith("option_") and len(key) > len("option_"):
        suffix = key[len("option_") :].strip()
        if len(suffix) == 1 and suffix.isalnum():
            return suffix.upper()
    if key.startswith("选项") and len(key) > len("选项"):
        suffix = key[len("选项") :].strip()
        if len(suffix) == 1:
            return suffix.upper()
    return None


def parse_options(row: dict[str, str], *, label: str, line_no: int, errors: list[str]) -> dict[str, str] | None:
    raw_options = row.get("options")
    if raw_options:
        try:
            parsed = json.loads(raw_options)
        except json.JSONDecodeError as exc:
            errors.append(f"{label}:{line_no}: options must be a JSON object: {exc.msg}")
            return None
        if not isinstance(parsed, dict):
            errors.append(f"{label}:{line_no}: options must be a JSON object")
            return None
        options = {str(key).strip().upper(): str(value).strip() for key, value in parsed.items()}
    else:
        options = {}
        for header, value in row.items():
            if not value:
                continue
            option_key = option_key_from_header(header)
            if option_key:
                options[option_key] = value

    if not options:
        return None
    for option_key, option_value in options.items():
        if not option_key:
            errors.append(f"{label}:{line_no}: option key cannot be empty")
        if not option_value:
            errors.append(f"{label}:{line_no}: option {option_key} cannot be empty")
    return dict(sorted(options.items()))


def normalize_choice_answer(
    answer: str,
    *,
    question_type: str,
    option_keys: Iterable[str],
    label: str,
    line_no: int,
    errors: list[str],
) -> str:
    allowed = set(option_keys)
    if question_type == "single_choice":
        normalized = answer.strip().upper()
        if normalized not in allowed:
            errors.append(f"{label}:{line_no}: correct_answer must match one option key")
        return normalized

    parts = [part.strip().upper() for part in ANSWER_SPLIT_RE.split(answer) if part.strip()]
    if len(parts) == 1 and len(parts[0]) > 1 and all(char in allowed for char in parts[0]):
        parts = list(parts[0])
    if not parts:
        errors.append(f"{label}:{line_no}: correct_answer is required")
        return answer
    unknown = sorted(set(parts) - allowed)
    if unknown:
        errors.append(f"{label}:{line_no}: correct_answer contains unknown option key(s): {','.join(unknown)}")
    if len(set(parts)) != len(parts):
        errors.append(f"{label}:{line_no}: correct_answer contains duplicated option key")
    return ",".join(sorted(set(parts)))


def normalize_judge_answer(answer: str, *, label: str, line_no: int, errors: list[str]) -> str:
    normalized = answer.strip().lower()
    if normalized in JUDGE_TRUE_VALUES:
        return "true"
    if normalized in JUDGE_FALSE_VALUES:
        return "false"
    errors.append(f"{label}:{line_no}: judge correct_answer must be true/false, A/B, or 正确/错误")
    return answer


def parse_questions(path: Path, errors: list[str]) -> list[QuestionInput]:
    label = str(path)
    questions: list[QuestionInput] = []
    seen_questions: set[tuple[tuple[str, ...], str]] = set()

    for line_no, row in read_csv_rows(path, label, errors):
        raw_category_path = row.get("category_path") or row.get("path")
        if not raw_category_path:
            errors.append(f"{label}:{line_no}: category_path is required")
            continue
        category_path = split_path(
            raw_category_path, field="category_path", label=label, line_no=line_no, errors=errors
        )
        if category_path is None:
            continue

        question_type = (row.get("question_type") or "").strip()
        if question_type not in ALLOWED_QUESTION_TYPES:
            errors.append(
                f"{label}:{line_no}: question_type must be one of {', '.join(sorted(ALLOWED_QUESTION_TYPES))}"
            )

        question_text = row.get("question_text") or ""
        if not question_text:
            errors.append(f"{label}:{line_no}: question_text is required")
        validate_length(
            question_text,
            max_length=MAX_QUESTION_TEXT_LENGTH,
            field="question_text",
            label=label,
            line_no=line_no,
            errors=errors,
        )

        correct_answer = row.get("correct_answer") or ""
        if not correct_answer:
            errors.append(f"{label}:{line_no}: correct_answer is required")
        validate_length(
            correct_answer,
            max_length=MAX_CORRECT_ANSWER_LENGTH,
            field="correct_answer",
            label=label,
            line_no=line_no,
            errors=errors,
        )

        explanation = row.get("explanation") or None
        if explanation:
            validate_length(
                explanation,
                max_length=MAX_EXPLANATION_LENGTH,
                field="explanation",
                label=label,
                line_no=line_no,
                errors=errors,
            )

        options = parse_options(row, label=label, line_no=line_no, errors=errors)
        if question_type in {"single_choice", "multiple_choice"}:
            if not options or len(options) < 2:
                errors.append(f"{label}:{line_no}: choice questions require at least two options")
            elif correct_answer:
                correct_answer = normalize_choice_answer(
                    correct_answer,
                    question_type=question_type,
                    option_keys=options.keys(),
                    label=label,
                    line_no=line_no,
                    errors=errors,
                )
        elif question_type == "judge" and correct_answer:
            correct_answer = normalize_judge_answer(correct_answer, label=label, line_no=line_no, errors=errors)

        question_key = (category_path, question_text)
        if question_text and question_key in seen_questions:
            errors.append(f"{label}:{line_no}: duplicated question_text in category {'/'.join(category_path)}")
            continue
        seen_questions.add(question_key)

        questions.append(
            QuestionInput(
                line_no=line_no,
                category_path=category_path,
                question_type=question_type,
                question_text=question_text,
                options=options,
                correct_answer=correct_answer,
                explanation=explanation,
            )
        )

    return questions


def load_models_and_session_factory() -> sessionmaker:
    global AdminUser, QuizCategory, QuizQuestion

    from app.core.config import settings
    from app.domain.community.src.index import QuizCategory as LoadedQuizCategory
    from app.domain.community.src.index import QuizQuestion as LoadedQuizQuestion
    from app.domain.user.src.index import AdminUser as LoadedAdminUser

    QuizCategory = LoadedQuizCategory
    QuizQuestion = LoadedQuizQuestion
    AdminUser = LoadedAdminUser

    engine = create_engine(settings.DATABASE_URL_SYNC, future=True)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def find_category(session: Any, parent_id: int | None, name: str) -> Any | None:
    _require_loaded_models()
    normalized = normalize_category_name(name)
    stmt = select(QuizCategory).where(QuizCategory.normalized_name == normalized)
    if parent_id is None:
        stmt = stmt.where(QuizCategory.parent_id.is_(None))
    else:
        stmt = stmt.where(QuizCategory.parent_id == parent_id)
    return session.scalars(stmt.order_by(QuizCategory.id).limit(1)).first()


def find_category_path(session: Any, category_path: tuple[str, ...]) -> Any | None:
    _require_loaded_models()
    parent_id: int | None = None
    category = None
    for name in category_path:
        category = find_category(session, parent_id, name)
        if category is None:
            return None
        if getattr(category, "status", "active") != "active":
            return None
        parent_id = category.id
    return category


def ensure_category_path(
    session: Any,
    category_path: tuple[str, ...],
    *,
    description: str | None = None,
    stats: ImportStats,
    admin_id: int,
) -> Any:
    _require_loaded_models()
    parent_id: int | None = None
    category = None
    for index, name in enumerate(category_path):
        category = find_category(session, parent_id, name)
        if category is None:
            is_leaf = index == len(category_path) - 1
            normalized = normalize_category_name(name)
            category = QuizCategory(
                name=normalized,
                normalized_name=normalized,
                parent_id=parent_id,
                depth=index + 1,
                description=description if is_leaf else None,
                status="active",
                sort_order=0,
                ever_had_question=False,
                lock_version=1,
                created_by=admin_id,
                updated_by=admin_id,
            )
            session.add(category)
            session.flush()
            stats.categories_created += 1
        else:
            if getattr(category, "status", "active") != "active":
                raise ValueError(f"分类已停用: {'/'.join(category_path[: index + 1])}")
            stats.categories_existing += 1
        parent_id = category.id
    return category


def question_exists(session: Any, *, category_id: int, question_text_hash: str) -> bool:
    _require_loaded_models()
    stmt = (
        select(QuizQuestion.id)
        .where(QuizQuestion.category_id == category_id)
        .where(QuizQuestion.question_text_hash == question_text_hash)
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none() is not None


def validate_category_references(
    session: Any,
    questions: list[QuestionInput],
    category_rows: list[CategoryInput],
    *,
    create_missing_categories: bool,
    errors: list[str],
) -> None:
    csv_category_paths = {row.path for row in category_rows}
    checked_paths: dict[tuple[str, ...], bool] = {}
    for question in questions:
        if question.category_path in checked_paths:
            exists = checked_paths[question.category_path]
        else:
            exists = find_category_path(session, question.category_path) is not None
            checked_paths[question.category_path] = exists
        if not exists and question.category_path not in csv_category_paths:
            errors.append(
                f"questions:{question.line_no}: category_path not found: {'/'.join(question.category_path)}; "
                "provide --categories or ask an administrator to create the category first"
            )


def import_categories(
    session: Any,
    category_rows: list[CategoryInput],
    stats: ImportStats,
    *,
    admin_id: int,
) -> None:
    _require_loaded_models()
    for row in category_rows:
        ensure_category_path(
            session,
            row.path,
            description=row.description,
            stats=stats,
            admin_id=admin_id,
        )


def import_questions(
    session: Any,
    questions: list[QuestionInput],
    *,
    create_missing_categories: bool,
    category_rows: list[CategoryInput],
    allow_duplicates: bool,
    stats: ImportStats,
    admin_id: int,
) -> None:
    _require_loaded_models()
    csv_category_paths = {row.path for row in category_rows}
    for row in questions:
        # Categories are controlled by administrators and must be created
        # before a question batch is accepted.  ``category_rows`` is an
        # explicit category import in the same transaction, not an implicit
        # create-from-question fallback.
        if row.category_path in csv_category_paths:
            category = ensure_category_path(
                session,
                row.category_path,
                stats=stats,
                admin_id=admin_id,
            )
        else:
            category = find_category_path(session, row.category_path)
            if category is None:
                raise ValueError(
                    f"category_path not found: {'/'.join(row.category_path)}; "
                    "please ask an administrator to create the category first"
                )

        answer: object = row.correct_answer
        if row.question_type == "multiple_choice":
            answer = [part for part in ANSWER_SPLIT_RE.split(row.correct_answer) if part]
        elif row.question_type == "judge":
            answer = "A" if row.correct_answer.lower() in JUDGE_TRUE_VALUES else "B"
        normalized = normalize_question_payload(
            question_type=row.question_type,
            question_text=row.question_text,
            options=row.options,
            correct_answer=answer,
            explanation=row.explanation,
            require_publishable=False,
        )
        if not allow_duplicates and question_exists(
            session,
            category_id=category.id,
            question_text_hash=normalized.question_text_hash,
        ):
            stats.questions_skipped += 1
            continue
        session.add(
            QuizQuestion(
                category_id=category.id,
                question_type=normalized.question_type.value,
                status="draft",
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
        )
        category.ever_had_question = True
        stats.questions_created += 1


def print_validation_errors(errors: list[str]) -> None:
    print("CSV validation failed:", file=sys.stderr)
    for error in errors:
        print(f"  - {error}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import quiz categories/questions from CSV files.")
    parser.add_argument("--categories", type=Path, help="CSV file for quiz_category rows")
    parser.add_argument("--questions", type=Path, help="CSV file for quiz_question rows")
    parser.add_argument(
        "--create-missing-categories",
        action="store_true",
        help="Create category_path values referenced by questions when they do not exist",
    )
    parser.add_argument(
        "--allow-duplicates",
        action="store_true",
        help="Insert duplicate question_text values in the same category instead of skipping them",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate files and DB references without writing")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not args.categories and not args.questions:
        parser.error("at least one of --categories or --questions is required")

    errors: list[str] = []
    category_rows = parse_categories(args.categories, errors) if args.categories else []
    questions = parse_questions(args.questions, errors) if args.questions else []
    if errors:
        print_validation_errors(errors)
        return 2

    SessionLocal = load_models_and_session_factory()
    with SessionLocal() as session:
        admin_id = session.scalar(select(AdminUser.id).order_by(AdminUser.id).limit(1))
        if admin_id is None:
            print(
                "quiz import requires an administrator row; run scripts/init_super_admin.py first",
                file=sys.stderr,
            )
            return 2
        if questions:
            validate_category_references(
                session,
                questions,
                category_rows,
                create_missing_categories=args.create_missing_categories,
                errors=errors,
            )
        if errors:
            print_validation_errors(errors)
            return 2
        if session.in_transaction():
            session.rollback()

        if args.dry_run:
            print(
                "Dry run passed: "
                f"{len(category_rows)} category row(s), {len(questions)} question row(s) validated."
            )
            return 0

        stats = ImportStats()
        with session.begin():
            import_categories(session, category_rows, stats, admin_id=admin_id)
            import_questions(
                session,
                questions,
                create_missing_categories=args.create_missing_categories,
                category_rows=category_rows,
                allow_duplicates=args.allow_duplicates,
                stats=stats,
                admin_id=admin_id,
            )

    print(
        "Import completed: "
        f"categories created={stats.categories_created}, "
        f"categories existing={stats.categories_existing}, "
        f"questions created={stats.questions_created}, "
        f"questions skipped={stats.questions_skipped}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
