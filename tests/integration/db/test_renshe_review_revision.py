"""Real submission/review transactions and private storage; isolated PostgreSQL schema."""
import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.domain.order.src.index import Order
from app.domain.renshe.src.index import RensheApplication, RensheReview
from app.port.exceptions import ConflictException
from app.services.order_fulfillment import OrderFulfillmentService
from app.services.renshe_application import RensheApplicationService
from app.services.renshe_review import RensheReviewService
from tests.unit import test_renshe_projects as projects
from tests.unit.test_renshe_graduated import graduated_batch, uploads, draft_payload

pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


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
    names = {"cert_product_catalog", "cert_product", "renshe_project_config", "user", "user_realname", "user_student", "admin_user", "plan", "order", "h3c_refund_request", "nisp_refund_request", "payment_refund_task", "cert_consultation_group"}
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
