"""PostgreSQL integration tests for the unified recycle-bin purge policies."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.community.src.index import (
    QuizAdminAuditLog,
    QuizKnowledgePoint,
    QuizLibrary,
    QuizMigrationIssue,
    QuizModule,
    QuizQuestion,
    QuizQuestionRevision,
)
from app.domain.user.src.index import AdminUser
from app.schemas.admin_quiz_contract import (
    AdminQuizKnowledgePointCreate,
    AdminQuizLibraryCreate,
    AdminQuizLibraryStatusUpdate,
    AdminQuizModuleCreate,
    AdminQuizQuestionCreate,
)
from app.services.admin_quiz_v2 import AdminQuizV2Service
from app.services.recycle_bin import TOMBSTONE_TEXT, TRASH_MODULES, RecycleBinService


pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


def _database_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    return url


@pytest.fixture
async def recycle_bin_env(monkeypatch):
    engine = create_async_engine(_database_url(), pool_size=3, max_overflow=2)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"rb_{uuid4().hex[:12]}"

    @asynccontextmanager
    async def test_db_ctx():
        async with factory() as session:
            yield session

    monkeypatch.setattr("app.services.recycle_bin.get_db_ctx", test_db_ctx)
    monkeypatch.setattr("app.services.admin_quiz_v2.get_db_ctx", test_db_ctx)
    async with factory() as db:
        admin = AdminUser(
            username=f"{prefix}_admin",
            password_hash="integration-test-only",
            role="quiz_admin",
        )
        db.add(admin)
        await db.commit()
        await db.refresh(admin)

    env = SimpleNamespace(
        service=AdminQuizV2Service(),
        bin=RecycleBinService(),
        factory=factory,
        prefix=prefix,
        admin_id=admin.id,
        library_ids=[],
    )
    try:
        yield env
    finally:
        # 墓碑行 name 已被改写，不能按前缀清理；按测试登记的 id 逆序删除。
        async with factory() as db:
            library_ids = env.library_ids
            if library_ids:
                await db.execute(
                    delete(QuizAdminAuditLog).where(
                        QuizAdminAuditLog.admin_id == admin.id
                    )
                )
                await db.execute(
                    delete(QuizMigrationIssue).where(
                        QuizMigrationIssue.library_id.in_(library_ids)
                    )
                )
                question_ids = list(
                    (
                        await db.execute(
                            select(QuizQuestion.id).where(
                                QuizQuestion.library_id.in_(library_ids)
                            )
                        )
                    ).scalars()
                )
                if question_ids:
                    questions = list(
                        (
                            await db.execute(
                                select(QuizQuestion).where(
                                    QuizQuestion.id.in_(question_ids)
                                )
                            )
                        ).scalars()
                    )
                    for question in questions:
                        question.current_revision_id = None
                        question.pending_revision_id = None
                    await db.flush()
                    await db.execute(
                        delete(QuizQuestionRevision).where(
                            QuizQuestionRevision.question_id.in_(question_ids)
                        )
                    )
                    await db.execute(
                        delete(QuizQuestion).where(QuizQuestion.id.in_(question_ids))
                    )
                await db.execute(
                    delete(QuizKnowledgePoint).where(
                        QuizKnowledgePoint.library_id.in_(library_ids)
                    )
                )
                await db.execute(
                    delete(QuizModule).where(QuizModule.library_id.in_(library_ids))
                )
                await db.execute(
                    delete(QuizLibrary).where(QuizLibrary.id.in_(library_ids))
                )
            await db.execute(delete(AdminUser).where(AdminUser.id == admin.id))
            await db.commit()
        await engine.dispose()


async def _draft_delete_library(env, library) -> None:
    """未发布题库直接移入回收站，并登记 id 供夹具清理。"""
    deleted = await env.service.transition_library(
        library.id,
        AdminQuizLibraryStatusUpdate(
            action="delete", lock_version=library.lock_version
        ),
        admin_id=env.admin_id,
    )
    assert deleted.status == "deleted"
    env.library_ids.append(library.id)


async def test_purge_empty_draft_library_is_physical(recycle_bin_env) -> None:
    env = recycle_bin_env
    library = await env.service.create_library(
        AdminQuizLibraryCreate(name=f"{env.prefix}空草稿库"),
        admin_id=env.admin_id,
    )
    await _draft_delete_library(env, library)

    result = await env.bin.purge_items(
        TRASH_MODULES["quiz_library"], [library.id]
    )
    assert result == {"physical": 1, "tombstone": 0}

    async with env.factory() as db:
        assert await db.get(QuizLibrary, library.id) is None


async def test_purge_library_with_question_turns_tombstone(recycle_bin_env) -> None:
    env = recycle_bin_env
    library = await env.service.create_library(
        AdminQuizLibraryCreate(name=f"{env.prefix}带题草稿库"),
        admin_id=env.admin_id,
    )
    module = await env.service.create_module(
        AdminQuizModuleCreate(library_id=library.id, name="回收站模块"),
        admin_id=env.admin_id,
    )
    point = await env.service.create_knowledge_point(
        AdminQuizKnowledgePointCreate(module_id=module.id, name="回收站知识点"),
        admin_id=env.admin_id,
    )
    await env.service.create_question(
        AdminQuizQuestionCreate(
            knowledge_point_id=point.id,
            question_type="single_choice",
            question_text=f"{env.prefix} 回收站彻底删除题？",
            options={"A": "是", "B": "否"},
            correct_answer="A",
        ),
        admin_id=env.admin_id,
    )
    await _draft_delete_library(env, library)

    # 子表（question/module/point）对 library 是 RESTRICT：物理删会 IntegrityError，
    # tombstone 策略改为保行清字段。
    result = await env.bin.purge_items(
        TRASH_MODULES["quiz_library"], [library.id]
    )
    assert result == {"physical": 0, "tombstone": 1}

    async with env.factory() as db:
        tombstone = await db.get(QuizLibrary, library.id)
        assert tombstone is not None
        assert tombstone.name == TOMBSTONE_TEXT
        assert tombstone.name_reserved is False
        assert tombstone.deleted_at is not None
        assert tombstone.restore_until is None
