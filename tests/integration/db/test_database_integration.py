"""PostgreSQL database integration tests.

Requires TEST_DATABASE_URL and TEST_DATABASE_URL_SYNC environment variables.
"""

import os
from datetime import date, datetime, timezone
from typing import AsyncGenerator

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]

ALL_TABLES = frozenset({
    "user", "user_profile", "user_realname", "user_student", "order",
    "course", "course_chapter", "user_chapter_progress", "course_enrollment",
    "certification", "quiz_category", "quiz_question", "quiz_checkin",
    "quiz_practice_session", "quiz_practice_session_question",
    "quiz_practice_attempt", "quiz_wrong_item", "quiz_collection",
    "quiz_exam", "quiz_exam_question", "quiz_exam_answer",
    "quiz_user_stats", "quiz_question_stats", "quiz_import_job", "quiz_import_error",
    "quiz_admin_audit_log", "quick_question", "conversation", "price_config",
    "user_points", "points_history", "coupon", "user_coupon",
    "deleted_openid", "agreement", "competition_reg", "ticket",
})


_engine = None


async def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            os.environ["TEST_DATABASE_URL"], echo=False,
        )
    return _engine


async def _get_inspector():
    engine = await _get_engine()
    sync_engine = engine.sync_engine
    return inspect(sync_engine)


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Create a new connection + transaction per test, rollback after."""
    url = os.environ["TEST_DATABASE_URL"]
    engine = create_async_engine(url, echo=False)
    async with engine.connect() as conn:
        trans = await conn.begin()
        try:
            sessionmaker = async_sessionmaker(
                bind=conn, class_=AsyncSession, expire_on_commit=False,
            )
            async with sessionmaker() as session:
                yield session
        finally:
            await trans.rollback()
            await conn.close()
    await engine.dispose()


# Convenience: provide a linked session + connection for inspector access


@pytest.fixture
async def conn_and_session() -> AsyncGenerator[tuple, None]:
    url = os.environ["TEST_DATABASE_URL"]
    engine = create_async_engine(url, echo=False)
    async with engine.connect() as conn:
        trans = await conn.begin()
        try:
            sessionmaker = async_sessionmaker(
                bind=conn, class_=AsyncSession, expire_on_commit=False,
            )
            async with sessionmaker() as session:
                yield conn, session
        finally:
            await trans.rollback()
            await conn.close()
    await engine.dispose()


# ---------------------------------------------------------------------------
# 1. Environment and migrations
# ---------------------------------------------------------------------------


class TestDatabaseEnvironment:
    async def test_urls_are_configured(self):
        assert os.getenv("TEST_DATABASE_URL", "").startswith("postgresql+asyncpg://")
        assert os.getenv("TEST_DATABASE_URL_SYNC", "").startswith("postgresql://")

    async def test_can_connect(self, conn_and_session):
        conn, _ = conn_and_session
        result = await conn.execute(text("SELECT 1"))
        assert result.scalar() == 1

    async def test_alembic_version_tracked(self, db_session):
        result = await db_session.execute(text("SELECT count(*) FROM alembic_version"))
        count = result.scalar()
        assert count >= 1, f"alembic_version should have at least 1 entry, got {count}"

    async def test_all_base_tables_exist(self, conn_and_session):
        conn, _ = conn_and_session
        def _check(sync_conn):
            inspector = inspect(sync_conn)
            return inspector.get_table_names()
        actual = set(await conn.run_sync(_check))
        actual.discard("alembic_version")
        missing = ALL_TABLES - actual
        assert not missing, f"Missing tables: {missing}"


# ---------------------------------------------------------------------------
# 2. Table structure verification
# ---------------------------------------------------------------------------


class TestTableStructure:
    async def test_user_columns_and_unique_openid(self, conn_and_session):
        conn, _ = conn_and_session
        def _check(sync_conn):
            inspector = inspect(sync_conn)
            cols = {c["name"] for c in inspector.get_columns("user")}
            indexes = {idx["name"]: idx for idx in inspector.get_indexes("user")}
            return cols, indexes
        cols, indexes = await conn.run_sync(_check)
        for col in ("id", "openid", "phone", "is_active", "created_at", "updated_at"):
            assert col in cols, f"user table missing column: {col}"
        openid_idxs = [v for k, v in indexes.items() if "openid" in k]
        assert len(openid_idxs) >= 1, "User.openid should have an index"
        assert openid_idxs[0]["unique"], "User.openid index should be unique"

    async def test_order_check_constraint(self, conn_and_session):
        conn, _ = conn_and_session
        def _check(sync_conn):
            inspector = inspect(sync_conn)
            return inspector.get_check_constraints("order")
        checks = await conn.run_sync(_check)
        names = {c["name"] for c in checks}
        assert "ck_order_status" in names, f"Missing ck_order_status; got: {names}"

    async def test_course_branch_schema_is_complete(self, conn_and_session):
        conn, _ = conn_and_session

        def _check(sync_conn):
            inspector = inspect(sync_conn)
            return (
                {column["name"] for column in inspector.get_columns("course")},
                set(inspector.get_table_names()),
            )

        course_columns, tables = await conn.run_sync(_check)
        assert "free_preview_seconds" in course_columns
        assert {"course_chapter", "user_chapter_progress"} <= tables

    async def test_plan_status_can_store_all_declared_states(self, conn_and_session):
        conn, _ = conn_and_session

        def _check(sync_conn):
            inspector = inspect(sync_conn)
            return next(
                column
                for column in inspector.get_columns("plan")
                if column["name"] == "status"
            )

        status_column = await conn.run_sync(_check)
        assert status_column["type"].length == 32

    async def test_split_identity_user_ids_are_unique(self, conn_and_session):
        conn, _ = conn_and_session
        def _check(sync_conn):
            inspector = inspect(sync_conn)
            return {
                table: inspector.get_indexes(table)
                for table in ("user_realname", "user_student")
            }
        indexes_by_table = await conn.run_sync(_check)
        for table, indexes in indexes_by_table.items():
            unique_on_user = [
                item
                for item in indexes
                if "user_id" in item.get("column_names", []) and item.get("unique")
            ]
            assert unique_on_user, f"{table}.user_id should be unique"

    async def test_price_config_partial_unique_index(self, conn_and_session):
        conn, _ = conn_and_session
        def _check(sync_conn):
            inspector = inspect(sync_conn)
            return inspector.get_indexes("price_config")
        indexes = await conn.run_sync(_check)
        names = {idx["name"] for idx in indexes}
        assert "uq_price_config_active_cert_user" in names, (
            f"Missing uq_price_config_active_cert_user; got: {names}"
        )

    async def test_certification_code_unique(self, conn_and_session):
        conn, _ = conn_and_session
        def _check(sync_conn):
            inspector = inspect(sync_conn)
            return inspector.get_indexes("certification")
        indexes = await conn.run_sync(_check)
        code_idx = [i for i in indexes if "code" in i.get("column_names", []) and i.get("unique")]
        assert len(code_idx) >= 1, "Certification.code should have a unique index"

    async def test_order_out_trade_no_unique(self, conn_and_session):
        conn, _ = conn_and_session
        def _check(sync_conn):
            inspector = inspect(sync_conn)
            return inspector.get_indexes("order")
        indexes = await conn.run_sync(_check)
        trade_no_idx = [
            i for i in indexes
            if "out_trade_no" in i.get("column_names", []) and i.get("unique")
        ]
        assert len(trade_no_idx) >= 1, "Order.out_trade_no should have a unique index"


# ---------------------------------------------------------------------------
# 3. CRUD operations
# ---------------------------------------------------------------------------


class TestUserCRUD:
    async def test_create_and_read(self, db_session):
        from app.domain.user.src.index import User
        user = User(openid="test_crud_001")
        db_session.add(user)
        await db_session.flush()
        assert user.id is not None

        fetched = await db_session.get(User, user.id)
        assert fetched.openid == "test_crud_001"
        assert fetched.is_active is True

    async def test_openid_uniqueness(self, db_session):
        from app.domain.user.src.index import User
        db_session.add(User(openid="dup_test_001"))
        await db_session.flush()
        db_session.add(User(openid="dup_test_001"))
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_update_phone(self, db_session):
        from app.domain.user.src.index import User
        user = User(openid="phone_update_test")
        db_session.add(user)
        await db_session.flush()
        user.phone = "13800138001"
        await db_session.flush()
        fetched = await db_session.get(User, user.id)
        assert fetched.phone == "13800138001"

    async def test_soft_delete(self, db_session):
        from app.domain.user.src.index import User
        user = User(openid="soft_delete_test")
        db_session.add(user)
        await db_session.flush()
        user.is_active = False
        await db_session.flush()
        fetched = await db_session.get(User, user.id)
        assert fetched.is_active is False


class TestOrderCRUD:
    async def test_create_order_valid_statuses(self, db_session):
        from app.domain.user.src.index import User
        from app.domain.order.src.index import Order
        user = User(openid="order_status_user")
        db_session.add(user)
        await db_session.flush()
        for status in ("pending", "paid", "completed", "refunded"):
            order = Order(
                user_id=user.id, order_kind="certification", product_type="H3C-NE",
                candidate_name="测试", candidate_phone="13800138000",
                price=9900, status=status,
            )
            db_session.add(order)
            await db_session.flush()
            assert order.id is not None

    async def test_rejects_invalid_status(self, db_session):
        from app.domain.user.src.index import User
        from app.domain.order.src.index import Order
        user = User(openid="bad_status_user")
        db_session.add(user)
        await db_session.flush()
        order = Order(
            user_id=user.id, order_kind="certification", product_type="H3C-NE",
            candidate_name="测试", candidate_phone="13800138000",
            price=9900, status="cancelled",
        )
        db_session.add(order)
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_out_trade_no_unique(self, db_session):
        from app.domain.user.src.index import User
        from app.domain.order.src.index import Order
        user = User(openid="trade_no_user")
        db_session.add(user)
        await db_session.flush()
        db_session.add(Order(user_id=user.id, order_kind="certification", product_type="H3C-NE",
                             candidate_name="A", candidate_phone="13801",
                             price=100, out_trade_no="trade_uniq"))
        await db_session.flush()
        db_session.add(Order(user_id=user.id, order_kind="certification", product_type="H3C-NE",
                             candidate_name="B", candidate_phone="13802",
                             price=200, out_trade_no="trade_uniq"))
        with pytest.raises(IntegrityError):
            await db_session.flush()


class TestCourseCRUD:
    async def test_create_and_read(self, db_session):
        from app.domain.certification.src.index import Course
        course = Course(
            title="测试课程",
            category="网络",
            price=9900,
            status="published",
            is_active=True,
        )
        db_session.add(course)
        await db_session.flush()
        assert course.is_active is True
        fetched = await db_session.get(Course, course.id)
        assert fetched.title == "测试课程"

    async def test_enrollment(self, db_session):
        from app.domain.user.src.index import User
        from app.domain.certification.src.index import Course, CourseEnrollment
        user = User(openid="enroll_user")
        db_session.add(user)
        await db_session.flush()
        course = Course(
            title="报名课程",
            category="安全",
            price=19900,
            status="published",
            is_active=True,
        )
        db_session.add(course)
        await db_session.flush()
        enrollment = CourseEnrollment(
            user_id=user.id,
            course_id=course.id,
            status="enrolled",
            learning_access=True,
        )
        db_session.add(enrollment)
        await db_session.flush()
        assert enrollment.status == "enrolled"
        assert enrollment.learning_access is True


class TestCertificationCRUD:
    async def test_create(self, db_session):
        from app.domain.certification.src.index import Certification
        cert = Certification(
            name="H3CNE", chinese_name="H3C认证网络工程师",
            code="H3C-NE-crud", vendor="H3C",
        )
        db_session.add(cert)
        await db_session.flush()
        assert cert.is_active is True

    async def test_code_unique(self, db_session):
        from app.domain.certification.src.index import Certification
        db_session.add(Certification(
            name="A", chinese_name="A证", code="CODE-UNIQUE-crud", vendor="H3C",
        ))
        await db_session.flush()
        db_session.add(Certification(
            name="B", chinese_name="B证", code="CODE-UNIQUE-crud", vendor="深信服",
        ))
        with pytest.raises(IntegrityError):
            await db_session.flush()


class TestQuizCRUD:
    async def test_full_flow(self, db_session):
        from app.domain.user.src.index import AdminUser
        from app.domain.user.src.index import User
        from app.domain.community.src.index import (
            QuizCategory,
            QuizPracticeAttempt,
            QuizPracticeSession,
            QuizPracticeSessionQuestion,
            QuizQuestion,
        )

        user = User(openid="quiz_flow_user")
        admin = AdminUser(
            username="quiz_flow_admin",
            password_hash="integration-test-only",
            role="super_admin",
        )
        db_session.add_all([user, admin])
        await db_session.flush()
        cat = QuizCategory(
            name="网络基础",
            normalized_name="网络基础",
            depth=1,
            created_by=admin.id,
            updated_by=admin.id,
        )
        db_session.add(cat)
        await db_session.flush()
        question = QuizQuestion(
            category_id=cat.id, question_type="single_choice",
            question_text="HTTP的默认端口?",
            normalized_question_text="http的默认端口?",
            question_text_hash="a" * 64,
            options={"A": "80", "B": "443", "C": "8080"},
            correct_answer="A",
            created_by=admin.id,
            updated_by=admin.id,
        )
        db_session.add(question)
        await db_session.flush()
        session = QuizPracticeSession(
            user_id=user.id,
            mode="normal",
            category_id=cat.id,
            requested_count=10,
            actual_count=1,
            started_at=datetime.now(timezone.utc),
        )
        db_session.add(session)
        await db_session.flush()
        snapshot = QuizPracticeSessionQuestion(
            session_id=session.id,
            question_id=question.id,
            position=1,
            category_id=cat.id,
            category_path=[{"id": cat.id, "name": cat.name}],
            question_type=question.question_type,
            question_text=question.question_text,
            options=question.options,
            correct_answer=question.correct_answer,
            explanation="端口解析",
            question_lock_version=question.lock_version,
        )
        db_session.add(snapshot)
        await db_session.flush()
        attempt = QuizPracticeAttempt(
            user_id=user.id,
            session_id=session.id,
            session_question_id=snapshot.id,
            idempotency_key="quiz-flow-attempt-1",
            attempt_no=1,
            is_first_attempt=True,
            user_answer="A",
            is_correct=True,
            submitted_at=datetime.now(timezone.utc),
        )
        db_session.add(attempt)
        await db_session.flush()
        assert attempt.is_first_attempt is True
        assert attempt.is_correct is True

    async def test_checkin(self, db_session):
        from app.domain.user.src.index import AdminUser
        from app.domain.user.src.index import User
        from app.domain.community.src.index import (
            QuizCategory,
            QuizCheckin,
            QuizPracticeAttempt,
            QuizPracticeSession,
            QuizPracticeSessionQuestion,
            QuizQuestion,
        )
        user = User(openid="checkin_user")
        admin = AdminUser(
            username="checkin_admin",
            password_hash="integration-test-only",
            role="super_admin",
        )
        db_session.add_all([user, admin])
        await db_session.flush()
        category = QuizCategory(
            name="打卡分类",
            normalized_name="打卡分类",
            depth=1,
            created_by=admin.id,
            updated_by=admin.id,
        )
        db_session.add(category)
        await db_session.flush()
        question = QuizQuestion(
            category_id=category.id,
            question_type="single_choice",
            question_text="打卡题",
            normalized_question_text="打卡题",
            question_text_hash="b" * 64,
            options={"A": "对", "B": "错", "C": "不确定"},
            correct_answer="A",
            created_by=admin.id,
            updated_by=admin.id,
        )
        db_session.add(question)
        await db_session.flush()
        session = QuizPracticeSession(
            user_id=user.id,
            mode="normal",
            category_id=category.id,
            requested_count=10,
            actual_count=1,
            started_at=datetime.now(timezone.utc),
        )
        db_session.add(session)
        await db_session.flush()
        snapshot = QuizPracticeSessionQuestion(
            session_id=session.id,
            question_id=question.id,
            position=1,
            category_id=category.id,
            category_path=[{"id": category.id, "name": category.name}],
            question_type="single_choice",
            question_text=question.question_text,
            options=question.options,
            correct_answer="A",
            explanation="打卡解析",
            question_lock_version=question.lock_version,
        )
        db_session.add(snapshot)
        await db_session.flush()
        attempt = QuizPracticeAttempt(
            user_id=user.id,
            session_id=session.id,
            session_question_id=snapshot.id,
            idempotency_key="checkin-attempt-1",
            attempt_no=1,
            is_first_attempt=True,
            user_answer="A",
            is_correct=True,
            submitted_at=datetime.now(timezone.utc),
        )
        db_session.add(attempt)
        await db_session.flush()
        checkin = QuizCheckin(
            user_id=user.id, checkin_date=date.today(),
            questions_completed=5, consecutive_days=3,
            first_attempt_id=attempt.id,
        )
        db_session.add(checkin)
        await db_session.flush()
        assert checkin.id is not None

    async def test_parent_child_category_fk(self, db_session):
        from app.domain.user.src.index import AdminUser
        from app.domain.community.src.index import QuizCategory
        admin = AdminUser(
            username="category_fk_admin",
            password_hash="integration-test-only",
            role="super_admin",
        )
        db_session.add(admin)
        await db_session.flush()
        parent = QuizCategory(
            name="父分类",
            normalized_name="父分类",
            depth=1,
            created_by=admin.id,
            updated_by=admin.id,
        )
        db_session.add(parent)
        await db_session.flush()
        child = QuizCategory(
            name="子分类",
            normalized_name="子分类",
            parent_id=parent.id,
            depth=2,
            created_by=admin.id,
            updated_by=admin.id,
        )
        db_session.add(child)
        await db_session.flush()
        assert child.parent_id == parent.id


class TestQuickQuestionCRUD:
    async def test_create(self, db_session):
        from app.domain.community.src.index import QuickQuestion
        q = QuickQuestion(question_text="什么是H3C?", category="认证")
        db_session.add(q)
        await db_session.flush()
        assert q.is_active is True
        assert q.sort_order == 0


class TestDeletedOpenidCRUD:
    async def test_create_and_uniqueness(self, db_session):
        from app.domain.user.src.index import DeletedOpenid
        db_session.add(DeletedOpenid(openid="deleted_001"))
        await db_session.flush()
        db_session.add(DeletedOpenid(openid="deleted_001"))
        with pytest.raises(IntegrityError):
            await db_session.flush()


class TestPriceConfigCRUD:
    async def test_create(self, db_session):
        from app.domain.order.src.index import PriceConfig
        pc = PriceConfig(product_type="H3C-NE", user_type="student", price=29900)
        db_session.add(pc)
        await db_session.flush()
        assert pc.is_active is True

    async def test_active_unique_constraint(self, db_session):
        from app.domain.order.src.index import PriceConfig
        db_session.add(PriceConfig(
            product_type="H3C-NE", user_type="student", price=29900, is_active=True,
        ))
        await db_session.flush()
        db_session.add(PriceConfig(
            product_type="H3C-NE", user_type="student", price=39900, is_active=True,
        ))
        with pytest.raises(IntegrityError):
            await db_session.flush()


class TestConversationCRUD:
    async def test_create(self, db_session):
        from app.domain.user.src.index import User
        from app.domain.community.src.index import Conversation
        user = User(openid="conv_user")
        db_session.add(user)
        await db_session.flush()
        conv = Conversation(
            user_id=user.id, session_id="sess-abc",
            messages={"messages": [{"role": "user", "content": "你好"}]},
            backend_type="dify",
        )
        db_session.add(conv)
        await db_session.flush()
        assert conv.session_id == "sess-abc"


# ---------------------------------------------------------------------------
# 4. Constraint enforcement
# ---------------------------------------------------------------------------


class TestConstraintEnforcement:
    async def test_user_realname_fk_enforced(self, db_session):
        from app.domain.user.src.index import UserRealname
        identity = UserRealname(
            user_id=99999, user_type="student", real_name="张三",
            id_card_number="11010519491231002X",
        )
        db_session.add(identity)
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_order_fk_enforced(self, db_session):
        from app.domain.order.src.index import Order
        order = Order(
            user_id=99999, order_kind="certification", product_type="H3C-NE", candidate_name="测试",
            candidate_phone="13800138000", price=9900,
        )
        db_session.add(order)
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_course_enrollment_fk_enforced(self, db_session):
        from app.domain.certification.src.index import CourseEnrollment
        enrollment = CourseEnrollment(user_id=99999, course_id=99999)
        db_session.add(enrollment)
        with pytest.raises(IntegrityError):
            await db_session.flush()
