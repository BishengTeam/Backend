"""PostgreSQL coverage for the fixed-hierarchy user catalog and V2 practice."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.certification.src.index import Course, CourseEntitlementJob, CourseEntitlementJobItem
from app.domain.community.src.index import (
    QuizAdminAuditLog,
    QuizCheckin,
    QuizCollection,
    QuizCourseLibraryBinding,
    QuizKnowledgePoint,
    QuizLibrary,
    QuizLibraryEntitlement,
    QuizModule,
    QuizPracticeAttempt,
    QuizPracticeSession,
    QuizPracticeSessionQuestion,
    QuizQuestion,
    QuizQuestionRevision,
    QuizQuestionRevisionStats,
    QuizUserStats,
    QuizWrongItem,
)
from app.domain.order.src.index import Order
from app.domain.plan.src.index import Plan  # noqa: F401 - resolve Order.plan_id mapper FK
from app.domain.user.src.index import AdminUser, User
from app.port.exceptions import ConflictException, NotFoundException, QuizV2Exception
from app.port.config import settings
from app.schemas.admin_quiz_contract import (
    AdminQuizCourseBindingCreate,
    AdminQuizKnowledgePointCreate,
    AdminQuizLibraryCreate,
    AdminQuizLibraryStatusUpdate,
    AdminQuizLibraryUpdate,
    AdminQuizModuleCreate,
    AdminQuizDailyStatsQuery,
    AdminQuizQuestionCreate,
    AdminQuizQuestionUpdate,
    AdminQuizUserPracticeQuery,
    AdminQuizStatsQuestionQuery,
    AdminQuizUserStatsQuery,
    AdminQuizVersionRequest,
)
from app.schemas.quiz_contract import (
    QuizCollectionCreate,
    QuizExamAnswerSave,
    QuizExamCreate,
    QuizPracticeAnswerSave,
    QuizPracticeAttemptCreate,
    QuizPracticeSessionCreate,
    QuizWrongBookQuery,
)
from app.services.admin_quiz_v2 import AdminQuizV2Service
from app.services.admin_quiz import AdminQuizService
from app.services.quiz_exam import QuizExamService
from app.services.quiz_practice import QuizPracticeService
from app.services.quiz_v2 import QuizV2Service


pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


def _database_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    return url


@pytest.fixture
async def quiz_v2_catalog_env(monkeypatch):
    engine = create_async_engine(_database_url(), pool_size=3, max_overflow=2)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"qv2cat_{uuid4().hex[:10]}"

    @asynccontextmanager
    async def db_ctx():
        async with factory() as session:
            yield session

    monkeypatch.setattr("app.services.admin_quiz_v2.get_db_ctx", db_ctx)
    monkeypatch.setattr("app.services.quiz_v2.get_db_ctx", db_ctx)
    monkeypatch.setattr("app.services.quiz_practice.get_db_ctx", db_ctx)
    monkeypatch.setattr("app.services.course_entitlement.get_db_ctx", db_ctx)

    async with factory() as db:
        admin = AdminUser(
            username=f"{prefix}_admin",
            password_hash="test-only",
            role="quiz_admin",
        )
        user = User(openid=f"{prefix}_user", is_active=True)
        course = Course(
            title=f"{prefix}课程",
            category="test",
            cover_storage_key=f"course/{prefix}/cover.jpg",
            price=100,
            preview_chapter_count=1,
            status="published",
            is_active=True,
        )
        db.add_all([admin, user, course])
        await db.flush()
        order = Order(
            user_id=user.id,
            order_kind="course",
            product_type="course",
            price=100,
            status="paid",
            paid_at=datetime.now(timezone.utc),
        )
        db.add(order)
        await db.commit()
        await db.refresh(admin)
        await db.refresh(user)
        await db.refresh(course)
        await db.refresh(order)

    admin_service = AdminQuizV2Service()
    library = await admin_service.create_library(
        AdminQuizLibraryCreate(
            name=f"{prefix}题库",
            access_mode="course_entitlement",
            description="课程附赠题库",
            cover_url="https://example.invalid/cover.png",
        ),
        admin_id=admin.id,
    )
    module = await admin_service.create_module(
        AdminQuizModuleCreate(library_id=library.id, name="模块"),
        admin_id=admin.id,
    )
    point = await admin_service.create_knowledge_point(
        AdminQuizKnowledgePointCreate(module_id=module.id, name="知识点"),
        admin_id=admin.id,
    )
    question = await admin_service.create_question(
        AdminQuizQuestionCreate(
            knowledge_point_id=point.id,
            question_type="judge",
            question_text=f"{prefix} TCP 面向连接。",
            correct_answer="A",
            explanation="正确。",
        ),
        admin_id=admin.id,
    )
    question = await admin_service.publish_question_revision(
        question.id,
        AdminQuizVersionRequest(lock_version=question.lock_version),
        admin_id=admin.id,
    )
    library = await admin_service.transition_library(
        library.id,
        AdminQuizLibraryStatusUpdate(
            action="publish", lock_version=library.lock_version
        ),
        admin_id=admin.id,
    )
    binding = await admin_service.create_course_binding(
        library.id,
        AdminQuizCourseBindingCreate(course_id=course.id),
        admin_id=admin.id,
    )
    library = await admin_service.update_library(
        library.id,
        AdminQuizLibraryUpdate(
            lock_version=library.lock_version,
            v2_enabled=True,
        ),
        admin_id=admin.id,
    )

    env = SimpleNamespace(
        factory=factory,
        prefix=prefix,
        admin_id=admin.id,
        user_id=user.id,
        course_id=course.id,
        order_id=order.id,
        library=library,
        binding=binding,
        module=module,
        point=point,
        question=question,
        admin_service=admin_service,
        user_service=QuizV2Service(),
        practice_service=QuizPracticeService(),
    )
    try:
        yield env
    finally:
        async with factory() as db:
            question_ids = list(
                (
                    await db.execute(
                        select(QuizQuestion.id).where(
                            QuizQuestion.library_id == library.id
                        )
                    )
                ).scalars()
            )
            session_ids = select(QuizPracticeSession.id).where(
                QuizPracticeSession.user_id == user.id,
                QuizPracticeSession.library_id == library.id,
            )
            await db.execute(delete(QuizCheckin).where(QuizCheckin.user_id == user.id))
            await db.execute(
                delete(QuizCollection).where(QuizCollection.user_id == user.id)
            )
            await db.execute(
                delete(QuizPracticeAttempt).where(
                    QuizPracticeAttempt.session_id.in_(session_ids)
                )
            )
            await db.execute(
                delete(QuizPracticeSessionQuestion).where(
                    QuizPracticeSessionQuestion.session_id.in_(session_ids)
                )
            )
            await db.execute(
                delete(QuizPracticeSession).where(
                    QuizPracticeSession.user_id == user.id,
                    QuizPracticeSession.library_id == library.id,
                )
            )
            await db.execute(
                delete(QuizWrongItem).where(
                    QuizWrongItem.user_id == user.id,
                    QuizWrongItem.question_id.in_(question_ids),
                )
            )
            await db.execute(
                delete(QuizUserStats).where(QuizUserStats.user_id == user.id)
            )
            await db.execute(
                delete(CourseEntitlementJobItem).where(
                    CourseEntitlementJobItem.job_id.in_(
                        select(CourseEntitlementJob.id).where(
                            CourseEntitlementJob.library_id == library.id
                        )
                    )
                )
            )
            await db.execute(
                delete(CourseEntitlementJob).where(
                    CourseEntitlementJob.library_id == library.id
                )
            )
            await db.execute(
                delete(QuizLibraryEntitlement).where(
                    QuizLibraryEntitlement.library_id == library.id
                )
            )
            await db.execute(
                delete(QuizCourseLibraryBinding).where(
                    QuizCourseLibraryBinding.library_id == library.id
                )
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
                for persisted_question in questions:
                    persisted_question.current_revision_id = None
                    persisted_question.pending_revision_id = None
                await db.flush()
                await db.execute(
                    delete(QuizQuestionRevisionStats).where(
                        QuizQuestionRevisionStats.question_id.in_(question_ids)
                    )
                )
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
                    QuizKnowledgePoint.library_id == library.id
                )
            )
            await db.execute(
                delete(QuizModule).where(QuizModule.library_id == library.id)
            )
            await db.execute(delete(QuizLibrary).where(QuizLibrary.id == library.id))
            await db.execute(delete(Order).where(Order.id == order.id))
            await db.execute(delete(Course).where(Course.id == course.id))
            await db.execute(delete(User).where(User.id == user.id))
            await db.execute(
                delete(QuizAdminAuditLog).where(QuizAdminAuditLog.admin_id == admin.id)
            )
            await db.execute(delete(AdminUser).where(AdminUser.id == admin.id))
            await db.commit()
        await engine.dispose()


async def _grant_entitlement(env, *, starts_at: datetime | None = None) -> int:
    async with env.factory() as db:
        entitlement = QuizLibraryEntitlement(
            user_id=env.user_id,
            library_id=env.library.id,
            course_id=env.course_id,
            order_id=env.order_id,
            source_type="course_order",
            status="active",
            starts_at=starts_at or datetime.now(timezone.utc),
            snapshot={
                "library_id": env.library.id,
                "library_code": env.library.library_code,
                "name": env.library.name,
            },
        )
        db.add(entitlement)
        await db.commit()
        await db.refresh(entitlement)
        return int(entitlement.id)


async def test_catalog_hides_unentitled_course_library(quiz_v2_catalog_env) -> None:
    env = quiz_v2_catalog_env
    assert await env.user_service.list_libraries(env.user_id) == []
    with pytest.raises(QuizV2Exception) as hidden:
        await env.user_service.get_library(env.user_id, env.library.id)
    assert hidden.value.detail["reason"] == "quiz_library_not_found"
    assert hidden.value.http_status_code == 404

    await _grant_entitlement(env)
    visible = await env.user_service.list_libraries(env.user_id)
    assert [item.id for item in visible] == [env.library.id]
    detail = await env.user_service.get_library(env.user_id, env.library.id)
    assert detail.modules[0].knowledge_points[0].id == env.point.id

    preview = await env.user_service.preview_practice_scope(
        env.user_id, "knowledge_point", env.point.id, "full"
    )
    assert preview.question_count == 1
    assert preview.unfinished_session_id is None
    session = await env.user_service.create_practice_session(
        env.user_id,
        QuizPracticeSessionCreate(
            mode="full",
            scope_type="knowledge_point",
            scope_id=env.point.id,
        ),
    )
    assert session.actual_count == 1
    assert session.category_id is None
    assert session.scope_type == "knowledge_point"
    assert session.questions[0].question_revision_id is not None
    assert session.expires_at is not None
    resumed = await env.user_service.create_practice_session(
        env.user_id,
        QuizPracticeSessionCreate(
            mode="full",
            scope_type="knowledge_point",
            scope_id=env.point.id,
        ),
    )
    assert resumed.id == session.id
    assert resumed.created_new is False
    assert resumed.resume_available is True
    skipped = await env.user_service.skip_practice_question(
        env.user_id, session.id, session.questions[0].session_question_id
    )
    assert skipped.skip_count == 1
    with pytest.raises(ConflictException, match="只能跳过一次"):
        await env.user_service.skip_practice_question(
            env.user_id, session.id, session.questions[0].session_question_id
        )
    replacement = await env.user_service.create_practice_session(
        env.user_id,
        QuizPracticeSessionCreate(
            mode="full",
            scope_type="knowledge_point",
            scope_id=env.point.id,
            restart_existing=True,
        ),
    )
    assert replacement.id != session.id


async def test_scope_preview_reports_last_completed_round(quiz_v2_catalog_env) -> None:
    env = quiz_v2_catalog_env
    await _grant_entitlement(env)
    question = await env.admin_service.create_question(
        AdminQuizQuestionCreate(
            knowledge_point_id=env.point.id,
            question_type="single_choice",
            question_text=f"{env.prefix} 最近一轮题目",
            options={"A": "甲", "B": "乙", "C": "丙"},
            correct_answer="A",
            explanation="甲正确。",
        ),
        admin_id=env.admin_id,
    )
    await env.admin_service.publish_question_revision(
        question.id,
        AdminQuizVersionRequest(lock_version=question.lock_version),
        admin_id=env.admin_id,
    )

    fresh = await env.user_service.preview_practice_scope(
        env.user_id, "module", env.module.id, "full"
    )
    assert fresh.question_count == 2
    assert fresh.last_completed_session is None

    session = await env.user_service.create_practice_session(
        env.user_id,
        QuizPracticeSessionCreate(
            mode="full",
            scope_type="knowledge_point",
            scope_id=env.point.id,
        ),
    )
    assert len(session.questions) == 2
    await env.practice_service.submit_attempt(
        env.user_id,
        session.id,
        QuizPracticeAttemptCreate(
            session_question_id=session.questions[0].session_question_id,
            idempotency_key="last-round-first",
            user_answer="A",
        ),
    )
    await env.practice_service.submit_attempt(
        env.user_id,
        session.id,
        QuizPracticeAttemptCreate(
            session_question_id=session.questions[1].session_question_id,
            idempotency_key="last-round-second",
            user_answer="B",
        ),
    )
    settled = await env.practice_service.submit_session(env.user_id, session.id)
    assert settled.status == "completed"

    preview = await env.user_service.preview_practice_scope(
        env.user_id, "knowledge_point", env.point.id, "full"
    )
    assert preview.last_completed_session is not None
    assert preview.last_completed_session.session_id == session.id
    assert preview.last_completed_session.answered_count == 2
    assert preview.last_completed_session.correct_count == 1
    assert preview.last_completed_session.accuracy == Decimal("50.0")
    assert preview.last_completed_session.completed_at is not None


async def test_practice_saves_full_answer_card_and_grades_only_on_final_submit(
    quiz_v2_catalog_env,
) -> None:
    env = quiz_v2_catalog_env
    await _grant_entitlement(env)
    for index in range(5):
        question = await env.admin_service.create_question(
            AdminQuizQuestionCreate(
                knowledge_point_id=env.point.id,
                question_type="judge",
                question_text=f"{env.prefix} 最终交卷题目 {index}",
                correct_answer="A",
                explanation=f"解析 {index}",
            ),
            admin_id=env.admin_id,
        )
        await env.admin_service.publish_question_revision(
            question.id,
            AdminQuizVersionRequest(lock_version=question.lock_version),
            admin_id=env.admin_id,
        )

    session = await env.user_service.create_practice_session(
        env.user_id,
        QuizPracticeSessionCreate(
            mode="full",
            scope_type="knowledge_point",
            scope_id=env.point.id,
        ),
    )
    assert len(session.questions) == 6
    assert [item.position for item in session.questions] == list(range(1, 7))
    assert all(item.user_answer is None for item in session.questions)
    assert all(item.correct_answer is None for item in session.questions)
    assert all(item.explanation is None for item in session.questions)

    first = session.questions[0]
    last = session.questions[-1]
    first_saved = await env.practice_service.save_answer(
        env.user_id,
        session.id,
        first.session_question_id,
        QuizPracticeAnswerSave(user_answer="A", lock_version=0),
    )
    assert first_saved.lock_version == 1
    last_saved = await env.practice_service.save_answer(
        env.user_id,
        session.id,
        last.session_question_id,
        QuizPracticeAnswerSave(user_answer="B", lock_version=0),
    )
    assert last_saved.lock_version == 1
    with pytest.raises(ConflictException, match="当前版本为 1"):
        await env.practice_service.save_answer(
            env.user_id,
            session.id,
            first.session_question_id,
            QuizPracticeAnswerSave(user_answer="B", lock_version=0),
        )

    in_progress = await env.user_service.get_practice_session(env.user_id, session.id)
    assert len(in_progress.questions) == 6
    assert in_progress.answered_count == 2
    assert in_progress.questions[-1].user_answer == "B"
    assert in_progress.questions[-1].is_correct is None
    assert in_progress.questions[-1].correct_answer is None
    assert in_progress.questions[-1].latest_result is None

    settled = await env.practice_service.submit_session(env.user_id, session.id)
    assert settled.status == "completed"
    assert settled.answered_count == 2
    assert settled.remaining_count == 4
    assert settled.questions[0].is_correct is True
    assert settled.questions[-1].is_correct is False
    assert settled.questions[-1].correct_answer == "A"
    assert settled.questions[1].user_answer is None
    assert settled.questions[1].correct_answer == "A"
    assert settled.questions[1].is_correct is None

    wrong_book = await env.practice_service.list_wrong_book(
        env.user_id, QuizWrongBookQuery()
    )
    assert wrong_book.total == 1
    assert wrong_book.items[0].question.category_id is None
    assert wrong_book.items[0].question.library_id == env.library.id
    await env.practice_service.add_collection(
        env.user_id,
        QuizCollectionCreate(question_id=last.id),
    )
    collections = await env.practice_service.list_collections(
        env.user_id, QuizWrongBookQuery()
    )
    assert collections.total == 1
    assert collections.items[0].question.category_id is None
    assert collections.items[0].question.library_id == env.library.id

    repeated = await env.practice_service.submit_session(env.user_id, session.id)
    assert repeated.status == "completed"
    async with env.factory() as db:
        attempt_count = int(
            (
                await db.execute(
                    select(func.count(QuizPracticeAttempt.id)).where(
                        QuizPracticeAttempt.session_id == session.id
                    )
                )
            ).scalar()
            or 0
        )
        stats = (
            await db.execute(
                select(QuizUserStats).where(QuizUserStats.user_id == env.user_id)
            )
        ).scalar_one()
        assert attempt_count == 2
        assert stats.practice_first_attempts == 2
        assert stats.practice_first_correct == 1


async def test_v2_session_pauses_resumes_and_terminates_with_access_changes(
    quiz_v2_catalog_env,
    monkeypatch,
) -> None:
    env = quiz_v2_catalog_env
    entitlement_id = await _grant_entitlement(env)
    clock = {"now": datetime.now(timezone.utc) + timedelta(minutes=1)}
    monkeypatch.setattr(
        QuizV2Service, "_now", staticmethod(lambda: clock["now"])
    )
    monkeypatch.setattr(
        AdminQuizV2Service, "_now", staticmethod(lambda: clock["now"])
    )
    monkeypatch.setattr(
        QuizPracticeService, "_now", staticmethod(lambda: clock["now"])
    )

    session = await env.user_service.create_practice_session(
        env.user_id,
        QuizPracticeSessionCreate(
            mode="full",
            scope_type="knowledge_point",
            scope_id=env.point.id,
        ),
    )
    assert session.expires_at is not None
    initial_expires_at = session.expires_at
    session_question_id = session.questions[0].session_question_id

    clock["now"] += timedelta(days=1)
    async with env.factory() as db:
        entitlement = await db.get(QuizLibraryEntitlement, entitlement_id)
        entitlement.status = "revoked"
        entitlement.revoked_at = clock["now"]
        await db.flush()
        changed = await QuizV2Service.sync_sessions_for_library(
            db,
            env.library.id,
            user_id=env.user_id,
            now=clock["now"],
        )
        assert changed == 1
        await db.commit()

    paused = await env.user_service.get_practice_session(env.user_id, session.id)
    assert paused.status == "paused"
    assert paused.pause_reason == "quiz_entitlement_inactive"
    with pytest.raises(QuizV2Exception) as blocked:
        await env.practice_service.submit_attempt(
            env.user_id,
            session.id,
            QuizPracticeAttemptCreate(
                session_question_id=session_question_id,
                idempotency_key="paused-attempt-1",
                user_answer="A",
            ),
        )
    assert blocked.value.http_status_code == 423
    assert blocked.value.detail["reason"] == "quiz_entitlement_inactive"

    clock["now"] += timedelta(days=2)
    async with env.factory() as db:
        entitlement = await db.get(QuizLibraryEntitlement, entitlement_id)
        entitlement.status = "active"
        entitlement.revoked_at = None
        await db.flush()
        changed = await QuizV2Service.sync_sessions_for_library(
            db,
            env.library.id,
            user_id=env.user_id,
            now=clock["now"],
        )
        assert changed == 1
        await db.commit()
    resumed = await env.user_service.get_practice_session(env.user_id, session.id)
    assert resumed.status == "in_progress"
    assert resumed.expires_at == initial_expires_at + timedelta(days=2)

    clock["now"] += timedelta(hours=1)
    current_library = await env.admin_service.get_library(env.library.id)
    suspended = await env.admin_service.transition_library(
        env.library.id,
        AdminQuizLibraryStatusUpdate(
            action="suspend", lock_version=current_library.lock_version
        ),
        admin_id=env.admin_id,
    )
    paused_for_library = await env.user_service.get_practice_session(
        env.user_id, session.id
    )
    assert paused_for_library.status == "paused"
    assert paused_for_library.pause_reason == "quiz_library_suspended"

    clock["now"] += timedelta(hours=6)
    restored = await env.admin_service.transition_library(
        env.library.id,
        AdminQuizLibraryStatusUpdate(
            action="restore", lock_version=suspended.lock_version
        ),
        admin_id=env.admin_id,
    )
    still_paused = await env.user_service.get_practice_session(env.user_id, session.id)
    assert still_paused.status == "paused"
    assert restored.v2_enabled is False
    enabled = await env.admin_service.update_library(
        env.library.id,
        AdminQuizLibraryUpdate(
            lock_version=restored.lock_version,
            v2_enabled=True,
        ),
        admin_id=env.admin_id,
    )
    assert enabled.v2_enabled is True
    resumed_again = await env.user_service.get_practice_session(
        env.user_id, session.id
    )
    assert resumed_again.status == "in_progress"
    assert resumed_again.expires_at == resumed.expires_at + timedelta(hours=6)

    clock["now"] += timedelta(hours=1)
    current_library = await env.admin_service.get_library(env.library.id)
    suspended_for_archive = await env.admin_service.transition_library(
        env.library.id,
        AdminQuizLibraryStatusUpdate(
            action="suspend", lock_version=current_library.lock_version
        ),
        admin_id=env.admin_id,
    )
    current_question = await env.admin_service.get_question(env.question.id)
    await env.admin_service.transition_question(
        env.question.id,
        AdminQuizVersionRequest(lock_version=current_question.lock_version),
        "disable",
        admin_id=env.admin_id,
    )
    archived = await env.admin_service.transition_library(
        env.library.id,
        AdminQuizLibraryStatusUpdate(
            action="archive", lock_version=suspended_for_archive.lock_version
        ),
        admin_id=env.admin_id,
    )
    assert archived.status == "archived"
    with pytest.raises(QuizV2Exception) as terminated:
        await env.user_service.get_practice_session(env.user_id, session.id)
    assert terminated.value.http_status_code == 410
    assert terminated.value.detail["reason"] == "practice_session_terminated"


async def test_wrong_only_review_and_revision_stats_follow_frozen_revision(
    quiz_v2_catalog_env,
    monkeypatch,
) -> None:
    env = quiz_v2_catalog_env
    await _grant_entitlement(env)
    clock = {"now": datetime.now(timezone.utc) + timedelta(minutes=1)}
    monkeypatch.setattr(
        QuizV2Service, "_now", staticmethod(lambda: clock["now"])
    )
    monkeypatch.setattr(
        AdminQuizV2Service, "_now", staticmethod(lambda: clock["now"])
    )
    monkeypatch.setattr(
        QuizPracticeService, "_now", staticmethod(lambda: clock["now"])
    )

    first_session = await env.user_service.create_practice_session(
        env.user_id,
        QuizPracticeSessionCreate(
            mode="full",
            scope_type="knowledge_point",
            scope_id=env.point.id,
        ),
    )
    first_revision_id = first_session.questions[0].question_revision_id
    assert first_revision_id == env.question.current_revision_id

    edited = await env.admin_service.update_question(
        env.question.id,
        AdminQuizQuestionUpdate(
            lock_version=env.question.lock_version,
            explanation="这是第二版解析。",
        ),
        admin_id=env.admin_id,
    )
    published = await env.admin_service.publish_question_revision(
        env.question.id,
        AdminQuizVersionRequest(lock_version=edited.lock_version),
        admin_id=env.admin_id,
    )
    second_revision_id = published.current_revision_id
    assert second_revision_id != first_revision_id

    wrong_result = await env.practice_service.submit_attempt(
        env.user_id,
        first_session.id,
        QuizPracticeAttemptCreate(
            session_question_id=first_session.questions[0].session_question_id,
            idempotency_key="revision-one-wrong",
            user_answer="B",
        ),
    )
    assert wrong_result.is_correct is False

    clock["now"] += timedelta(seconds=1)
    review_session = await env.user_service.create_practice_session(
        env.user_id,
        QuizPracticeSessionCreate(
            mode="wrong_only",
            scope_type="knowledge_point",
            scope_id=env.point.id,
        ),
    )
    assert review_session.questions[0].question_revision_id == second_revision_id
    correct_result = await env.practice_service.submit_attempt(
        env.user_id,
        review_session.id,
        QuizPracticeAttemptCreate(
            session_question_id=review_session.questions[0].session_question_id,
            idempotency_key="revision-two-correct",
            user_answer="A",
        ),
    )
    assert correct_result.is_correct is True

    async with env.factory() as db:
        revision_stats = {
            int(item.question_revision_id): item
            for item in (
                await db.execute(
                    select(QuizQuestionRevisionStats).where(
                        QuizQuestionRevisionStats.question_id == env.question.id
                    )
                )
            ).scalars()
        }
        assert revision_stats[int(first_revision_id)].practice_first_attempts == 1
        assert revision_stats[int(first_revision_id)].practice_first_correct == 0
        assert revision_stats[int(second_revision_id)].practice_first_attempts == 1
        assert revision_stats[int(second_revision_id)].practice_first_correct == 1
        wrong_item = (
            await db.execute(
                select(QuizWrongItem).where(
                    QuizWrongItem.user_id == env.user_id,
                    QuizWrongItem.question_id == env.question.id,
                )
            )
        ).scalar_one()
        # Since the mastery rework one correct review no longer clears the
        # item; three consecutive correct answers are required instead.
        assert wrong_item.status == "active"
        assert wrong_item.consecutive_correct_count == 1
        assert wrong_item.wrong_count == 1
        assert wrong_item.review_count == 1
        assert wrong_item.last_reviewed_at == clock["now"]


async def test_library_progress_reports_first_attempt_stats_per_catalog_node(
    quiz_v2_catalog_env,
) -> None:
    env = quiz_v2_catalog_env
    await _grant_entitlement(env)
    for index in range(2):
        question = await env.admin_service.create_question(
            AdminQuizQuestionCreate(
                knowledge_point_id=env.point.id,
                question_type="single_choice",
                question_text=f"{env.prefix} 进度题目 {index}",
                options={"A": "甲", "B": "乙", "C": "丙"},
                correct_answer="A",
                explanation="甲正确。",
            ),
            admin_id=env.admin_id,
        )
        await env.admin_service.publish_question_revision(
            question.id,
            AdminQuizVersionRequest(lock_version=question.lock_version),
            admin_id=env.admin_id,
        )
    other_module = await env.admin_service.create_module(
        AdminQuizModuleCreate(library_id=env.library.id, name="进度模块"),
        admin_id=env.admin_id,
    )
    other_point = await env.admin_service.create_knowledge_point(
        AdminQuizKnowledgePointCreate(module_id=other_module.id, name="进度知识点"),
        admin_id=env.admin_id,
    )
    untouched = await env.admin_service.create_question(
        AdminQuizQuestionCreate(
            knowledge_point_id=other_point.id,
            question_type="judge",
            question_text=f"{env.prefix} 未练习题目",
            correct_answer="A",
            explanation="正确。",
        ),
        admin_id=env.admin_id,
    )
    await env.admin_service.publish_question_revision(
        untouched.id,
        AdminQuizVersionRequest(lock_version=untouched.lock_version),
        admin_id=env.admin_id,
    )

    session = await env.user_service.create_practice_session(
        env.user_id,
        QuizPracticeSessionCreate(
            mode="full",
            scope_type="knowledge_point",
            scope_id=env.point.id,
        ),
    )
    assert len(session.questions) == 3

    async def attempt(position: int, answer: str, key: str) -> None:
        item = session.questions[position - 1]
        await env.practice_service.submit_attempt(
            env.user_id,
            session.id,
            QuizPracticeAttemptCreate(
                session_question_id=item.session_question_id,
                idempotency_key=key,
                user_answer=answer,
            ),
        )

    await attempt(1, "A", "progress-1")
    await attempt(2, "A", "progress-2")
    # A re-attempt must stay out of first-attempt statistics.
    await attempt(1, "B", "progress-1-retry")
    await attempt(3, "B", "progress-3")

    progress = await env.user_service.get_library_progress(
        env.user_id, env.library.id
    )
    assert progress.library_id == env.library.id
    assert progress.question_count == 4
    assert progress.answered_questions == 3
    assert progress.accuracy == Decimal("66.7")
    assert progress.latest_accuracy == Decimal("33.3")
    assert [item.module_id for item in progress.modules] == [
        env.module.id,
        other_module.id,
    ]
    practiced_module = progress.modules[0]
    assert practiced_module.question_count == 3
    assert practiced_module.answered_questions == 3
    assert practiced_module.accuracy == Decimal("66.7")
    assert practiced_module.latest_accuracy == Decimal("33.3")
    assert [item.knowledge_point_id for item in practiced_module.knowledge_points] == [
        env.point.id
    ]
    untouched_module = progress.modules[1]
    assert untouched_module.module_id == other_module.id
    assert untouched_module.question_count == 1
    assert untouched_module.answered_questions == 0
    assert untouched_module.accuracy == Decimal("0.0")
    assert untouched_module.latest_accuracy == Decimal("0.0")


async def test_behavior_stats_report_daily_trend_user_ranking_and_wrong_order(
    quiz_v2_catalog_env,
    monkeypatch,
) -> None:
    env = quiz_v2_catalog_env
    await _grant_entitlement(env)

    @asynccontextmanager
    async def admin_db_ctx():
        async with env.factory() as session:
            yield session

    monkeypatch.setattr("app.services.admin_quiz.get_db_ctx", admin_db_ctx)
    session = await env.user_service.create_practice_session(
        env.user_id,
        QuizPracticeSessionCreate(
            mode="full",
            scope_type="knowledge_point",
            scope_id=env.point.id,
        ),
    )
    item = session.questions[0]
    await env.practice_service.submit_attempt(
        env.user_id,
        session.id,
        QuizPracticeAttemptCreate(
            session_question_id=item.session_question_id,
            idempotency_key="behavior-wrong",
            user_answer="B",
        ),
    )

    service = AdminQuizService()
    # The per-question stats table is refreshed by the background worker;
    # run one aggregation pass so the ranking endpoint sees the attempt.
    await service.aggregate_question_stats()
    daily = await service.get_daily_stats(AdminQuizDailyStatsQuery(days=7))
    assert len(daily) == 7
    today_item = daily[-1]
    assert today_item.practice_attempts >= 1
    assert today_item.active_users == 1
    assert all(entry.practice_attempts >= 0 for entry in daily)

    users = await service.list_user_stats(
        AdminQuizUserStatsQuery(page=1, page_size=20)
    )
    assert users.total >= 1
    top = users.items[0]
    assert top.user_id == env.user_id
    assert top.practice_total_attempts >= 1

    ranking = await service.list_question_stats(
        AdminQuizStatsQuestionQuery(sort="practice_wrong_count", order="desc")
    )
    wrong_item = ranking.items[0]
    assert wrong_item.practice_first_attempts >= 1
    assert wrong_item.practice_first_correct == 0


async def test_admin_user_practice_stats_filter_student_library_and_range(
    quiz_v2_catalog_env,
    monkeypatch,
) -> None:
    env = quiz_v2_catalog_env
    await _grant_entitlement(env)

    @asynccontextmanager
    async def admin_db_ctx():
        async with env.factory() as session:
            yield session

    monkeypatch.setattr("app.services.admin_quiz.get_db_ctx", admin_db_ctx)
    monkeypatch.setattr("app.services.quiz_exam.get_db_ctx", admin_db_ctx)

    for index in range(9):
        question = await env.admin_service.create_question(
            AdminQuizQuestionCreate(
                knowledge_point_id=env.point.id,
                question_type="single_choice",
                question_text=f"{env.prefix} 学生练习查询题目 {index}",
                options={"A": "甲", "B": "乙", "C": "丙"},
                correct_answer="A",
                explanation="甲正确。",
            ),
            admin_id=env.admin_id,
        )
        await env.admin_service.publish_question_revision(
            question.id,
            AdminQuizVersionRequest(lock_version=question.lock_version),
            admin_id=env.admin_id,
        )

    session = await env.user_service.create_practice_session(
        env.user_id,
        QuizPracticeSessionCreate(
            mode="full",
            scope_type="knowledge_point",
            scope_id=env.point.id,
        ),
    )
    assert len(session.questions) == 10
    for position, answer in enumerate(("A", "B", "A"), start=1):
        await env.practice_service.submit_attempt(
            env.user_id,
            session.id,
            QuizPracticeAttemptCreate(
                session_question_id=session.questions[position - 1].session_question_id,
                idempotency_key=f"user-practice-{position}",
                user_answer=answer,
            ),
        )

    exam_service = QuizExamService()
    exam = await exam_service.create_exam(
        env.user_id,
        QuizExamCreate(
            scope_type="knowledge_point",
            scope_id=env.point.id,
            question_count=10,
        ),
    )
    assert exam.status == "in_progress"
    for question in exam.questions[:8]:
        await exam_service.save_answer(
            env.user_id,
            exam.id,
            question.exam_question_id,
            QuizExamAnswerSave(user_answer="A", lock_version=0),
        )
    settled_exam = await exam_service.submit_exam(env.user_id, exam.id)
    assert settled_exam.status == "completed"

    service = AdminQuizService()
    today = datetime.now(ZoneInfo(settings.APP_TIMEZONE)).date()
    result = await service.get_user_practice_stats(
        AdminQuizUserPracticeQuery(
            user_id=env.user_id,
            library_id=env.library.id,
            date_from=today,
            date_to=today,
        )
    )
    assert result.total_attempts == 3
    assert result.answered_questions == 3
    assert result.first_attempts == 3
    assert result.first_correct == 2
    assert result.first_accuracy == Decimal("66.7")
    assert result.active_days == 1
    assert len(result.daily) == 1
    assert result.daily[0].date == today
    assert result.daily[0].attempts == 3
    assert result.daily[0].correct == 2
    assert result.daily[0].accuracy == Decimal("66.7")
    assert len(result.exam_rounds) == 1
    exam_round = result.exam_rounds[0]
    assert exam_round.exam_id == exam.id
    assert exam_round.status == "completed"
    assert exam_round.question_count == 10
    assert exam_round.correct_count == 8
    assert exam_round.wrong_count == 0
    assert exam_round.unanswered_count == 2
    assert exam_round.score == Decimal("80.0")
    assert exam_round.settled_at is not None
    assert result.exam_settled_count == 1
    assert result.exam_average_score == Decimal("80.0")
    assert result.exam_highest_score == Decimal("80.0")
    assert result.exam_latest_score == Decimal("80.0")

    outside = await service.get_user_practice_stats(
        AdminQuizUserPracticeQuery(
            user_id=env.user_id,
            library_id=env.library.id,
            date_from=today - timedelta(days=10),
            date_to=today - timedelta(days=1),
        )
    )
    assert outside.total_attempts == 0
    assert outside.daily == []
    assert outside.active_days == 0
    assert outside.exam_rounds == []
    assert outside.exam_settled_count == 0
    assert outside.exam_average_score is None

    with pytest.raises(NotFoundException):
        await service.get_user_practice_stats(
            AdminQuizUserPracticeQuery(
                user_id=99999999,
                library_id=env.library.id,
                date_from=today,
                date_to=today,
            )
        )
