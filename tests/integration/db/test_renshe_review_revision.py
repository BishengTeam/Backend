"""Real submission/review transactions and private storage; isolated PostgreSQL schema."""
import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.domain.order.src.index import Order
from app.domain.renshe.src.index import RensheApplication, RensheAuditLog, RensheReview
from app.port.exceptions import ConflictException
from app.services.order_fulfillment import OrderFulfillmentService
from app.services.renshe_application import RensheApplicationService
from app.services.renshe_review import RensheReviewService
from app.services.plan import PlanService
from app.schemas.plan import PlanUpdate
from app.domain.plan.src.index import Plan
from app.port.exceptions import AppException
from tests.unit import test_renshe_projects as projects
from tests.unit.test_renshe_projects import create_batch
from tests.unit.test_renshe_graduated import graduated_batch, uploads, draft_payload

pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


async def test_renshe_notice_confirmation_is_saved_with_submitted_material_version(pg_env, monkeypatch):
    from app.models.certification_journey import CertificationJourneyContent
    from app.domain.renshe.src.index import RensheApplicationVersion
    from app.services.certification_journey import CertificationJourneyService, user_journey
    env = pg_env
    monkeypatch.setattr('app.services.certification_journey.get_db_ctx', env.db)
    async with env.db() as db:
        connection = await db.connection()
        await connection.run_sync(lambda bind: CertificationJourneyContent.__table__.create(bind, checkfirst=True))
        await db.commit()
    _, batch = await graduated_batch(env, monkeypatch)
    service = RensheApplicationService(env.storage)
    await uploads(service, batch)
    draft = await service.save_draft(1, draft_payload(batch))
    notice = await CertificationJourneyService().notice('renshe', batch.id)
    submitted = await service.submit(1, draft.id, notice_hash=notice['notice_hash'], require_notice=True)
    async with env.db() as db:
        version = await db.get(RensheApplicationVersion, submitted.version.id)
        assert version.form_data['enrollment_notice']['notice_hash'] == notice['notice_hash']
        assert version.form_data['enrollment_notice']['subject']['id'] == batch.product_type
        assert version.form_data['enrollment_notice']['requirements']['reporting_policy']['commitment_template']
    journey = await user_journey('renshe', draft.id, 1)
    assert journey['stages'][3] == '外审' and journey['progress']['stage'] == 0
    assert journey['exam_org_name'] == ""

    # 提交后修改 Plan：历史报名仍显示提交时冻结的空机构。
    async with env.db() as db:
        plan = await db.get(Plan, batch.id)
        plan.exam_org_name = "提交后新增的机构"
        await db.commit()
    frozen = await user_journey('renshe', draft.id, 1)
    assert frozen['exam_org_name'] == ""

    # 旧版本完全没有该 key 时才回退当前 Plan。
    async with env.db() as db:
        version = await db.get(RensheApplicationVersion, submitted.version.id)
        version.batch_snapshot = {}
        await db.commit()
    legacy = await user_journey('renshe', draft.id, 1)
    assert legacy['exam_org_name'] == "提交后新增的机构"


async def test_renshe_update_plan_can_clear_exam_date_without_server_error(pg_env, monkeypatch):
    env = pg_env
    project, published = await graduated_batch(env, monkeypatch)
    draft = await create_batch(project, name="草稿清空考试日期")

    cleared = await PlanService().update_plan(
        draft.id,
        PlanUpdate(confirm_holiday=False, exam_date=None),
        project.code,
        admin_id=1,
    )
    assert cleared.exam_date is None

    await PlanService().update_plan(
        draft.id,
        PlanUpdate(confirm_holiday=False, exam_org_name="机构A"),
        project.code,
        admin_id=1,
    )
    async with env.db() as db:
        logs = list((await db.scalars(
            select(RensheAuditLog).where(
                RensheAuditLog.action == "renshe.batch.org_change"
            ).order_by(RensheAuditLog.id)
        )).all())
    assert logs[-1].summary["changes"]["exam_org_name"] == {
        "old": None,
        "new": "机构A",
    }

    before = len(logs)
    await PlanService().update_plan(
        draft.id,
        PlanUpdate(confirm_holiday=False, exam_org_name="机构A"),
        project.code,
        admin_id=1,
    )
    async with env.db() as db:
        count_same = await db.scalar(
            select(func.count()).select_from(RensheAuditLog).where(
                RensheAuditLog.action == "renshe.batch.org_change"
            )
        )
    assert count_same == before

    with pytest.raises(AppException):
        await PlanService().update_plan(
            published.id,
            PlanUpdate(confirm_holiday=False, exam_date=None),
            project.code,
            admin_id=1,
        )


async def test_published_single_price_change_is_audited(pg_env, monkeypatch):
    env = pg_env
    project, published = await graduated_batch(env, monkeypatch)
    assert published.price_cents == 50100
    await PlanService().update_plan(
        published.id,
        PlanUpdate(confirm_holiday=False, price_cents=77700),
        project.code,
        admin_id=1,
    )
    async with env.db() as db:
        log = await db.scalar(
            select(RensheAuditLog)
            .where(
                RensheAuditLog.action == "renshe.batch.price_change",
                RensheAuditLog.object_id == published.id,
            )
            .order_by(RensheAuditLog.id.desc())
        )
    assert log is not None
    assert log.summary == {"old": 50100, "new": 77700}


