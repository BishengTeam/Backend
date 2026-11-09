"""Integration tests for quiz activation codes (quiz018).

覆盖：批量生成/兑换（幂等、错误分支、作废）、目录双 schema 兼容契约、
练习闸门 403、访问模式转换表，以及激活码权益的会话同步。
"""

from __future__ import annotations

import asyncio
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.admin.quiz import router as admin_quiz_router
from app.domain.community.src.index import (
    QuizActivationCode,
    QuizAdminAuditLog,
    QuizKnowledgePoint,
    QuizLibrary,
    QuizLibraryEntitlement,
    QuizModule,
    QuizPracticeSession,
    QuizQuestion,
    QuizQuestionRevision,
)
from app.domain.user.src.index import AdminUser, User
from app.middleware.auth import require_normal_admin
from app.port.config import settings
from app.port.exceptions import BusinessException, QuizV2Exception, ValidationException
from app.schemas.admin_quiz_contract import (
    AdminQuizActivationCodeCreate,
    AdminQuizActivationCodeQuery,
    AdminQuizKnowledgePointCreate,
    AdminQuizLibraryAccessModeConvert,
    AdminQuizLibraryCreate,
    AdminQuizLibraryStatusUpdate,
    AdminQuizLibraryUpdate,
    AdminQuizModuleCreate,
    AdminQuizQuestionCreate,
    AdminQuizVersionRequest,
)
from app.services.admin_quiz_v2 import AdminQuizV2Service
from app.services.quiz_activation import (
    QuizActivationCodeService,
    format_activation_code,
    normalize_activation_code,
)
from app.services.quiz_v2 import QuizV2Service


pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


def _database_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    return url


_DISPLAY_CODE_RE = re.compile(r"^[2-9A-HJ-NP-Z]{4}(-[2-9A-HJ-NP-Z]{4}){3}$")


