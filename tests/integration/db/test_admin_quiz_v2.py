"""PostgreSQL integration tests for the fixed quiz-library V2 foundation."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
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
from app.port.exceptions import BusinessException, ConflictException, ValidationException
from app.schemas.admin_quiz_contract import (
    AdminQuizBatchRequest,
    AdminQuizBatchTarget,
    AdminQuizContentStatusUpdate,
    AdminQuizKnowledgePointCreate,
    AdminQuizLibraryAccessModeConvert,
    AdminQuizLibraryCreate,
    AdminQuizLibraryStatusUpdate,
    AdminQuizLibraryUpdate,
    AdminQuizModuleCreate,
    AdminQuizQuestionCreate,
    AdminQuizQuestionUpdate,
    AdminQuizStatsQuestionQuery,
    AdminQuizVersionRequest,
)
from app.services.admin_quiz import AdminQuizService
from app.services.admin_quiz_v2 import AdminQuizV2Service


pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


def _database_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    return url


@pytest.fixture
async def quiz_v2_env(monkeypatch):
    engine = create_async_engine(_database_url(), pool_size=3, max_overflow=2)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"qv2_{uuid4().hex[:12]}"

    @asynccontextmanager
    async def test_db_ctx():
        async with factory() as session:
            yield session

    monkeypatch.setattr("app.services.admin_quiz_v2.get_db_ctx", test_db_ctx)
    monkeypatch.setattr("app.services.admin_quiz.get_db_ctx", test_db_ctx)
    async with factory() as db:
        admin = AdminUser(
            username=f"{prefix}_admin",
            password_hash="integration-test-only",
            role="super_admin",
        )
        db.add(admin)
        await db.commit()
        await db.refresh(admin)

    env = SimpleNamespace(
        service=AdminQuizV2Service(),
        stats_service=AdminQuizService(),
        factory=factory,
        prefix=prefix,
        admin_id=admin.id,
    )
    try:
        yield env
    finally:
        async with factory() as db:
            library_ids = list(
                (
                    await db.execute(
                        select(QuizLibrary.id).where(QuizLibrary.name.like(f"{prefix}%"))
                    )
                ).scalars()
            )
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
                await db.execute(delete(QuizLibrary).where(QuizLibrary.id.in_(library_ids)))
            await db.execute(delete(AdminUser).where(AdminUser.id == admin.id))
            await db.commit()
        await engine.dispose()


async def test_v2_aggregate_stats_exclude_deleted_content_and_use_fixed_path(
    quiz_v2_env,
) -> None:
    env = quiz_v2_env
    baseline = await env.stats_service.get_stats_overview()

    library = await env.service.create_library(
        AdminQuizLibraryCreate(name=f"{env.prefix}统计题库"),
        admin_id=env.admin_id,
    )
    module = await env.service.create_module(
        AdminQuizModuleCreate(library_id=library.id, name="统计模块"),
        admin_id=env.admin_id,
    )
    point = await env.service.create_knowledge_point(
        AdminQuizKnowledgePointCreate(module_id=module.id, name="统计知识点"),
        admin_id=env.admin_id,
    )
    visible_question = await env.service.create_question(
        AdminQuizQuestionCreate(
            knowledge_point_id=point.id,
            question_type="single_choice",
            question_text=f"{env.prefix} 可见统计题？",
            options={"A": "是", "B": "否"},
            correct_answer="A",
        ),
        admin_id=env.admin_id,
    )
    deleted_question = await env.service.create_question(
        AdminQuizQuestionCreate(
            knowledge_point_id=point.id,
            question_type="judge",
            question_text=f"{env.prefix} 已删除统计题？",
            options={"A": "正确", "B": "错误"},
            correct_answer="A",
        ),
        admin_id=env.admin_id,
    )
    deleted_question = await env.service.transition_question(
        deleted_question.id,
        AdminQuizVersionRequest(lock_version=deleted_question.lock_version),
        "delete",
        admin_id=env.admin_id,
    )

    deleted_module = await env.service.create_module(
        AdminQuizModuleCreate(library_id=library.id, name="已删除模块"),
        admin_id=env.admin_id,
    )
    deleted_point = await env.service.create_knowledge_point(
        AdminQuizKnowledgePointCreate(
            module_id=deleted_module.id,
            name="已删除知识点",
        ),
        admin_id=env.admin_id,
    )

    hidden_library = await env.service.create_library(
        AdminQuizLibraryCreate(name=f"{env.prefix}已删除含题题库"),
        admin_id=env.admin_id,
    )
    hidden_module = await env.service.create_module(
        AdminQuizModuleCreate(library_id=hidden_library.id, name="隐藏模块"),
        admin_id=env.admin_id,
    )
    hidden_point = await env.service.create_knowledge_point(
        AdminQuizKnowledgePointCreate(module_id=hidden_module.id, name="隐藏知识点"),
        admin_id=env.admin_id,
    )
    hidden_question = await env.service.create_question(
        AdminQuizQuestionCreate(
            knowledge_point_id=hidden_point.id,
            question_type="single_choice",
            question_text=f"{env.prefix} 已删除题库中的题？",
            options={"A": "是", "B": "否"},
            correct_answer="A",
        ),
        admin_id=env.admin_id,
    )
    empty_deleted_library = await env.service.create_library(
        AdminQuizLibraryCreate(name=f"{env.prefix}已删除空题库"),
        admin_id=env.admin_id,
    )

    now = datetime.now(timezone.utc)
    async with env.factory() as db:
        persisted_module = await db.get(QuizModule, deleted_module.id)
        persisted_point = await db.get(QuizKnowledgePoint, deleted_point.id)
        persisted_hidden_library = await db.get(QuizLibrary, hidden_library.id)
        persisted_empty_library = await db.get(
            QuizLibrary, empty_deleted_library.id
        )
        for item in (persisted_module, persisted_point):
            item.status = "deleted"
            item.disabled_at = now
            item.deleted_at = now
            item.restore_until = now + timedelta(days=7)
        for item in (persisted_hidden_library, persisted_empty_library):
            item.status = "deleted"
            item.archived_at = now
            item.deleted_at = now
            item.restore_until = now + timedelta(days=7)
        await db.commit()

    overview = await env.stats_service.get_stats_overview()
    assert overview.library_count == baseline.library_count + 1
    assert overview.draft_library_count == baseline.draft_library_count + 1
    assert overview.module_count == baseline.module_count + 1
    assert overview.active_module_count == baseline.active_module_count + 1
    assert overview.knowledge_point_count == baseline.knowledge_point_count + 1
    assert (
        overview.active_knowledge_point_count
        == baseline.active_knowledge_point_count + 1
    )
    assert overview.question_count == baseline.question_count + 1
    assert overview.draft_question_count == baseline.draft_question_count + 1

    path_query = AdminQuizStatsQuestionQuery(
        library_id=library.id,
        module_id=module.id,
        knowledge_point_id=point.id,
        keyword=env.prefix,
        page=1,
        page_size=10,
    )
    page = await env.stats_service.list_question_stats(path_query)
    assert page.total == 1
    item = page.items[0]
    assert item.question_id == visible_question.id
    assert (item.library_id, item.library_name) == (library.id, library.name)
    assert (item.module_id, item.module_name) == (module.id, module.name)
    assert (item.knowledge_point_id, item.knowledge_point_name) == (
        point.id,
        point.name,
    )

    deleted_page = await env.stats_service.list_question_stats(
        AdminQuizStatsQuestionQuery(
            library_id=library.id,
            status="deleted",
            page=1,
            page_size=10,
        )
    )
    assert deleted_page.total == 1
    assert deleted_page.items[0].question_id == deleted_question.id

    all_page = await env.stats_service.list_question_stats(
        AdminQuizStatsQuestionQuery(
            library_id=library.id,
            include_deleted=True,
            page=1,
            page_size=10,
        )
    )
    assert {item.question_id for item in all_page.items} == {
        visible_question.id,
        deleted_question.id,
    }
    hidden_page = await env.stats_service.list_question_stats(
        AdminQuizStatsQuestionQuery(
            library_id=hidden_library.id,
            include_deleted=True,
            page=1,
            page_size=10,
        )
    )
    assert hidden_page.total == 0
    assert hidden_question.id not in {item.question_id for item in hidden_page.items}


async def test_fixed_hierarchy_uniqueness_versions_and_bottom_up_delete(quiz_v2_env) -> None:
    env = quiz_v2_env
    library = await env.service.create_library(
        AdminQuizLibraryCreate(name=f"{env.prefix}题库"),
        admin_id=env.admin_id,
    )
    assert library.library_code.startswith("QL")
    assert library.status == "draft"
    assert library.access_mode == "access_mode_pending"
    assert library.v2_enabled is False

    module = await env.service.create_module(
        AdminQuizModuleCreate(library_id=library.id, name="基础模块"),
        admin_id=env.admin_id,
    )
    with pytest.raises(ValidationException, match="模块名称"):
        await env.service.create_module(
            AdminQuizModuleCreate(library_id=library.id, name="基础模块"),
            admin_id=env.admin_id,
        )
    point = await env.service.create_knowledge_point(
        AdminQuizKnowledgePointCreate(module_id=module.id, name="OSI 模型"),
        admin_id=env.admin_id,
    )
    tree = await env.service.get_content_tree(library.id)
    assert tree.modules[0].knowledge_points[0].id == point.id

    with pytest.raises(ConflictException):
        await env.service.update_library(
            library.id,
            AdminQuizLibraryUpdate(lock_version=999, description="stale"),
            admin_id=env.admin_id,
        )
    with pytest.raises(BusinessException, match="停用"):
        await env.service.delete_knowledge_point(
            point.id,
            AdminQuizVersionRequest(lock_version=point.lock_version),
            admin_id=env.admin_id,
        )
    point = await env.service.set_knowledge_point_status(
        point.id,
        AdminQuizContentStatusUpdate(
            status="disabled", lock_version=point.lock_version
        ),
        admin_id=env.admin_id,
    )
    await env.service.delete_knowledge_point(
        point.id,
        AdminQuizVersionRequest(lock_version=point.lock_version),
        admin_id=env.admin_id,
    )
    with pytest.raises(BusinessException, match="停用"):
        await env.service.delete_module(
            module.id,
            AdminQuizVersionRequest(lock_version=module.lock_version),
            admin_id=env.admin_id,
        )
    module = await env.service.set_module_status(
        module.id,
        AdminQuizContentStatusUpdate(
            status="disabled", lock_version=module.lock_version
        ),
        admin_id=env.admin_id,
    )
    await env.service.delete_module(
        module.id,
        AdminQuizVersionRequest(lock_version=module.lock_version),
        admin_id=env.admin_id,
    )

    async with env.factory() as db:
        persisted_point = await db.get(QuizKnowledgePoint, point.id)
        persisted_module = await db.get(QuizModule, module.id)
        assert persisted_point.status == "deleted"
        assert persisted_point.restore_until is not None
        assert persisted_module.status == "deleted"
        assert persisted_module.restore_until is not None


async def test_library_access_mode_conversion_versions_audit_and_transition_gate(
    quiz_v2_env,
) -> None:
    env = quiz_v2_env
    library = await env.service.create_library(
        AdminQuizLibraryCreate(name=f"{env.prefix}访问模式转换"),
        admin_id=env.admin_id,
    )

    with pytest.raises(ValidationException, match="不能转换"):
        await env.service.convert_library_access_mode(
            library.id,
            AdminQuizLibraryAccessModeConvert(
                lock_version=library.lock_version,
                target_mode="access_mode_pending",
            ),
            admin_id=env.admin_id,
        )

    converted = await env.service.convert_library_access_mode(
        library.id,
        AdminQuizLibraryAccessModeConvert(
            lock_version=library.lock_version,
            target_mode="free",
        ),
        admin_id=env.admin_id,
    )
    assert converted.library.access_mode == "free"
    assert converted.library.lock_version == library.lock_version + 1
    assert converted.sessions_affected == 0

    with pytest.raises(ConflictException):
        await env.service.convert_library_access_mode(
            library.id,
            AdminQuizLibraryAccessModeConvert(
                lock_version=library.lock_version,
                target_mode="course_entitlement",
            ),
            admin_id=env.admin_id,
        )

    async with env.factory() as db:
        audit = (
            await db.execute(
                select(QuizAdminAuditLog).where(
                    QuizAdminAuditLog.admin_id == env.admin_id,
                    QuizAdminAuditLog.object_id == library.id,
                    QuizAdminAuditLog.action == "library.convert_access_mode",
                )
            )
        ).scalar_one()
        assert audit.permission == "quiz_library_manage"
        assert audit.changed_fields["access_mode"]["before"] == "access_mode_pending"
        assert audit.changed_fields["access_mode"]["after"] == "free"


async def test_library_publication_gate_and_safe_delete_restore(quiz_v2_env) -> None:
    env = quiz_v2_env
    library = await env.service.create_library(
        AdminQuizLibraryCreate(name=f"{env.prefix}发布题库"),
        admin_id=env.admin_id,
    )
    with pytest.raises(BusinessException, match="封面"):
        await env.service.transition_library(
            library.id,
            AdminQuizLibraryStatusUpdate(
                action="publish", lock_version=library.lock_version
            ),
            admin_id=env.admin_id,
        )

    # Archive/delete rules are exercised without bypassing the publication
    # gate by moving the otherwise empty fixture to a suspended historical
    # state inside the test transaction.
    async with env.factory() as db:
        persisted = await db.get(QuizLibrary, library.id)
        persisted.status = "suspended"
        persisted.published_at = persisted.created_at
        persisted.suspended_at = persisted.created_at
        await db.commit()
        await db.refresh(persisted)
        version = persisted.lock_version

    archived = await env.service.transition_library(
        library.id,
        AdminQuizLibraryStatusUpdate(action="archive", lock_version=version),
        admin_id=env.admin_id,
    )
    deleted = await env.service.transition_library(
        library.id,
        AdminQuizLibraryStatusUpdate(
            action="delete", lock_version=archived.lock_version
        ),
        admin_id=env.admin_id,
    )
    assert deleted.restore_until is not None
    restored = await env.service.transition_library(
        library.id,
        AdminQuizLibraryStatusUpdate(
            action="undo_delete", lock_version=deleted.lock_version
        ),
        admin_id=env.admin_id,
    )
    assert restored.status == "archived"
    assert restored.v2_enabled is False


async def test_migration_recheck_resolves_reorganized_and_deleted_questions(
    quiz_v2_env,
) -> None:
    env = quiz_v2_env
    library = await env.service.create_library(
        AdminQuizLibraryCreate(name=f"{env.prefix}迁移复检题库"),
        admin_id=env.admin_id,
    )
    async with env.factory() as db:
        persisted = await db.get(QuizLibrary, library.id)
        persisted.migration_state = "needs_organization"
        holding_module = QuizModule(
            library_id=library.id,
            name="待整理",
            normalized_name=f"{env.prefix}_pending",
            status="active",
            system_kind="pending_organization",
            sort_order=999,
            created_by=env.admin_id,
            updated_by=env.admin_id,
        )
        db.add(holding_module)
        await db.flush()
        holding_point = QuizKnowledgePoint(
            library_id=library.id,
            module_id=holding_module.id,
            name="未分类",
            normalized_name=f"{env.prefix}_uncategorized",
            status="active",
            system_kind="uncategorized",
            sort_order=999,
            created_by=env.admin_id,
            updated_by=env.admin_id,
        )
        db.add(holding_point)
        await db.commit()
        await db.refresh(holding_point)

    normal_module = await env.service.create_module(
        AdminQuizModuleCreate(library_id=library.id, name="正式模块"),
        admin_id=env.admin_id,
    )
    normal_point = await env.service.create_knowledge_point(
        AdminQuizKnowledgePointCreate(
            module_id=normal_module.id,
            name="正式知识点",
        ),
        admin_id=env.admin_id,
    )
    moved_question = await env.service.create_question(
        AdminQuizQuestionCreate(
            knowledge_point_id=holding_point.id,
            question_type="single_choice",
            question_text="迁移后需要重新归类的题目？",
            options={"A": "是", "B": "否"},
            correct_answer="A",
            explanation="用于迁移复检测试。",
        ),
        admin_id=env.admin_id,
    )
    deleted_question = await env.service.create_question(
        AdminQuizQuestionCreate(
            knowledge_point_id=holding_point.id,
            question_type="single_choice",
            question_text="迁移后决定删除的题目？",
            options={"A": "保留", "B": "删除"},
            correct_answer="B",
            explanation="用于迁移复检测试。",
        ),
        admin_id=env.admin_id,
    )
    async with env.factory() as db:
        db.add_all(
            [
                QuizMigrationIssue(
                    library_id=library.id,
                    severity="blocking",
                    status="open",
                    issue_code="question_attached_to_library",
                    legacy_object_type="question",
                    legacy_id=moved_question.id,
                    original_path=[{"id": 1, "name": "旧一级分类"}],
                    resolution="请人工归类",
                ),
                QuizMigrationIssue(
                    library_id=library.id,
                    severity="blocking",
                    status="open",
                    issue_code="question_attached_to_module",
                    legacy_object_type="question",
                    legacy_id=deleted_question.id,
                    original_path=[{"id": 2, "name": "旧二级分类"}],
                    resolution="请人工归类或删除",
                ),
            ]
        )
        await db.commit()

    current = await env.service.get_library(library.id)
    assert current.open_migration_issue_count == 2
    unchanged = await env.service.transition_library(
        library.id,
        AdminQuizLibraryStatusUpdate(
            action="reconcile_migration",
            lock_version=current.lock_version,
        ),
        admin_id=env.admin_id,
    )
    assert unchanged.open_migration_issue_count == 2
    assert unchanged.migration_state == "needs_organization"

    moved_question = await env.service.update_question(
        moved_question.id,
        AdminQuizQuestionUpdate(
            lock_version=moved_question.lock_version,
            knowledge_point_id=normal_point.id,
        ),
        admin_id=env.admin_id,
    )
    await env.service.transition_question(
        deleted_question.id,
        AdminQuizVersionRequest(lock_version=deleted_question.lock_version),
        "delete",
        admin_id=env.admin_id,
    )
    reconciled = await env.service.transition_library(
        library.id,
        AdminQuizLibraryStatusUpdate(
            action="reconcile_migration",
            lock_version=unchanged.lock_version,
        ),
        admin_id=env.admin_id,
    )
    assert moved_question.knowledge_point_id == normal_point.id
    assert reconciled.open_migration_issue_count == 0
    assert reconciled.migration_state == "ready"
    report = await env.service.migration_report()
    assert not any(issue.library_id == library.id for issue in report.issues)

    async with env.factory() as db:
        issues = list(
            (
                await db.execute(
                    select(QuizMigrationIssue).where(
                        QuizMigrationIssue.library_id == library.id
                    )
                )
            ).scalars()
        )
        assert {issue.status for issue in issues} == {"resolved"}
        assert all(issue.resolved_at is not None for issue in issues)


async def test_question_revisions_publish_without_overwriting_live_content(
    quiz_v2_env,
) -> None:
    env = quiz_v2_env
    library = await env.service.create_library(
        AdminQuizLibraryCreate(name=f"{env.prefix}版本题库"),
        admin_id=env.admin_id,
    )
    module = await env.service.create_module(
        AdminQuizModuleCreate(library_id=library.id, name="网络基础"),
        admin_id=env.admin_id,
    )
    point = await env.service.create_knowledge_point(
        AdminQuizKnowledgePointCreate(module_id=module.id, name="OSI 模型"),
        admin_id=env.admin_id,
    )
    question = await env.service.create_question(
        AdminQuizQuestionCreate(
            knowledge_point_id=point.id,
            question_type="single_choice",
            question_text="OSI 模型共有几层？",
            options={"A": "五层", "B": "六层", "C": "七层"},
            correct_answer="C",
            explanation="OSI 参考模型共有七层。",
        ),
        admin_id=env.admin_id,
    )
    assert question.status == "draft"
    assert question.pending_revision_no == 1
    assert question.current_revision_id is None

    published = await env.service.publish_question_revision(
        question.id,
        AdminQuizVersionRequest(lock_version=question.lock_version),
        admin_id=env.admin_id,
    )
    assert published.status == "published"
    assert published.current_revision_no == 1
    assert published.pending_revision_id is None

    edited = await env.service.update_question(
        question.id,
        AdminQuizQuestionUpdate(
            lock_version=published.lock_version,
            question_text="OSI 参考模型共有几层？",
        ),
        admin_id=env.admin_id,
    )
    assert edited.question_text == "OSI 模型共有几层？"
    assert edited.current_revision_no == 1
    assert edited.pending_revision_no == 2
    revisions = await env.service.list_question_revisions(question.id)
    assert [(item.revision_no, item.status) for item in revisions] == [
        (2, "draft"),
        (1, "published"),
    ]

    switched = await env.service.publish_question_revision(
        question.id,
        AdminQuizVersionRequest(lock_version=edited.lock_version),
        admin_id=env.admin_id,
    )
    assert switched.question_text == "OSI 参考模型共有几层？"
    assert switched.current_revision_no == 2

    disabled = await env.service.transition_question(
        question.id,
        AdminQuizVersionRequest(lock_version=switched.lock_version),
        "disable",
        admin_id=env.admin_id,
    )
    disabled_edit = await env.service.update_question(
        question.id,
        AdminQuizQuestionUpdate(
            lock_version=disabled.lock_version,
            explanation="OSI 是七层参考模型。",
        ),
        admin_id=env.admin_id,
    )
    disabled_publish = await env.service.publish_question_revision(
        question.id,
        AdminQuizVersionRequest(lock_version=disabled_edit.lock_version),
        admin_id=env.admin_id,
    )
    assert disabled_publish.status == "disabled"
    assert disabled_publish.current_revision_no == 3

    deleted = await env.service.transition_question(
        question.id,
        AdminQuizVersionRequest(lock_version=disabled_publish.lock_version),
        "delete",
        admin_id=env.admin_id,
    )
    assert deleted.status == "deleted"
    assert deleted.restore_until is not None
    restored = await env.service.transition_question(
        question.id,
        AdminQuizVersionRequest(lock_version=deleted.lock_version),
        "undo_delete",
        admin_id=env.admin_id,
    )
    assert restored.status == "disabled"


async def test_published_library_cannot_lose_its_last_effective_content_path(
    quiz_v2_env,
) -> None:
    env = quiz_v2_env
    library = await env.service.create_library(
        AdminQuizLibraryCreate(
            name=f"{env.prefix}有效路径保护",
            description="用于验证已发布题库不能被普通内容操作掏空。",
            cover_url="https://example.invalid/library.png",
            access_mode="free",
        ),
        admin_id=env.admin_id,
    )

    modules = []
    points = []
    questions = []
    for index in (1, 2):
        module = await env.service.create_module(
            AdminQuizModuleCreate(
                library_id=library.id, name=f"模块 {index}"
            ),
            admin_id=env.admin_id,
        )
        point = await env.service.create_knowledge_point(
            AdminQuizKnowledgePointCreate(
                module_id=module.id, name=f"知识点 {index}"
            ),
            admin_id=env.admin_id,
        )
        question = await env.service.create_question(
            AdminQuizQuestionCreate(
                knowledge_point_id=point.id,
                question_type="single_choice",
                question_text=f"有效路径保护题目 {index}？",
                    options={"A": "是", "B": "否", "C": "不确定"},
                correct_answer="A",
                explanation="用于集成测试。",
            ),
            admin_id=env.admin_id,
        )
        question = await env.service.publish_question_revision(
            question.id,
            AdminQuizVersionRequest(lock_version=question.lock_version),
            admin_id=env.admin_id,
        )
        modules.append(module)
        points.append(point)
        questions.append(question)

    library = await env.service.transition_library(
        library.id,
        AdminQuizLibraryStatusUpdate(
            action="publish", lock_version=library.lock_version
        ),
        admin_id=env.admin_id,
    )
    assert library.status == "published"

    questions[0] = await env.service.transition_question(
        questions[0].id,
        AdminQuizVersionRequest(lock_version=questions[0].lock_version),
        "disable",
        admin_id=env.admin_id,
    )
    with pytest.raises(BusinessException, match="至少一条有效"):
        await env.service.transition_question(
            questions[1].id,
            AdminQuizVersionRequest(lock_version=questions[1].lock_version),
            "disable",
            admin_id=env.admin_id,
        )
    questions[0] = await env.service.transition_question(
        questions[0].id,
        AdminQuizVersionRequest(lock_version=questions[0].lock_version),
        "restore",
        admin_id=env.admin_id,
    )

    points[0] = await env.service.set_knowledge_point_status(
        points[0].id,
        AdminQuizContentStatusUpdate(
            status="disabled", lock_version=points[0].lock_version
        ),
        admin_id=env.admin_id,
    )
    with pytest.raises(BusinessException, match="先停用题库"):
        await env.service.set_knowledge_point_status(
            points[1].id,
            AdminQuizContentStatusUpdate(
                status="disabled", lock_version=points[1].lock_version
            ),
            admin_id=env.admin_id,
        )
    points[0] = await env.service.set_knowledge_point_status(
        points[0].id,
        AdminQuizContentStatusUpdate(
            status="active", lock_version=points[0].lock_version
        ),
        admin_id=env.admin_id,
    )

    modules[0] = await env.service.set_module_status(
        modules[0].id,
        AdminQuizContentStatusUpdate(
            status="disabled", lock_version=modules[0].lock_version
        ),
        admin_id=env.admin_id,
    )
    with pytest.raises(BusinessException, match="至少一条有效"):
        await env.service.set_module_status(
            modules[1].id,
            AdminQuizContentStatusUpdate(
                status="disabled", lock_version=modules[1].lock_version
            ),
            admin_id=env.admin_id,
        )
    modules[0] = await env.service.set_module_status(
        modules[0].id,
        AdminQuizContentStatusUpdate(
            status="active", lock_version=modules[0].lock_version
        ),
        admin_id=env.admin_id,
    )

    result = await env.service.batch_transition_questions(
        AdminQuizBatchRequest(
            items=[
                AdminQuizBatchTarget(
                    question_id=question.id, lock_version=question.lock_version
                )
                for question in questions
            ]
        ),
        "disable",
        admin_id=env.admin_id,
    )
    assert result.succeeded is False
    assert result.updated_count == 0
    assert {item.question_id for item in result.errors} == {
        question.id for question in questions
    }
    async with env.factory() as db:
        persisted = list(
            (
                await db.execute(
                    select(QuizQuestion).where(
                        QuizQuestion.id.in_([question.id for question in questions])
                    )
                )
            ).scalars()
        )
        assert {question.status for question in persisted} == {"published"}