async def test_disabled_project_still_allows_historical_correction_draft(pg_env, monkeypatch):
    env = pg_env
    _, batch = await graduated_batch(env, monkeypatch)
    service = RensheApplicationService(env.storage)
    await uploads(service, batch)
    draft = await service.save_draft(1, draft_payload(batch))

    from app.domain.renshe.src.index import RensheBatchProject
    from app.models.cert_product import CertProduct

    async with env.db() as db, db.begin():
        product = await db.scalar(
            select(CertProduct).where(CertProduct.code == batch.product_type)
        )
        project = RensheBatchProject(
            plan_id=batch.id,
            product_id=product.id,
            config_id=batch.renshe_config_id,
            price_cents=100,
            is_active=False,
        )
        db.add(project)
        await db.flush()
        project_id = project.id
        application = await db.get(RensheApplication, draft.id)
        application.batch_project_id = project.id

    updated = await service.save_draft(
        1, draft_payload(batch, contact_phone="13900139000")
    )
    assert updated.id == draft.id
    async with env.db() as db:
        application = await db.get(RensheApplication, draft.id)
        assert application.batch_project_id == project_id
        assert application.draft_data["contact_phone"] == "13900139000"


@pytest.fixture
async def pg_env(monkeypatch, tmp_path):
    schema = "review_test_" + uuid4().hex
    url = os.environ["TEST_DATABASE_URL"]
    admin_engine = create_async_engine(url, poolclass=NullPool)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        # The actual deployed test schema must also contain the migrated column.
        assert await connection.scalar(text("SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='renshe_review' AND column_name='checklist_snapshot'")) == 1
    monkeypatch.setattr(projects, "create_async_engine", lambda _: create_async_engine(url,
        poolclass=NullPool, connect_args={"server_settings": {"search_path": schema}}))
    from app.adapter.database import Base
    names = {"cert_product_catalog", "cert_product", "renshe_project_config", "holiday", "user", "user_realname", "user_student", "admin_user", "plan", "order", "h3c_refund_request", "nisp_refund_request", "payment_refund_task", "cert_consultation_group"}
    names.update(name for name in Base.metadata.tables if name.startswith(("renshe_", "offline_")))
    pending = list(names)
    while pending:
        for foreign_key in Base.metadata.tables[pending.pop()].foreign_keys:
            dependency = foreign_key.column.table.name
            if dependency not in names:
                names.add(dependency)
                pending.append(dependency)
    schema_engine = projects.create_async_engine("")
    async with schema_engine.begin() as connection:
        await connection.run_sync(lambda db: Base.metadata.create_all(db, tables=[Base.metadata.tables[name] for name in names]))
    await schema_engine.dispose()
    fixture = projects.isolated.__wrapped__(monkeypatch, tmp_path)
    try:
        yield await anext(fixture)
    finally:
        await fixture.aclose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin_engine.dispose()


async def test_real_correction_blocks_old_review_and_snapshots_new_definition(pg_env, monkeypatch):
    env = pg_env
    _, batch = await graduated_batch(env, monkeypatch)
    application_service = RensheApplicationService(env.storage)
    review_service = RensheReviewService()
    await uploads(application_service, batch)
    draft = await application_service.save_draft(1, draft_payload(batch))
    original = await application_service.submit(1, draft.id)
    from datetime import datetime, timezone
    async with env.db() as db:
        order = await db.get(Order, original.order_id)
        order.payment_channel, order.offline_status = "wechat", None
        order.status, order.transaction_id, order.paid_at = "paid", "review-race", datetime.now(timezone.utc)
        await OrderFulfillmentService().on_paid(db, order)
        await db.commit()
    old_payload = await projects.review_payload(env, draft.id)
    await review_service.review(reviewer_id=1, application_id=draft.id, stage="initial",
        data=await projects.review_payload(env, draft.id, decision="rejected", reason="专业需补正", required_changes=["education_info.major"]))
    updated = draft_payload(batch)
    updated.education_info.major = "信息安全"
    await application_service.save_draft(1, updated)
    copying, release = asyncio.Event(), asyncio.Event()
    copy = env.storage.copy_version_material

    async def pause_copy(**kwargs):
        copying.set()
        await release.wait()
        return await copy(**kwargs)

    monkeypatch.setattr(env.storage, "copy_version_material", pause_copy)
    submit = asyncio.create_task(application_service.submit(1, draft.id))
    try:
        await asyncio.wait_for(copying.wait(), 10)
        stale_review = asyncio.create_task(review_service.review(reviewer_id=1, application_id=draft.id,
            stage="initial", data=old_payload))
        await asyncio.sleep(0.15)
        assert not stale_review.done(), "review must wait on the real submission lock"
        release.set()
        result = await asyncio.wait_for(submit, 10)
        with pytest.raises(ConflictException, match="版本已变化"):
            await asyncio.wait_for(stale_review, 10)
    finally:
        release.set()
        await asyncio.gather(submit, return_exceptions=True)
    assert result.version.version_no == 2
    assert result.order_id == original.order_id and not result.payment_required
    async with env.db() as db:
        assert (await db.get(RensheApplication, draft.id)).status == "pending_initial_review"
        assert len(list(await db.scalars(select(RensheReview)))) == 1
    approved = await review_service.review(reviewer_id=1, application_id=draft.id, stage="initial",
        data=await projects.review_payload(env, draft.id))
    async with env.db() as db:
        row = await db.get(RensheReview, approved.id)
        assert row.checklist_snapshot["version_id"] == result.version.id
        assert row.checklist_snapshot["project_config_id"] == batch.renshe_config_id
        assert {check["key"] for check in row.checklist_snapshot["checks"]} == set(row.checklist)
        assert all(row.checklist.values())