@pytest.fixture
async def activation_env(monkeypatch):
    engine = create_async_engine(_database_url(), pool_size=3, max_overflow=2)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"qac_{uuid4().hex[:12]}"

    @asynccontextmanager
    async def test_db_ctx():
        async with factory() as session:
            yield session

    monkeypatch.setattr(settings, "QUIZ_LEGACY_ACTIVATION_CODE_GENERATION_ENABLED", True)
    monkeypatch.setattr("app.services.quiz_activation.get_db_ctx", test_db_ctx)
    monkeypatch.setattr("app.services.quiz_v2.get_db_ctx", test_db_ctx)
    monkeypatch.setattr("app.services.admin_quiz_v2.get_db_ctx", test_db_ctx)

    async with factory() as db:
        admin = AdminUser(
            username=f"{prefix}_admin",
            password_hash="integration-test-only",
            role="quiz_admin",
        )
        db.add(admin)
        await db.flush()
        students = [
            User(openid=f"{prefix}_stu{index}", is_active=True) for index in range(2)
        ]
        db.add_all(students)
        await db.commit()
        for student in students:
            await db.refresh(student)

    env = SimpleNamespace(
        codes=QuizActivationCodeService(),
        catalog=QuizV2Service(),
        admin=AdminQuizV2Service(),
        factory=factory,
        prefix=prefix,
        admin_id=admin.id,
        student_a=students[0].id,
        student_b=students[1].id,
        library=None,
    )

    async def _cleanup_env() -> None:
        async with factory() as db:
            library_ids = list(
                (
                    await db.execute(
                        select(QuizLibrary.id).where(
                            QuizLibrary.name.like(f"{prefix}%")
                        )
                    )
                ).scalars()
            )
            await db.execute(
                delete(QuizLibraryEntitlement).where(
                    QuizLibraryEntitlement.library_id.in_(library_ids)
                )
            )
            await db.execute(
                delete(QuizActivationCode).where(
                    QuizActivationCode.library_id.in_(library_ids)
                )
            )
            await db.execute(
                delete(QuizPracticeSession).where(
                    QuizPracticeSession.library_id.in_(library_ids)
                )
            )
            await db.execute(
                delete(QuizAdminAuditLog).where(QuizAdminAuditLog.admin_id == admin.id)
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
            await db.execute(delete(User).where(User.openid.like(f"{prefix}%")))
            await db.execute(delete(AdminUser).where(AdminUser.id == admin.id))
            await db.commit()
        await engine.dispose()

    # 内容先建、再改访问模式、再发布、最后开 V2 入口：publish 门禁要求
    # 非 pending 访问模式且存在「模块→知识点→已发布题」路径（2026.11.03.3 教训）。
    # setup 阶段任何一步失败都必须内联清理，否则 super_admin 泄漏会
    # 连锁打挂后续所有人社/题库集成测试（uq_admin_user_single_super_admin）。
    try:
        library = await env.admin.create_library(
            AdminQuizLibraryCreate(name=f"{prefix}激活码题库"),
            admin_id=env.admin_id,
        )
        module = await env.admin.create_module(
            AdminQuizModuleCreate(library_id=library.id, name="激活码模块"),
            admin_id=env.admin_id,
        )
        point = await env.admin.create_knowledge_point(
            AdminQuizKnowledgePointCreate(module_id=module.id, name="激活码知识点"),
            admin_id=env.admin_id,
        )
        question = await env.admin.create_question(
            AdminQuizQuestionCreate(
                knowledge_point_id=point.id,
                question_type="single_choice",
                question_text=f"{prefix} 激活码题库题目？",
                options={"A": "是", "B": "否", "C": "不确定"},
                correct_answer="A",
                explanation="激活码集成测试题目。",
            ),
            admin_id=env.admin_id,
        )
        await env.admin.publish_question_revision(
            question.id,
            AdminQuizVersionRequest(lock_version=question.lock_version),
            admin_id=env.admin_id,
        )
        library = await env.admin.update_library(
            library.id,
            AdminQuizLibraryUpdate(
                lock_version=library.lock_version,
                access_mode="activation_code",
            ),
            admin_id=env.admin_id,
        )
        library = await env.admin.transition_library(
            library.id,
            AdminQuizLibraryStatusUpdate(
                action="publish", lock_version=library.lock_version
            ),
            admin_id=env.admin_id,
        )
        library = await env.admin.update_library(
            library.id,
            AdminQuizLibraryUpdate(
                lock_version=library.lock_version,
                v2_enabled=True,
            ),
            admin_id=env.admin_id,
        )
        env.library = library
    except Exception:
        await _cleanup_env()
        raise

    try:
        yield env
    finally:
        await _cleanup_env()


def test_normalize_strips_separators_and_uppercases():
    assert normalize_activation_code(" ab-cd 12ef ") == "ABCD12EF"
    assert format_activation_code("ABCD1234EFGH5678") == "ABCD-1234-EFGH-5678"

    with pytest.raises(ValidationException):
        normalize_activation_code("   ")


async def test_generate_codes_display_format_and_normalized_storage(activation_env):
    env = activation_env
    codes = (await env.codes.generate_codes(
        env.library.id,
        AdminQuizActivationCodeCreate(quantity=3, note="首批"),
        admin_id=env.admin_id,
    )).codes
    assert len(codes) == 3
    assert len(set(codes)) == 3
    for display in codes:
        assert _DISPLAY_CODE_RE.match(display)

    async with env.factory() as db:
        rows = list(
            (
                await db.execute(
                    select(QuizActivationCode).where(
                        QuizActivationCode.library_id == env.library.id
                    )
                )
            ).scalars()
        )
    assert len(rows) == 3
    for row in rows:
        assert row.status == "issued"
        assert row.code == normalize_activation_code(format_activation_code(row.code))
        assert row.note == "首批"


async def test_redeem_grants_entitlement_and_marks_code(activation_env):
    env = activation_env
    (display,) = (await env.codes.generate_codes(
        env.library.id,
        AdminQuizActivationCodeCreate(quantity=1),
        admin_id=env.admin_id,
    )).codes
    result = await env.codes.redeem(env.student_a, env.library.id, display.lower())
    assert result.library_id == int(env.library.id)
    assert result.activated is True

    async with env.factory() as db:
        code = (
            await db.execute(
                select(QuizActivationCode).where(
                    QuizActivationCode.code == normalize_activation_code(display)
                )
            )
        ).scalar_one()
        assert code.status == "redeemed"
        assert int(code.redeemed_by) == int(env.student_a)
        assert code.redeemed_at is not None
        entitlement = (
            await db.execute(
                select(QuizLibraryEntitlement).where(
                    QuizLibraryEntitlement.activation_code_id == code.id
                )
            )
        ).scalar_one()
        assert int(entitlement.user_id) == int(env.student_a)
        assert entitlement.source_type == "activation_code"
        assert entitlement.status == "active"
        assert entitlement.ends_at is None
        assert entitlement.snapshot["activation_code"] == display


async def test_redeem_is_idempotent_for_same_user_only(activation_env):
    env = activation_env
    (display,) = (await env.codes.generate_codes(
        env.library.id,
        AdminQuizActivationCodeCreate(quantity=1),
        admin_id=env.admin_id,
    )).codes
    first = await env.codes.redeem(env.student_a, env.library.id, display)
    again = await env.codes.redeem(env.student_a, env.library.id, display)
    assert first.activated and again.activated

    with pytest.raises(ValidationException, match="已被使用"):
        await env.codes.redeem(env.student_b, env.library.id, display)

    async with env.factory() as db:
        count = await db.scalar(
            select(func.count()).select_from(QuizLibraryEntitlement)
        )
    assert count == 1


async def test_redeem_rejects_unknown_and_cross_library_codes(activation_env):
    env = activation_env
    other_library = await env.admin.create_library(
        AdminQuizLibraryCreate(name=f"{env.prefix}另一题库"),
        admin_id=env.admin_id,
    )
    with pytest.raises(ValidationException, match="不存在"):
        await env.codes.redeem(env.student_a, env.library.id, "AAAA-BBBB-CCCC-DDDD")
    (display,) = (await env.codes.generate_codes(
        env.library.id,
        AdminQuizActivationCodeCreate(quantity=1),
        admin_id=env.admin_id,
    )).codes
    with pytest.raises(ValidationException, match="不匹配"):
        await env.codes.redeem(env.student_a, other_library.id, display)


async def test_revoke_only_issued_codes(activation_env):
    env = activation_env
    codes = (await env.codes.generate_codes(
        env.library.id,
        AdminQuizActivationCodeCreate(quantity=2),
        admin_id=env.admin_id,
    )).codes
    async with env.factory() as db:
        rows = list(
            (
                await db.execute(
                    select(QuizActivationCode)
                    .where(QuizActivationCode.library_id == env.library.id)
                    .order_by(QuizActivationCode.id.asc())
                )
            ).scalars()
        )
    issued_id, redeemable_id = rows[0].id, rows[1].id

    await env.codes.revoke_code(issued_id, admin_id=env.admin_id)
    with pytest.raises(ValidationException, match="已作废"):
        await env.codes.redeem(env.student_b, env.library.id, codes[0])

    await env.codes.redeem(env.student_a, env.library.id, codes[1])
    with pytest.raises(BusinessException, match="已兑换"):
        await env.codes.revoke_code(redeemable_id, admin_id=env.admin_id)


async def test_list_codes_filters_by_status(activation_env):
    env = activation_env
    codes = (await env.codes.generate_codes(
        env.library.id,
        AdminQuizActivationCodeCreate(quantity=2),
        admin_id=env.admin_id,
    )).codes
    await env.codes.redeem(env.student_a, env.library.id, codes[0])

    issued = await env.codes.list_codes(
        AdminQuizActivationCodeQuery(library_id=env.library.id, status="issued")
    )
    redeemed = await env.codes.list_codes(
        AdminQuizActivationCodeQuery(library_id=env.library.id, status="redeemed")
    )
    assert issued.total == 1
    assert redeemed.total == 1
    assert redeemed.items[0].redeemed_by == int(env.student_a)
    assert redeemed.items[0].code == codes[0]


async def test_export_rows_covers_all_codes(activation_env):
    env = activation_env
    codes = (await env.codes.generate_codes(
        env.library.id,
        AdminQuizActivationCodeCreate(quantity=2, note="导出"),
        admin_id=env.admin_id,
    )).codes
    await env.codes.redeem(env.student_a, env.library.id, codes[0])
    rows = await env.codes.export_rows(env.library.id, None)
    assert len(rows) == 2
    assert rows[0][0] == codes[0]
    assert rows[0][1] == "已兑换"
    assert rows[1][1] == "未兑换"
    assert rows[1][5] == "导出"


@pytest.mark.parametrize("days", [1, 3, 7, 30, 60, 90, 365])
async def test_timed_code_batch_and_audit(activation_env, monkeypatch, days):
    env = activation_env
    now = datetime(2026, 11, 5, 2, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.quiz_activation._now", lambda: now)
    batch = await env.codes.generate_codes(
        env.library.id, AdminQuizActivationCodeCreate(quantity=2, validity_days=days),
        admin_id=env.admin_id, schema_v2=True,
    )
    deadline = now + timedelta(days=days)
    assert batch.expires_at == deadline
    async with env.factory() as db:
        rows = (await db.scalars(select(QuizActivationCode).where(
            QuizActivationCode.library_id == env.library.id,
        ))).all()
        assert len(rows) == 2
        assert all(row.expires_at == deadline and row.created_at == now for row in rows)
        audit = await db.scalar(select(QuizAdminAuditLog).where(
            QuizAdminAuditLog.admin_id == env.admin_id,
            QuizAdminAuditLog.action == "library.generate_activation_codes",
        ))
        assert audit.changed_fields == {
            "validity_days": {"before": None, "after": days},
            "expires_at": {"before": None, "after": deadline.isoformat()},
        }


async def test_exact_deadline_idempotence_and_versioned_api(activation_env, monkeypatch):
    env = activation_env
    now = datetime(2026, 11, 5, 2, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.quiz_activation._now", lambda: now)
    app = FastAPI()
    app.include_router(admin_quiz_router)
    app.dependency_overrides[require_normal_admin] = lambda: SimpleNamespace(id=env.admin_id, role="quiz_admin")
    path = f"/quiz/libraries/{env.library.id}/activation-codes"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        legacy_libraries = (await client.get("/quiz/libraries", params={"keyword": env.prefix})).json()["data"]
        assert "practice_counts" not in legacy_libraries[0]
        current_libraries = (await client.get("/quiz/libraries", params={
            "keyword": env.prefix, "count_schema": "2",
        })).json()["data"]
        assert current_libraries[0]["question_count"] == current_libraries[0]["practice_counts"]["practicable"] == 1
        legacy = await client.post(path, json={})
        assert legacy.status_code == 200 and set(legacy.json()["data"]) == {"codes"}
        for invalid in (2, 366, True, 1.0, "1"):
            assert (await client.post(path, params={"code_schema": "2"}, json={"validity_days": invalid})).status_code == 422
        timed = await client.post(path, params={"code_schema": "2"}, json={"quantity": 2, "validity_days": 1})
        assert timed.status_code == 200
        data = timed.json()["data"]
        valid, expired = data["codes"]
        deadline = datetime.fromisoformat(data["expires_at"])
        now = deadline - timedelta(microseconds=1)
        assert (await env.codes.redeem(env.student_a, env.library.id, valid)).activated
        now = deadline
        assert (await env.codes.redeem(env.student_a, env.library.id, valid)).activated
        with pytest.raises(ValidationException, match="激活码已被使用"):
            await env.codes.redeem(env.student_b, env.library.id, valid)
        with pytest.raises(ValidationException, match="激活码已过期"):
            await env.codes.redeem(env.student_b, env.library.id, expired)
        assert (await env.codes.redeem(env.student_b, env.library.id, legacy.json()["data"]["codes"][0])).activated

        old_page = (await client.get(path)).json()["data"]
        assert old_page["total"] == 2
        assert all(item["status"] == "redeemed" and "expires_at" not in item for item in old_page["items"])
        old_issued = (await client.get(path, params={"status": "issued"})).json()["data"]
        assert old_issued["total"] == 0
        old_csv = (await client.get(f"{path}/export")).text
        assert "兑换截止时间" not in old_csv and expired not in old_csv
        issued = await client.get(path, params={"status": "issued", "code_schema": "2"})
        assert issued.json()["data"]["total"] == 0
        expired_filter = {"status": "expired", "code_schema": "2"}
        page = (await client.get(path, params=expired_filter)).json()["data"]
        assert page["total"] == 1
        assert page["items"][0]["code"] == expired and page["items"][0]["status"] == "expired"
        assert datetime.fromisoformat(page["items"][0]["expires_at"]) == deadline
        csv = (await client.get(f"{path}/export", params=expired_filter)).text
        assert "兑换截止时间" in csv and "已过期" in csv and expired in csv and valid not in csv
        with pytest.raises(BusinessException, match="已过期"):
            await env.codes.revoke_code(page["items"][0]["id"], admin_id=env.admin_id)

    monkeypatch.setattr(settings, "QUIZ_LEGACY_ACTIVATION_CODE_GENERATION_ENABLED", False)
    with pytest.raises(ValidationException, match="新版页面"):
        await env.codes.generate_codes(env.library.id, AdminQuizActivationCodeCreate(), admin_id=env.admin_id)
    with pytest.raises(ValidationException, match="请选择兑换有效期"):
        await env.codes.generate_codes(
            env.library.id, AdminQuizActivationCodeCreate(), admin_id=env.admin_id, schema_v2=True,
        )


@pytest.mark.parametrize("competing_action", ["redeem", "revoke"])
async def test_timed_code_concurrent_actions(activation_env, competing_action):
    env = activation_env
    batch = await env.codes.generate_codes(
        env.library.id, AdminQuizActivationCodeCreate(validity_days=1),
        admin_id=env.admin_id, schema_v2=True,
    )
    (code,) = batch.codes
    async with env.factory() as db:
        code_id = await db.scalar(select(QuizActivationCode.id).where(
            QuizActivationCode.code == normalize_activation_code(code),
        ))
    other = (
        env.codes.redeem(env.student_b, env.library.id, code)
        if competing_action == "redeem"
        else env.codes.revoke_code(code_id, admin_id=env.admin_id)
    )
    results = await asyncio.gather(
        env.codes.redeem(env.student_a, env.library.id, code), other, return_exceptions=True,
    )
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert any(isinstance(result, (ValidationException, BusinessException)) for result in results)
    async with env.factory() as db:
        row = await db.get(QuizActivationCode, code_id)
        count = await db.scalar(select(func.count()).select_from(QuizLibraryEntitlement).where(
            QuizLibraryEntitlement.activation_code_id == code_id,
        ))
        assert row.status in {"redeemed", "revoked"}
        assert count == (1 if row.status == "redeemed" else 0)


async def test_redemption_checks_expiry_after_waiting_for_row_lock(activation_env, monkeypatch):
    env = activation_env
    batch = await env.codes.generate_codes(
        env.library.id, AdminQuizActivationCodeCreate(validity_days=1),
        admin_id=env.admin_id, schema_v2=True,
    )
    (code,) = batch.codes
    now = batch.expires_at - timedelta(seconds=1)
    monkeypatch.setattr("app.services.quiz_activation._now", lambda: now)
    async with env.factory() as db:
        stmt = select(QuizActivationCode).where(
            QuizActivationCode.code == normalize_activation_code(code)
        ).with_for_update()
        await db.scalar(stmt)
        lock_requested = asyncio.Event()
        engine = db.bind.sync_engine

        def observe_lock(_conn, _cursor, statement, _params, _context, _executemany):
            if "quiz_activation_code" in statement and "FOR UPDATE" in statement:
                lock_requested.set()

        event.listen(engine, "before_cursor_execute", observe_lock)
        redemption = asyncio.create_task(env.codes.redeem(env.student_a, env.library.id, code))
        try:
            await asyncio.wait_for(lock_requested.wait(), timeout=5)
            now = batch.expires_at
        finally:
            event.remove(engine, "before_cursor_execute", observe_lock)
            await db.rollback()
        with pytest.raises(ValidationException, match="激活码已过期"):
            await redemption


async def test_catalog_legacy_contract_hides_activation_library(activation_env):
    env = activation_env
    legacy = await env.catalog.list_libraries(env.student_a)
    assert all(item.access_mode != "activation_code" for item in legacy)
    assert all("accessible" not in item.model_dump() for item in legacy)

    schema_v2 = await env.catalog.list_libraries(env.student_a, schema="2")
    activation_items = [
        item for item in schema_v2 if item.access_mode == "activation_code"
    ]
    assert len(activation_items) == 1
    assert activation_items[0].id == int(env.library.id)
    assert activation_items[0].accessible is False


async def test_catalog_schema2_reflects_activation_state(activation_env):
    env = activation_env
    (display,) = (await env.codes.generate_codes(
        env.library.id,
        AdminQuizActivationCodeCreate(quantity=1),
        admin_id=env.admin_id,
    )).codes
    before = await env.catalog.list_libraries(env.student_a, schema="2")
    locked = next(item for item in before if item.id == int(env.library.id))
    assert locked.accessible is False

    await env.codes.redeem(env.student_a, env.library.id, display)
    after = await env.catalog.list_libraries(env.student_a, schema="2")
    unlocked = next(item for item in after if item.id == int(env.library.id))
    assert unlocked.accessible is True

    other_view = await env.catalog.list_libraries(env.student_b, schema="2")
    still_locked = next(item for item in other_view if item.id == int(env.library.id))
    assert still_locked.accessible is False


async def test_get_library_schema_contract(activation_env):
    env = activation_env
    with pytest.raises(QuizV2Exception) as legacy_exc:
        await env.catalog.get_library(env.student_a, env.library.id)
    assert legacy_exc.value.http_status_code == 404

    detail = await env.catalog.get_library(env.student_a, env.library.id, schema="2")
    assert detail.accessible is False
    assert detail.modules, "目录公开库详情应包含模块树"

    anonymous = await env.catalog.get_library(None, env.library.id, schema="2")
    assert anonymous.accessible is False

    with pytest.raises(QuizV2Exception) as legacy_anonymous_exc:
        await env.catalog.get_library(None, env.library.id)
    # 旧契约对激活码库统一按不存在处理（含匿名），绝不吐出新模式枚举值。
    assert legacy_anonymous_exc.value.http_status_code == 404


async def test_practice_requires_activation(activation_env):
    env = activation_env
    async with env.factory() as db:
        with pytest.raises(QuizV2Exception) as locked:
            await env.catalog._require_accessible_library(
                db, env.student_a, env.library.id
            )
    assert locked.value.http_status_code == 403
    assert locked.value.detail["reason"] == "quiz_activation_required"


async def test_session_state_follows_activation(activation_env):
    env = activation_env
    now = datetime.now(timezone.utc)
    async with env.factory() as db:
        session = QuizPracticeSession(
            user_id=env.student_a,
            library_id=env.library.id,
            scope_type="library",
            scope_id=env.library.id,
            mode="full",
            requested_count=10,
            actual_count=10,
            status="in_progress",
            started_at=now,
            expires_at=now + timedelta(days=7),
            lock_version=1,
        )
        db.add(session)
        await db.commit()
        session_id = session.id

    async with env.factory() as db:
        stored = await db.get(QuizPracticeSession, session_id)
        state, reason, _at = await env.catalog._session_access_state(db, stored, now)
    assert (state, reason) == ("paused", "quiz_entitlement_inactive")

    (display,) = (await env.codes.generate_codes(
        env.library.id,
        AdminQuizActivationCodeCreate(quantity=1),
        admin_id=env.admin_id,
    )).codes
    await env.codes.redeem(env.student_a, env.library.id, display)
    async with env.factory() as db:
        stored = await db.get(QuizPracticeSession, session_id)
        state, reason, _at = await env.catalog._session_access_state(
            db, stored, datetime.now(timezone.utc)
        )
    assert (state, reason) == ("active", None)


async def test_convert_between_free_and_activation_code(activation_env):
    env = activation_env
    library = await env.admin.create_library(
        AdminQuizLibraryCreate(name=f"{env.prefix}转换题库"),
        admin_id=env.admin_id,
    )
    converted = await env.admin.convert_library_access_mode(
        library.id,
        AdminQuizLibraryAccessModeConvert(
            lock_version=library.lock_version,
            target_mode="activation_code",
        ),
        admin_id=env.admin_id,
    )
    assert converted.library.access_mode == "activation_code"
    back = await env.admin.convert_library_access_mode(
        library.id,
        AdminQuizLibraryAccessModeConvert(
            lock_version=converted.library.lock_version,
            target_mode="free",
        ),
        admin_id=env.admin_id,
    )
    assert back.library.access_mode == "free"
