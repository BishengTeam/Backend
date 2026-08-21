from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.domain.community.src.model.quiz import QuizCategory, QuizQuestion
from app.adapter.logging import client_ip_var, request_id_var
from app.port.exceptions import BusinessException, ConflictException
from app.schemas.admin_quiz import AdminQuizCategoryCreate, AdminQuizQuestionCreate
from app.schemas.admin_quiz_contract import AdminQuizVersionRequest
from app.services.admin_quiz import AdminQuizService


class _FakeDb:
    def __init__(self, *, category=None) -> None:
        self.added: QuizCategory | None = None
        self.category = category

    async def get(self, model, identifier):
        return self.category

    def add(self, value) -> None:
        self.added = value
        value.id = 101

    async def commit(self) -> None:
        return None

    async def refresh(self, value) -> None:
        return None


class _LifecycleDb:
    def __init__(self, question, category) -> None:
        self.question = question
        self.category = category
        self.deleted = None

    async def get(self, model, identifier):
        if model is QuizQuestion:
            return self.question if identifier == self.question.id else None
        if model is QuizCategory:
            return self.category if identifier == self.category.id else None
        return None

    async def commit(self) -> None:
        return None

    async def refresh(self, value) -> None:
        return None

    async def delete(self, value) -> None:
        self.deleted = value


class _ScalarResult:
    def scalar_one_or_none(self):
        return None


class _AuditDb(_FakeDb):
    def __init__(self) -> None:
        super().__init__()
        self.values = []

    def add(self, value) -> None:
        self.values.append(value)
        if isinstance(value, QuizCategory):
            value.id = 101
        else:
            value.id = 202
        self.added = value

    async def execute(self, statement):
        return _ScalarResult()

    async def flush(self) -> None:
        return None


@pytest.mark.asyncio
async def test_create_category_populates_rebuilt_domain_fields(monkeypatch) -> None:
    db = _FakeDb()

    @asynccontextmanager
    async def fake_db_ctx():
        yield db

    monkeypatch.setattr("app.services.admin_quiz.get_db_ctx", fake_db_ctx)

    result = await AdminQuizService().create_category(
        AdminQuizCategoryCreate(name="  网络   基础  "),
        admin_id=7,
    )

    assert db.added is not None
    assert db.added.name == "网络 基础"
    assert db.added.normalized_name == "网络 基础"
    assert db.added.depth == 1
    assert db.added.created_by == 7
    assert db.added.updated_by == 7
    assert result.id == 101


@pytest.mark.asyncio
async def test_create_question_populates_rebuilt_domain_fields(monkeypatch) -> None:
    db = _FakeDb(category=SimpleNamespace(id=12))

    @asynccontextmanager
    async def fake_db_ctx():
        yield db

    monkeypatch.setattr("app.services.admin_quiz.get_db_ctx", fake_db_ctx)

    result = await AdminQuizService().create_question(
        AdminQuizQuestionCreate(
            category_id=12,
            question_type="single_choice",
            question_text="  题干  ",
            options={"A": "一", "B": "二", "C": "三"},
            correct_answer="A",
        ),
        admin_id=7,
    )

    assert db.added is not None
    assert db.added.normalized_question_text == "题干"
    assert len(db.added.question_text_hash) == 64
    assert db.added.status == "draft"
    assert db.added.created_by == 7
    assert db.added.updated_by == 7
    assert result.id == 101


@pytest.mark.asyncio
async def test_create_category_audit_uses_flushed_object_id(monkeypatch) -> None:
    db = _AuditDb()

    @asynccontextmanager
    async def fake_db_ctx():
        yield db

    monkeypatch.setattr("app.services.admin_quiz.get_db_ctx", fake_db_ctx)

    await AdminQuizService().create_category(
        AdminQuizCategoryCreate(name="审计分类"),
        admin_id=7,
    )

    audit = next(value for value in db.values if value.__class__.__name__ == "QuizAdminAuditLog")
    assert audit.object_id == 101
    assert audit.changed_fields["name"]["before"] is None


