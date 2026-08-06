from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError
from sqlalchemy.dialects.postgresql import JSONB

from app.adapter.database import Base
from app.contracts.quiz import (
    DELETED_QUIZ_ENDPOINTS,
    QUIZ_API_CONTRACTS,
    QuizErrorCode,
)
from app.domain.community.src.model.quiz import (
    QuizAdminAuditLog,
    QuizCategory,
    QuizCheckin,
    QuizCollection,
    QuizExam,
    QuizPracticeAttempt,
    QuizQuestion,
    QuizWrongItem,
)
from app.domain.community.src.rule.quiz import (
    JUDGE_OPTIONS,
    QuizRuleViolation,
    answers_match,
    normalize_question_payload,
    normalize_question_text,
    normalize_submitted_answer,
)
from app.port.config import Settings
from app.schemas.admin_quiz_contract import (
    AdminQuizCategoryUpdate,
    AdminQuizQuestionCreate,
)
from app.schemas.quiz_contract import (
    QuizExamAbandonedDetail,
    QuizExamInProgressDetail,
    QuizExamSettledDetail,
    QuizPublicQuestion,
)
from app.services.quiz_tasks import QuizTaskRegistry


REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = REPO_ROOT / "alembic/versions/quiz001_rebuild_quiz_domain.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("quiz001_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _production_settings(**overrides) -> Settings:
    values = {
        "APP_ENV": "production",
        "APP_DEBUG": False,
        "JWT_SECRET": "stage-one-test-secret-that-is-long-enough",
        "PII_HASH_KEY": "stage-one-test-pii-key-that-is-long-enough",
        "REDIS_URL": "rediss://redis.internal:6379/0",
        "RENSHE_STORAGE_TYPE": "aliyun_oss",
        "ALIYUN_OSS_ENDPOINT": "https://oss-cn.example.com",
        "ALIYUN_OSS_BUCKET": "renshe-private",
        "ALIYUN_OSS_ACCESS_KEY_ID": "renshe-key",
        "ALIYUN_OSS_ACCESS_KEY_SECRET": "renshe-secret",
        "QUIZ_IMPORT_STORAGE_TYPE": "aliyun_oss",
        "QUIZ_OSS_ENDPOINT": "https://oss-cn.example.com",
        "QUIZ_OSS_BUCKET": "quiz-private",
        "QUIZ_OSS_ACCESS_KEY_ID": "quiz-key",
        "QUIZ_OSS_ACCESS_KEY_SECRET": "quiz-secret",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_question_rules_normalize_all_supported_types() -> None:
    single = normalize_question_payload(
        question_type="single_choice",
        question_text="  HTTP   默认端口是？ ",
        options={"a": "80", "b": "443", "c": "22"},
        correct_answer=" a ",
        explanation="HTTP 默认使用 80。",
        require_publishable=True,
    )
    assert single.normalized_question_text == "HTTP 默认端口是？"
    assert single.options == {"A": "80", "B": "443", "C": "22"}
    assert single.correct_answer == "A"
    assert len(single.question_text_hash) == 64

    multiple = normalize_question_payload(
        question_type="multiple_choice",
        question_text="选择协议",
        options={"A": "HTTP", "B": "SSH", "C": "TCP", "D": "UDP"},
        correct_answer=["D", "A", "A"],
        explanation="A 和 D。",
        require_publishable=True,
    )
    assert multiple.correct_answer == ["A", "D"]

    judge = normalize_question_payload(
        question_type="judge",
        question_text="TCP 面向连接。",
        correct_answer="a",
        explanation="正确。",
        require_publishable=True,
    )
    assert judge.options == JUDGE_OPTIONS
    assert judge.correct_answer == "A"


def test_question_rules_allow_incomplete_drafts_but_reject_invalid_publish() -> None:
    draft = normalize_question_payload(
        question_type="single_choice",
        question_text="未完成草稿",
        options={"A": "一个选项", "B": "两个选项"},
    )
    assert draft.correct_answer is None

    with pytest.raises(QuizRuleViolation, match="3 至 4"):
        normalize_question_payload(
            question_type="single_choice",
            question_text="不能发布",
            options={"A": "一", "B": "二"},
            correct_answer="A",
            explanation="解析",
            require_publishable=True,
        )

    with pytest.raises(QuizRuleViolation, match="固定为"):
        normalize_question_payload(
            question_type="judge",
            question_text="判断",
            options={"A": "是", "B": "否"},
        )


def test_submitted_answers_are_canonical_and_exact_match_only() -> None:
    options = {"A": "一", "B": "二", "C": "三", "D": "四"}
    assert normalize_submitted_answer(
        "multiple_choice", ["c", "A", "C"], options=options
    ) == ["A", "C"]
    assert answers_match(
        "multiple_choice", ["C", "A"], ["A", "C"], options=options
    )
    assert not answers_match(
        "multiple_choice", ["A"], ["A", "C"], options=options
    )
    assert normalize_question_text(" A\n\t B ") == "A B"


def test_contract_registry_is_complete_strict_and_machine_readable() -> None:
    assert len(QUIZ_API_CONTRACTS) == 43
    keys = {(entry.method, entry.path) for entry in QUIZ_API_CONTRACTS}
    assert len(keys) == len(QUIZ_API_CONTRACTS)
    assert ("GET", "/api/quiz/categories") in keys
    assert ("GET", "/admin/quiz/audit-logs") in keys

    for entry in QUIZ_API_CONTRACTS:
        TypeAdapter(entry.response_model).json_schema()
        for model in (entry.query_model, entry.body_model):
            if model is not None:
                assert isinstance(model, type) and issubclass(model, BaseModel)
                model.model_json_schema()
        if entry.auth == "admin":
            assert entry.permission in {"quiz:list", "quiz:write", "quiz:import"}
        assert entry.example

    question_list = next(
        entry
        for entry in QUIZ_API_CONTRACTS
        if (entry.method, entry.path) == ("GET", "/api/quiz/questions")
    )
    answer_save = next(
        entry
        for entry in QUIZ_API_CONTRACTS
        if entry.path.endswith("/answers/{exam_question_id}")
    )
    assert question_list.rate_limit_per_minute == 60
    assert answer_save.rate_limit_per_minute == 120
    assert QuizErrorCode.RATE_LIMITED in answer_save.errors


def test_contract_explicitly_deletes_legacy_endpoints() -> None:
    deleted = set(DELETED_QUIZ_ENDPOINTS)
    assert ("POST", "/api/quiz/submit") in deleted
    assert ("POST", "/api/quiz/checkin") in deleted
    assert ("POST", "/admin/quiz/import") in deleted
    assert ("POST", "/admin/quiz/questions/batch-delete") in deleted
    assert not deleted & {
        (entry.method, entry.path) for entry in QUIZ_API_CONTRACTS
    }


def test_answer_visibility_is_enforced_by_separate_exam_schemas() -> None:
    public_fields = QuizPublicQuestion.model_fields
    assert "correct_answer" not in public_fields
    assert "explanation" not in public_fields

    in_progress_schema = str(QuizExamInProgressDetail.model_json_schema())
    abandoned_schema = str(QuizExamAbandonedDetail.model_json_schema())
    settled_schema = str(QuizExamSettledDetail.model_json_schema())
    assert "correct_answer" not in in_progress_schema
    assert "explanation" not in in_progress_schema
    assert "correct_answer" not in abandoned_schema
    assert "explanation" not in abandoned_schema
    assert "correct_answer" in settled_schema
    assert "explanation" in settled_schema


def test_admin_contract_normalizes_judge_drafts_and_requires_versions() -> None:
    question = AdminQuizQuestionCreate(
        category_id=1,
        question_type="judge",
        question_text="  TCP 面向连接。 ",
        correct_answer="a",
    )
    assert question.options == JUDGE_OPTIONS
    assert question.correct_answer == "A"

    with pytest.raises(ValidationError):
        AdminQuizCategoryUpdate(name="新名称")
    with pytest.raises(ValidationError):
        AdminQuizCategoryUpdate(lock_version=1)


def test_quiz_metadata_matches_the_rebuilt_domain() -> None:
    migration = _load_migration()
    expected = set(migration.NEW_TABLES)
    actual = {name for name in Base.metadata.tables if name.startswith("quiz_")}
    assert actual == expected
    assert "quiz_record" not in actual
    assert isinstance(QuizQuestion.__table__.c.options.type, JSONB)
    assert QuizCategory.__table__.c.depth.type.python_type is int

    category_indexes = {index.name: index for index in QuizCategory.__table__.indexes}
    assert category_indexes["uq_quiz_category_root_name"].unique
    assert category_indexes["uq_quiz_category_sibling_name"].unique

    exam_indexes = {index.name: index for index in QuizExam.__table__.indexes}
    assert exam_indexes["uq_quiz_exam_active_user"].unique
    assert exam_indexes["uq_quiz_exam_active_user"].dialect_options["postgresql"]["where"] is not None

    attempt_constraints = {constraint.name for constraint in QuizPracticeAttempt.__table__.constraints}
    assert "uq_quiz_practice_attempt_idempotency" in attempt_constraints
    assert "uq_quiz_practice_attempt_number" in attempt_constraints
    assert "fk_quiz_practice_attempt_session_question" in attempt_constraints


def test_reactivation_and_retention_constraints_match_frozen_lifecycle() -> None:
    def constraint_sql(model: type, name: str) -> str:
        constraint = next(
            item for item in model.__table__.constraints if item.name == name
        )
        return str(constraint.sqltext)

    question_lifecycle = constraint_sql(
        QuizQuestion, "ck_quiz_question_lifecycle"
    )
    wrong_lifecycle = constraint_sql(
        QuizWrongItem, "ck_quiz_wrong_item_lifecycle"
    )
    collection_lifecycle = constraint_sql(
        QuizCollection, "ck_quiz_collection_lifecycle"
    )
    exam_deadline = constraint_sql(QuizExam, "ck_quiz_exam_deadline")

    assert "published_at IS NOT NULL) OR" in question_lifecycle
    assert "published_at IS NOT NULL AND disabled_at IS NULL" not in question_lifecycle
    assert "status = 'active' OR" in wrong_lifecycle
    assert "status = 'active' AND cleared_at IS NULL" not in wrong_lifecycle
    assert "is_active = true OR removed_at IS NOT NULL" in collection_lifecycle
    assert "INTERVAL '3600 seconds'" in exam_deadline

    checkin_attempt_fk = next(
        fk
        for fk in QuizCheckin.__table__.foreign_key_constraints
        if fk.referred_table.name == "quiz_practice_attempt"
    )
    audit_admin_fk = next(iter(QuizAdminAuditLog.__table__.foreign_key_constraints))
    assert checkin_attempt_fk.ondelete == "CASCADE"
    assert audit_admin_fk.ondelete == "RESTRICT"


def test_destructive_migration_is_isolated_and_backup_gated() -> None:
    migration = _load_migration()
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    assert migration.revision == "quiz001"
    assert migration.down_revision == "rsh001"
    assert "quiz_backup_ref" in source
    assert "quiz_downgrade_backup_ref" in source
    assert not re.search(r"\bbanner\b|\bzone\b", source, flags=re.IGNORECASE)

    operated_tables = set(
        re.findall(r'op\.(?:create_table|drop_table)\(\s*"([^"]+)"', source)
    )
    assert operated_tables
    assert all(table.startswith("quiz_") for table in operated_tables)

    for table_name, table in Base.metadata.tables.items():
        if not table_name.startswith("quiz_"):
            continue
        expected_names = {
            item.name
            for item in (*table.constraints, *table.indexes)
            if item.name is not None
        }
        missing = sorted(name for name in expected_names if name not in source)
        assert not missing, f"migration is missing {table_name} objects: {missing}"


def test_quiz_settings_are_frozen_and_production_requires_private_oss() -> None:
    valid = _production_settings()
    assert valid.QUIZ_EXAM_DURATION_SECONDS == 3600
    assert valid.QUIZ_IMPORT_MAX_FILE_BYTES == 10 * 1024 * 1024
    assert valid.QUIZ_IMPORT_MAX_QUESTIONS == 5000
    assert valid.QUIZ_IMPORT_RETENTION_DAYS == 7

    with pytest.raises(ValidationError, match="must remain 120"):
        _production_settings(QUIZ_ANSWER_SAVE_RATE_PER_MINUTE=121)
    with pytest.raises(ValidationError, match="QUIZ_IMPORT_STORAGE_TYPE"):
        _production_settings(QUIZ_IMPORT_STORAGE_TYPE="local")
    with pytest.raises(ValidationError, match="QUIZ_OSS_BUCKET"):
        _production_settings(QUIZ_OSS_BUCKET="")
    with pytest.raises(ValidationError, match="REDIS_URL"):
        _production_settings(REDIS_URL="http://redis.internal")


@pytest.mark.asyncio
async def test_quiz_task_registry_runs_all_processors_and_isolates_failures() -> None:
    registry = QuizTaskRegistry()
    calls: list[str] = []

    async def first() -> bool:
        calls.append("first")
        return True

    async def broken() -> bool:
        calls.append("broken")
        raise RuntimeError("expected")

    async def last() -> bool:
        calls.append("last")
        return False

    registry.register("first", first)
    registry.register("broken", broken)
    registry.register("last", last)
    assert registry.names == ("first", "broken", "last")
    assert await registry.run_once() is True
    assert calls == ["first", "broken", "last"]
    with pytest.raises(ValueError, match="already registered"):
        registry.register("first", first)
