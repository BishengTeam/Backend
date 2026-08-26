"""PostgreSQL coverage for V2 exam scopes and immutable revisions."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.certification.src.index import Course
from app.domain.community.src.index import (
    QuizAdminAuditLog,
    QuizCourseLibraryBinding,
    QuizKnowledgePoint,
    QuizLibrary,
    QuizLibraryEntitlement,
    QuizModule,
    QuizQuestion,
    QuizQuestionRevision,
    QuizQuestionRevisionStats,
    QuizWrongItem,
)
from app.domain.community.src.rule.quiz import (
    normalize_question_text,
    question_text_digest,
)
from app.domain.order.src.index import Order
from app.domain.plan.src.index import Plan  # noqa: F401 - resolve Order.plan_id FK
from app.domain.user.src.index import AdminUser, User
from app.port.exceptions import QuizV2Exception, ValidationException
from app.schemas.admin_quiz_contract import (
    AdminQuizQuestionUpdate,
    AdminQuizVersionRequest,
)
from app.schemas.quiz_contract import QuizExamAnswerSave, QuizExamCreate
from app.schemas.quiz_contract import QuizExamScopeSelection
from app.schemas.quiz_contract import QuizManualExamCreate
from app.services.admin_quiz_v2 import AdminQuizV2Service
from app.services.quiz_exam import QuizExamService


pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


def _database_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    assert url.startswith("postgresql+asyncpg://")
    return url


async def _seed_question(
    db,
    *,
    admin_id: int,
    library_id: int,
    point_id: int,
    stem: str,
    now: datetime,
) -> QuizQuestion:
    normalized_stem = normalize_question_text(stem)
    digest = question_text_digest(normalized_stem)
    question = QuizQuestion(
        library_id=library_id,
        knowledge_point_id=point_id,
        category_id=None,
        question_type="judge",
        status="published",
        question_text=stem,
        normalized_question_text=normalized_stem,
        question_text_hash=digest,
        options={"A": "正确", "B": "错误"},
        correct_answer="A",
        explanation=f"{stem} 的第一版解析",
        ever_published=True,
        published_at=now,
        stem_reserved=True,
        lock_version=1,
        created_by=admin_id,
        updated_by=admin_id,
    )
    db.add(question)
    await db.flush()
    revision = QuizQuestionRevision(
        question_id=question.id,
        revision_no=1,
        status="published",
        question_type=question.question_type,
        question_text=question.question_text,
        normalized_question_text=question.normalized_question_text,
        question_text_hash=question.question_text_hash,
        options=question.options,
        correct_answer=question.correct_answer,
        explanation=question.explanation,
        published_at=now,
        created_by=admin_id,
    )
    db.add(revision)
    await db.flush()
    question.current_revision_id = revision.id
    return question


@pytest.fixture
async def quiz_v2_exam_env(monkeypatch):
    engine = create_async_engine(_database_url(), pool_size=4, max_overflow=4)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"qv2exam_{uuid4().hex[:10]}"

    @asynccontextmanager
    async def db_ctx():
        async with factory() as session:
            yield session

    for target in (
        "app.services.admin_quiz_v2.get_db_ctx",
        "app.services.quiz_exam.get_db_ctx",
        "app.services.quiz_practice.get_db_ctx",
        "app.services.quiz_v2.get_db_ctx",
    ):
        monkeypatch.setattr(target, db_ctx)

    now = datetime.now(timezone.utc)
    async with factory() as db:
        admin = AdminUser(
            username=f"{prefix}_admin",
            password_hash="test-only",
            role="super_admin",
        )
        free_user = User(openid=f"{prefix}_free", is_active=True)
        entitled_user = User(openid=f"{prefix}_entitled", is_active=True)
        hidden_user = User(openid=f"{prefix}_hidden", is_active=True)
        course = Course(
            title=f"{prefix}课程",
            category="test",
            cover_storage_key=f"course/{prefix}/cover.jpg",
            price=100,
            preview_chapter_count=1,
            status="published",
            is_active=True,
        )
        db.add_all([admin, free_user, entitled_user, hidden_user, course])
        await db.flush()
        order = Order(
            user_id=entitled_user.id,
            order_kind="course",
            product_type="course",
            price=100,
            status="paid",
            paid_at=now,
        )
        free_library = QuizLibrary(
            name=f"{prefix}免费题库",
            normalized_name=f"{prefix}免费题库",
            description="免费 V2 考试题库",
            cover_url="https://example.invalid/free.png",
            access_mode="free",
            system_kind="none",
            migration_state="ready",
            status="published",
            v2_enabled=True,
            published_at=now,
            created_by=admin.id,
            updated_by=admin.id,
        )
        paid_library = QuizLibrary(
            name=f"{prefix}课程题库",
            normalized_name=f"{prefix}课程题库",
            description="课程权益 V2 考试题库",
            cover_url="https://example.invalid/paid.png",
            access_mode="course_entitlement",
            system_kind="none",
            migration_state="ready",
            status="published",
            v2_enabled=True,
            published_at=now,
            created_by=admin.id,
            updated_by=admin.id,
        )
        db.add_all([order, free_library, paid_library])
        await db.flush()

        free_module = QuizModule(
            library_id=free_library.id,
            name="免费模块",
            normalized_name="免费模块",
            status="active",
            created_by=admin.id,
            updated_by=admin.id,
        )
        paid_module = QuizModule(
            library_id=paid_library.id,
            name="课程模块",
            normalized_name="课程模块",
            status="active",
            created_by=admin.id,
            updated_by=admin.id,
        )
        db.add_all([free_module, paid_module])
        await db.flush()
        free_points = [
            QuizKnowledgePoint(
                library_id=free_library.id,
                module_id=free_module.id,
                name=f"免费知识点 {index}",
                normalized_name=f"免费知识点 {index}",
                status="active",
                created_by=admin.id,
                updated_by=admin.id,
            )
            for index in (1, 2)
        ]
        paid_point = QuizKnowledgePoint(
            library_id=paid_library.id,
            module_id=paid_module.id,
            name="课程知识点",
            normalized_name="课程知识点",
            status="active",
            created_by=admin.id,
            updated_by=admin.id,
        )
        db.add_all([*free_points, paid_point])
        await db.flush()

        free_questions: list[QuizQuestion] = []
        paid_questions: list[QuizQuestion] = []
        for point_index, point in enumerate(free_points, start=1):
            for question_index in range(1, 11):
                free_questions.append(
                    await _seed_question(
                        db,
                        admin_id=admin.id,
                        library_id=free_library.id,
                        point_id=point.id,
                        stem=(
                            f"{prefix} 免费知识点 {point_index} "
                            f"判断题 {question_index}"
                        ),
                        now=now,
                    )
                )
        for question_index in range(1, 11):
            paid_questions.append(
                await _seed_question(
                    db,
                    admin_id=admin.id,
                    library_id=paid_library.id,
                    point_id=paid_point.id,
                    stem=f"{prefix} 课程判断题 {question_index}",
                    now=now,
                )
            )

        binding = QuizCourseLibraryBinding(
            course_id=course.id,
            library_id=paid_library.id,
            status="active",
            lock_version=1,
            created_by=admin.id,
            updated_by=admin.id,
        )
        entitlement = QuizLibraryEntitlement(
            user_id=entitled_user.id,
            library_id=paid_library.id,
            course_id=course.id,
            order_id=order.id,
            source_type="course_order",
            status="active",
            starts_at=now,
            snapshot={
                "library_id": int(paid_library.id),
                "library_code": paid_library.library_code,
                "name": paid_library.name,
            },
        )
        db.add_all([binding, entitlement])
        await db.commit()

    library_ids = [int(free_library.id), int(paid_library.id)]
    user_ids = [int(free_user.id), int(entitled_user.id), int(hidden_user.id)]
    env = SimpleNamespace(
        factory=factory,
        admin_id=int(admin.id),
        admin_service=AdminQuizV2Service(),
        exam_service=QuizExamService(),
        free_user_id=int(free_user.id),
        entitled_user_id=int(entitled_user.id),
        hidden_user_id=int(hidden_user.id),
        free_library=free_library,
        paid_library=paid_library,
        free_module=free_module,
        paid_module=paid_module,
        free_points=free_points,
        paid_point=paid_point,
        free_questions=free_questions,
        paid_questions=paid_questions,
        course_id=int(course.id),
        order_id=int(order.id),
    )
    try:
        yield env
    finally:
        async with factory() as db:
            await db.execute(
                delete(QuizLibraryEntitlement).where(
                    QuizLibraryEntitlement.library_id.in_(library_ids)
                )
            )
            await db.execute(
                delete(QuizCourseLibraryBinding).where(
                    QuizCourseLibraryBinding.library_id.in_(library_ids)
                )
            )
            await db.execute(delete(Order).where(Order.id == env.order_id))
            await db.execute(delete(User).where(User.id.in_(user_ids)))
            question_ids = select(QuizQuestion.id).where(
                QuizQuestion.library_id.in_(library_ids)
            )
            await db.execute(
                delete(QuizQuestionRevisionStats).where(
                    QuizQuestionRevisionStats.question_id.in_(question_ids)
                )
            )
            questions = list(
                (
                    await db.execute(
                        select(QuizQuestion).where(
                            QuizQuestion.library_id.in_(library_ids)
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
            await db.execute(delete(Course).where(Course.id == env.course_id))
            await db.execute(
                delete(QuizAdminAuditLog).where(
                    QuizAdminAuditLog.admin_id == env.admin_id
                )
            )
            await db.execute(delete(AdminUser).where(AdminUser.id == env.admin_id))
            await db.commit()
        await engine.dispose()


async def test_v2_exam_supports_free_library_module_and_knowledge_point_scopes(
    quiz_v2_exam_env,
) -> None:
    env = quiz_v2_exam_env
    scopes = (
        ("library", env.free_library.id),
        ("module", env.free_module.id),
        ("knowledge_point", env.free_points[0].id),
    )
    for scope_type, scope_id in scopes:
        exam = await env.exam_service.create_exam(
            env.free_user_id,
            QuizExamCreate(
                scope_type=scope_type,
                scope_id=scope_id,
                question_count=10,
            ),
        )
        payload = exam.model_dump()
        assert payload["category_id"] is None
        assert payload["library_id"] == env.free_library.id
        assert payload["scope_type"] == scope_type
        assert payload["scope_id"] == scope_id
        assert len(payload["questions"]) == 10
        assert all(question["question_revision_id"] for question in payload["questions"])
        assert all(question["category_id"] is None for question in payload["questions"])
        assert all("correct_answer" not in question for question in payload["questions"])
        assert all("explanation" not in question for question in payload["questions"])
        assert all(
            [item.kind for item in question.category_path]
            == ["library", "module", "knowledge_point"]
            for question in exam.questions
        )
        await env.exam_service.abandon_exam(env.free_user_id, exam.id)


async def test_course_exam_is_hidden_without_entitlement_and_available_with_it(
    quiz_v2_exam_env,
) -> None:
    env = quiz_v2_exam_env
    request = QuizExamCreate(
        scope_type="knowledge_point",
        scope_id=env.paid_point.id,
        question_count=10,
    )
    with pytest.raises(QuizV2Exception) as hidden:
        await env.exam_service.create_exam(env.hidden_user_id, request)
    assert hidden.value.http_status_code == 404
    assert hidden.value.detail["reason"] == "quiz_library_not_found"

    exam = await env.exam_service.create_exam(env.entitled_user_id, request)
    assert exam.library_id == env.paid_library.id
    assert exam.scope_type == "knowledge_point"
    assert len(exam.questions) == 10


async def test_multi_scope_exam_mixes_modules_and_points_within_one_library(
    quiz_v2_exam_env,
) -> None:
    env = quiz_v2_exam_env
    exam = await env.exam_service.create_exam(
        env.free_user_id,
        QuizExamCreate(
            scopes=[
                QuizExamScopeSelection(
                    scope_type="module", scope_id=env.free_module.id
                ),
                QuizExamScopeSelection(
                    scope_type="knowledge_point", scope_id=env.free_points[1].id
                ),
            ],
            question_count=15,
        ),
    )
    assert exam.library_id == env.free_library.id
    assert exam.scope_type == "library"
    assert exam.scope_id == env.free_library.id
    assert len(exam.questions) == 15
    point_ids = {int(point.id) for point in env.free_points}
    assert {
        question.knowledge_point_id
        for question in exam.questions
        if question.knowledge_point_id is not None
    } <= point_ids
    await env.exam_service.abandon_exam(env.free_user_id, exam.id)


async def test_multi_scope_exam_rejects_cross_library_selection(
    quiz_v2_exam_env,
) -> None:
    env = quiz_v2_exam_env
    with pytest.raises(ValidationException, match="同一题库"):
        await env.exam_service.create_exam(
            env.entitled_user_id,
            QuizExamCreate(
                scopes=[
                    QuizExamScopeSelection(
                        scope_type="module", scope_id=env.free_module.id
                    ),
                    QuizExamScopeSelection(
                        scope_type="knowledge_point", scope_id=env.paid_point.id
                    ),
                ],
                question_count=10,
            ),
        )


async def test_manual_exam_creates_exam_from_explicit_selection(
    quiz_v2_exam_env,
) -> None:
    env = quiz_v2_exam_env
    question_ids = [int(question.id) for question in env.free_questions[:10]]
    exam = await env.exam_service.create_manual_exam(
        env.free_user_id,
        QuizManualExamCreate(question_ids=question_ids),
    )
    assert exam.library_id == env.free_library.id
    assert exam.scope_type == "library"
    assert exam.scope_id == env.free_library.id
    assert exam.question_count == 10
    assert [item.question_id for item in exam.questions] == question_ids
    assert all(item.question_revision_id for item in exam.questions)
    await env.exam_service.abandon_exam(env.free_user_id, exam.id)


async def test_manual_exam_rejects_cross_library_and_unavailable_questions(
    quiz_v2_exam_env,
) -> None:
    env = quiz_v2_exam_env
    free_ids = [int(question.id) for question in env.free_questions[:9]]
    paid_ids = [int(question.id) for question in env.paid_questions[:1]]
    with pytest.raises(ValidationException, match="同一题库"):
        await env.exam_service.create_manual_exam(
            env.entitled_user_id,
            QuizManualExamCreate(question_ids=[*free_ids, *paid_ids]),
        )
    with pytest.raises(Exception, match="不存在或不可用"):
        await env.exam_service.create_manual_exam(
            env.free_user_id,
            QuizManualExamCreate(question_ids=[*free_ids, 999999999]),
        )



async def test_v2_exam_freezes_revision_path_and_updates_wrong_book_and_stats(
    quiz_v2_exam_env,
) -> None:
    env = quiz_v2_exam_env
    exam = await env.exam_service.create_exam(
        env.free_user_id,
        QuizExamCreate(
            scope_type="knowledge_point",
            scope_id=env.free_points[0].id,
            question_count=10,
        ),
    )
    target = exam.questions[0]
    correct_target = exam.questions[1]
    frozen_revision_id = int(target.question_revision_id)
    frozen_text = target.question_text
    frozen_path = [item.model_dump() for item in target.category_path]

    current = await env.admin_service.get_question(target.id)
    edited = await env.admin_service.update_question(
        target.id,
        AdminQuizQuestionUpdate(
            lock_version=current.lock_version,
            question_text=f"{frozen_text}（第二版）",
            explanation="第二版解析不能覆盖已开始考试。",
        ),
        admin_id=env.admin_id,
    )
    published = await env.admin_service.publish_question_revision(
        target.id,
        AdminQuizVersionRequest(lock_version=edited.lock_version),
        admin_id=env.admin_id,
    )
    assert published.current_revision_id != frozen_revision_id

    still_running = await env.exam_service.get_exam(env.free_user_id, exam.id)
    frozen = next(question for question in still_running.questions if question.id == target.id)
    assert frozen.question_revision_id == frozen_revision_id
    assert frozen.question_text == frozen_text
    assert [item.model_dump() for item in frozen.category_path] == frozen_path
    assert "correct_answer" not in frozen.model_dump()
    assert "explanation" not in frozen.model_dump()

    await env.exam_service.save_answer(
        env.free_user_id,
        exam.id,
        target.exam_question_id,
        QuizExamAnswerSave(user_answer="B", lock_version=0),
    )
    await env.exam_service.save_answer(
        env.free_user_id,
        exam.id,
        correct_target.exam_question_id,
        QuizExamAnswerSave(user_answer="A", lock_version=0),
    )
    await env.exam_service.submit_exam(env.free_user_id, exam.id)
    settled = await env.exam_service.get_exam(env.free_user_id, exam.id)
    target_result = next(
        question for question in settled.questions if question.id == target.id
    )
    assert target_result.question_revision_id == frozen_revision_id
    assert target_result.question_text == frozen_text
    assert target_result.explanation.endswith("第一版解析")
    assert target_result.correct_answer == "A"
    assert target_result.is_correct is False
    assert settled.correct_count == 1
    assert settled.wrong_count == 1
    assert settled.unanswered_count == 8

    async with env.factory() as db:
        wrong = (
            await db.execute(
                select(QuizWrongItem).where(
                    QuizWrongItem.user_id == env.free_user_id,
                    QuizWrongItem.question_id == target.id,
                )
            )
        ).scalar_one()
        assert wrong.latest_wrong_snapshot["category_id"] is None
        assert wrong.latest_wrong_snapshot["category_path"] == frozen_path
        wrong_revision_stats = (
            await db.execute(
                select(QuizQuestionRevisionStats).where(
                    QuizQuestionRevisionStats.question_revision_id
                    == frozen_revision_id
                )
            )
        ).scalar_one()
        correct_revision_stats = (
            await db.execute(
                select(QuizQuestionRevisionStats).where(
                    QuizQuestionRevisionStats.question_revision_id
                    == correct_target.question_revision_id
                )
            )
        ).scalar_one()
        assert wrong_revision_stats.exam_answers == 1
        assert wrong_revision_stats.exam_correct == 0
        assert correct_revision_stats.exam_answers == 1
        assert correct_revision_stats.exam_correct == 1

    future_exam = await env.exam_service.create_exam(
        env.free_user_id,
        QuizExamCreate(
            scope_type="knowledge_point",
            scope_id=env.free_points[0].id,
            question_count=10,
        ),
    )
    future_target = next(
        question for question in future_exam.questions if question.id == target.id
    )
    assert future_target.question_revision_id == published.current_revision_id
    assert future_target.question_text.endswith("（第二版）")