@pytest.mark.asyncio
async def test_admin_audit_captures_request_context(monkeypatch) -> None:
    db = _AuditDb()
    request_token = request_id_var.set("req-quiz-001")
    ip_token = client_ip_var.set("192.0.2.10")

    @asynccontextmanager
    async def fake_db_ctx():
        yield db

    monkeypatch.setattr("app.services.admin_quiz.get_db_ctx", fake_db_ctx)
    try:
        await AdminQuizService().create_category(
            AdminQuizCategoryCreate(name="上下文审计"),
            admin_id=7,
        )
    finally:
        client_ip_var.reset(ip_token)
        request_id_var.reset(request_token)

    audit = next(value for value in db.values if value.__class__.__name__ == "QuizAdminAuditLog")
    assert audit.request_id == "req-quiz-001"
    assert audit.ip_address == "192.0.2.10"


def _question(*, status: str = "draft", lock_version: int = 1):
    return SimpleNamespace(
        id=11,
        category_id=7,
        question_type="single_choice",
        status=status,
        question_text="题干",
        normalized_question_text="题干",
        question_text_hash="x" * 64,
        options={"A": "一", "B": "二", "C": "三"},
        correct_answer="A",
        explanation="解析",
        image_urls=[],
        ever_published=status != "draft",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc) if status != "draft" else None,
        disabled_at=datetime(2026, 2, 1, tzinfo=timezone.utc) if status == "disabled" else None,
        lock_version=lock_version,
        updated_by=7,
    )


@pytest.mark.asyncio
async def test_draft_can_publish_and_increments_version(monkeypatch) -> None:
    question = _question()
    category = SimpleNamespace(id=7, parent_id=None, status="active")
    db = _LifecycleDb(question, category)

    @asynccontextmanager
    async def fake_db_ctx():
        yield db

    monkeypatch.setattr("app.services.admin_quiz.get_db_ctx", fake_db_ctx)
    result = await AdminQuizService().publish_question(
        question.id,
        AdminQuizVersionRequest(lock_version=1),
        admin_id=7,
    )

    assert result.status == "published"
    assert result.ever_published is True
    assert result.lock_version == 2
    assert result.disabled_at is None


@pytest.mark.asyncio
async def test_draft_can_be_deleted_but_published_cannot(monkeypatch) -> None:
    question = _question()
    db = _LifecycleDb(question, SimpleNamespace(id=7, parent_id=None, status="active"))

    @asynccontextmanager
    async def fake_db_ctx():
        yield db

    monkeypatch.setattr("app.services.admin_quiz.get_db_ctx", fake_db_ctx)
    await AdminQuizService().delete_question(question.id, 1, admin_id=7)
    assert db.deleted is question

    published = _question(status="published")
    db = _LifecycleDb(published, SimpleNamespace(id=7, parent_id=None, status="active"))

    @asynccontextmanager
    async def published_db_ctx():
        yield db

    monkeypatch.setattr("app.services.admin_quiz.get_db_ctx", published_db_ctx)
    with pytest.raises(BusinessException):
        await AdminQuizService().delete_question(published.id, 1, admin_id=7)


@pytest.mark.asyncio
async def test_restore_preserves_last_disabled_timestamp(monkeypatch) -> None:
    question = _question(status="disabled")
    disabled_at = question.disabled_at
    db = _LifecycleDb(question, SimpleNamespace(id=7, parent_id=None, status="active"))

    @asynccontextmanager
    async def fake_db_ctx():
        yield db

    monkeypatch.setattr("app.services.admin_quiz.get_db_ctx", fake_db_ctx)
    result = await AdminQuizService().restore_question(
        question.id,
        AdminQuizVersionRequest(lock_version=1),
        admin_id=7,
    )

    assert result.status == "published"
    assert result.disabled_at == disabled_at


@pytest.mark.asyncio
async def test_transition_rejects_stale_lock_version(monkeypatch) -> None:
    question = _question(lock_version=2)
    db = _LifecycleDb(question, SimpleNamespace(id=7, parent_id=None, status="active"))

    @asynccontextmanager
    async def fake_db_ctx():
        yield db

    monkeypatch.setattr("app.services.admin_quiz.get_db_ctx", fake_db_ctx)
    with pytest.raises(ConflictException):
        await AdminQuizService().publish_question(
            question.id,
            AdminQuizVersionRequest(lock_version=1),
            admin_id=7,
        )
