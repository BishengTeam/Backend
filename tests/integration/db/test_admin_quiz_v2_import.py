"""V2 fixed-path import validation and atomic confirmation."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.community.src.index import (
    QuizImportJob,
    QuizKnowledgePoint,
    QuizLibrary,
    QuizModule,
    QuizQuestion,
    QuizQuestionRevision,
)
from app.domain.user.src.index import AdminUser
from app.port.config import settings
from app.schemas.admin_quiz_contract import AdminQuizLibraryCreate
from app.services.admin_quiz import AdminQuizService
from app.services.admin_quiz_v2 import AdminQuizV2Service


pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


async def test_v2_import_imports_duplicate_stems_verbatim(
    monkeypatch, tmp_path: Path
) -> None:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(url, pool_size=2, max_overflow=1)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"qv2dup_{uuid4().hex[:10]}"

    @asynccontextmanager
    async def db_ctx():
        async with factory() as session:
            yield session

    async def inline(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("app.services.admin_quiz.get_db_ctx", db_ctx)
    monkeypatch.setattr("app.services.admin_quiz_v2.get_db_ctx", db_ctx)
    monkeypatch.setattr("app.services.admin_quiz.asyncio.to_thread", inline)
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "QUIZ_IMPORT_STORAGE_TYPE", "local")
    async with factory() as db:
        admin = AdminUser(
            username=f"{prefix}_admin",
            password_hash="test-only",
            role="super_admin",
        )
        db.add(admin)
        await db.commit()
        await db.refresh(admin)

    library = await AdminQuizV2Service().create_library(
        AdminQuizLibraryCreate(name=f"{prefix}题库"), admin_id=admin.id
    )
    service = AdminQuizService()
    questions = [
        {
            "category_path": [f"{prefix}模块", f"{prefix}知识点"],
            "question_type": "single_choice",
            "question_text": f"{prefix}重复题",
            "options": {"A": "甲", "B": "乙"},
            "correct_answer": "A",
        },
        {
            "category_path": [f"{prefix}模块", f"{prefix}知识点"],
            "question_type": "single_choice",
            "question_text": f"{prefix}重复题",  # duplicate stem, different answer
            "options": {"A": "甲", "B": "乙"},
            "correct_answer": "B",
        },
        {
            "category_path": [f"{prefix}模块", f"{prefix}知识点"],
            "question_type": "single_choice",
            "question_text": f"{prefix}唯一题",
            "options": {"A": "甲", "B": "乙"},
            "correct_answer": "A",
        },
    ]
    job = await service.create_import_job(
        source_type="json",
        content=json.dumps({"questions": questions}, ensure_ascii=False).encode(),
        admin_id=admin.id,
        filename="v2-dup.json",
        library_id=library.id,
    )
    assert await service.process_import_job(job.id) is True

    async with factory() as db:
        persisted = await db.get(QuizImportJob, job.id)
        assert persisted.status == "succeeded"
        assert persisted.error_count == 0
        assert persisted.total_rows == 3
        # 2026-10-22（修订）：行行入库，同题干的两行各自成题。
        assert persisted.created_count == 3

        module = (
            await db.execute(
                select(QuizModule).where(QuizModule.library_id == library.id)
            )
        ).scalar_one()
        created = list(
            (
                await db.execute(
                    select(QuizQuestion).where(
                        QuizQuestion.library_id == library.id
                    )
                )
            ).scalars()
        )
        assert len(created) == 3
        same_stem = [
            item for item in created if item.question_text == f"{prefix}重复题"
        ]
        assert len(same_stem) == 2
        assert {item.correct_answer for item in same_stem} == {"A", "B"}

        # Cleanup
        for item in created:
            item.pending_revision_id = None
            item.current_revision_id = None
        await db.flush()
        await db.execute(
            text("DELETE FROM quiz_question_revision WHERE question_id = ANY(:ids)"),
            {"ids": [item.id for item in created]},
        )
        await db.execute(
            text("DELETE FROM quiz_question WHERE library_id = :library_id"),
            {"library_id": library.id},
        )
        await db.execute(
            text("DELETE FROM quiz_import_error WHERE job_id = :job_id"),
            {"job_id": job.id},
        )
        await db.execute(
            text(
                "DELETE FROM quiz_admin_audit_log WHERE admin_id = :admin_id "
                "OR (object_type = 'import_job' AND object_id = :job_id)"
            ),
            {"admin_id": admin.id, "job_id": job.id},
        )
        await db.execute(
            text("DELETE FROM quiz_import_job WHERE id = :job_id"), {"job_id": job.id}
        )
        await db.execute(
            text("DELETE FROM quiz_knowledge_point WHERE module_id = :id"),
            {"id": module.id},
        )
        await db.execute(
            text("DELETE FROM quiz_module WHERE id = :id"), {"id": module.id}
        )
        await db.execute(
            text("DELETE FROM quiz_library WHERE id = :id"), {"id": library.id}
        )
        await db.execute(
            text("DELETE FROM admin_user WHERE id = :id"), {"id": admin.id}
        )
        await db.commit()
    await engine.dispose()


async def test_v2_import_requires_two_level_path_and_confirms_atomically(
    monkeypatch, tmp_path: Path
) -> None:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(url, pool_size=2, max_overflow=1)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"qv2imp_{uuid4().hex[:10]}"

    @asynccontextmanager
    async def db_ctx():
        async with factory() as session:
            yield session

    async def inline(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("app.services.admin_quiz.get_db_ctx", db_ctx)
    monkeypatch.setattr("app.services.admin_quiz_v2.get_db_ctx", db_ctx)
    monkeypatch.setattr("app.services.admin_quiz.asyncio.to_thread", inline)
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "QUIZ_IMPORT_STORAGE_TYPE", "local")
    async with factory() as db:
        admin = AdminUser(
            username=f"{prefix}_admin",
            password_hash="test-only",
            role="super_admin",
        )
        db.add(admin)
        await db.commit()
        await db.refresh(admin)

    library = await AdminQuizV2Service().create_library(
        AdminQuizLibraryCreate(name=f"{prefix}题库"), admin_id=admin.id
    )
    questions = [
        {
            "category_path": [f"{prefix}模块", f"{prefix}知识点"],
            "question_type": "judge",
            "question_text": f"{prefix}题目{index}",
            "correct_answer": "A",
            "explanation": "正确。",
        }
        for index in range(2)
    ]
    service = AdminQuizService()
    job = await service.create_import_job(
        source_type="json",
        content=json.dumps({"questions": questions}, ensure_ascii=False).encode(),
        admin_id=admin.id,
        filename="v2.json",
        library_id=library.id,
    )
    # 2026-10-22 relaxed flow: missing categories are auto-created, so one
    # processing run goes straight to succeeded without manual confirmation.
    assert await service.process_import_job(job.id) is True

    async with factory() as db:
        persisted = await db.get(QuizImportJob, job.id)
        assert persisted.status == "succeeded"
        assert persisted.created_count == 2
        module = (
            await db.execute(
                select(QuizModule).where(QuizModule.library_id == library.id)
            )
        ).scalar_one()
        point = (
            await db.execute(
                select(QuizKnowledgePoint).where(
                    QuizKnowledgePoint.module_id == module.id
                )
            )
        ).scalar_one()
        created = list(
            (
                await db.execute(
                    select(QuizQuestion).where(
                        QuizQuestion.library_id == library.id,
                        QuizQuestion.knowledge_point_id == point.id,
                    )
                )
            ).scalars()
        )
        assert len(created) == 2
        assert all(item.category_id is None for item in created)
        assert all(item.pending_revision_id is not None for item in created)

        # Cleanup uses SQL for audit/import metadata and then removes immutable
        # revisions after detaching their owning logical questions.
        for item in created:
            item.pending_revision_id = None
            item.current_revision_id = None
        await db.flush()
        await db.execute(
            text("DELETE FROM quiz_question_revision WHERE question_id = ANY(:ids)"),
            {"ids": [item.id for item in created]},
        )
        await db.execute(
            text("DELETE FROM quiz_question WHERE library_id = :library_id"),
            {"library_id": library.id},
        )
        await db.execute(
            text("DELETE FROM quiz_import_error WHERE job_id = :job_id"),
            {"job_id": job.id},
        )
        await db.execute(
            text(
                "DELETE FROM quiz_admin_audit_log WHERE admin_id = :admin_id "
                "OR (object_type = 'import_job' AND object_id = :job_id)"
            ),
            {"admin_id": admin.id, "job_id": job.id},
        )
        await db.execute(
            text("DELETE FROM quiz_import_job WHERE id = :job_id"), {"job_id": job.id}
        )
        await db.execute(
            text("DELETE FROM quiz_knowledge_point WHERE id = :id"), {"id": point.id}
        )
        await db.execute(
            text("DELETE FROM quiz_module WHERE id = :id"), {"id": module.id}
        )
        await db.execute(
            text("DELETE FROM quiz_library WHERE id = :id"), {"id": library.id}
        )
        await db.execute(
            text("DELETE FROM admin_user WHERE id = :id"), {"id": admin.id}
        )
        await db.commit()
    await engine.dispose()
