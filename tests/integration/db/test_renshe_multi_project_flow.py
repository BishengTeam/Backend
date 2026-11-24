"""人社多项目批次：发布 → catalog → 草稿 → 材料 → 提交 → 订单项目归属。"""
import shutil
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from openpyxl import load_workbook
from sqlalchemy import delete, select

from app.domain.order.src.index import Order
from app.domain.plan.src.index import Plan
from app.domain.renshe.src.index import (
    RensheApplication,
    RensheApplicationVersion,
    RensheAuditLog,
    RensheBatchProject,
    RensheExportVolume,
    RensheRefundRequest,
)
from app.domain.user.src.index import UserRealname, UserStudent
from app.port.config import settings
from app.port.exceptions import ConflictException, NotFoundException
from app.schemas.plan import PlanCreate, PlanProjectInput
from app.schemas.plan import PlanUpdate
from app.schemas.renshe_project import RensheProjectWrite
from app.schemas.renshe_public import public_plan
from app.services.certification_journey import CertificationJourneyService, user_journey
from app.services.order_fulfillment import OrderFulfillmentService
from app.services.plan import PlanService
from app.services.renshe_application import RensheApplicationService
from app.services.renshe_audit import RensheAuditQueryService
from app.services.renshe_graduated import save_template
from app.services.renshe_export import RensheExportService
from app.services.renshe_project import RensheProjectService, graduated_configuration
from app.services.renshe_review import RensheReviewService
from app.services.renshe_reporting import default_policy
from tests.integration.db.test_renshe_review_revision import pg_env  # noqa: F401
from tests.unit.test_renshe_graduated import draft_payload
from tests.unit.test_renshe_projects import review_payload, verified_student

pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


def graduated_project_config() -> dict:
    config = graduated_configuration()
    config["reporting_policy"] = {
        **default_policy(),
        "version": "fixture-v2",
        "confirmed_by_client": True,
        "commitment_template": save_template("测试模板.pdf", b"%PDF-1.4\nfixture"),
        "employment_reporting": {
            "confirmed": True,
            "date_rule": "work_or_enrollment",
            "years_rule": "required",
            "allow_zero_years": True,
        },
    }
    return config


async def multi_project_batch(env, monkeypatch):
    root = env.root / "templates"
    root.mkdir()
    for name in ("报名信息.xlsx", "工作经历.xlsx"):
        shutil.copyfile(Path(settings.RENSHE_TEMPLATE_DIR) / name, root / name)
    monkeypatch.setattr(settings, "RENSHE_TEMPLATE_DIR", str(root))
    config = graduated_project_config()
    first = await RensheProjectService().save(
        RensheProjectWrite(chinese_name="多项目一级", status="available", config=config),
        admin_id=1,
    )
    second = await RensheProjectService().save(
        RensheProjectWrite(chinese_name="多项目二级", status="available", config=config),
        admin_id=1,
    )
    now = datetime.now(timezone.utc)
    batch = await PlanService().create_renshe_multi_batch(
        PlanCreate(
            name="多项目批次一",
            projects=[
                PlanProjectInput(product_id=first.id, price_cents=38000),
                PlanProjectInput(product_id=second.id, price_cents=45000),
            ],
            apply_start=now - timedelta(hours=1),
            apply_end=now + timedelta(days=10),
            exam_date=now + timedelta(days=15),
            exam_location="测试考点",
            description="多项目批次说明",
            contact_name="老师",
            contact_phone="13800138000",
        ),
        admin_id=1,
    )
    await PlanService().publish_plan(batch.id, None, admin_id=1)
    await verified_student(env)
    async with env.db() as db:
        await db.execute(delete(UserStudent))
        identity = await db.scalar(select(UserRealname))
        identity.user_type = "social"
        await db.commit()
    projects = {}
    async with env.db() as db:
        for project in (await db.scalars(
            select(RensheBatchProject).where(RensheBatchProject.plan_id == batch.id)
        )).all():
            projects[project.product_id] = project.id
    return first, second, batch, projects


async def upload_project_materials(service, plan_id, config, batch_project_id=None):
    for requirement in config["materials"]:
        kind = requirement["kind"]
        if kind in {"id_card_front", "id_card_back", "portrait"}:
            continue
        pdf = kind in {"education_report", "xuexin_registration", "commitment_letter"}
        await service.upload_application_material(
            1,
            plan_id,
            kind,
            filename="实际原文件.pdf" if pdf else "证明材料.jpeg",
            content_type="application/pdf" if pdf else "image/jpeg",
            content=b"%PDF-1.4\nfixture" if pdf else b"\xff\xd8\xfffixture",
            batch_project_id=batch_project_id,
        )
    return config


async def test_multi_project_batch_publishes_and_lists_every_project(pg_env, monkeypatch):
    env = pg_env
    monkeypatch.setattr("app.services.certification_journey.get_db_ctx", env.db)
    first, second, batch, projects = await multi_project_batch(env, monkeypatch)

    catalog = await CertificationJourneyService().catalog("renshe")
    entries = [item for item in catalog["batches"] if item["plan_id"] == batch.id]
    assert {item["batch_project_id"] for item in entries} == {
        projects[first.id],
        projects[second.id],
    }
    by_subject = {item["subject_id"]: item for item in entries}
    assert by_subject[first.code]["prices"][0]["price_cents"] == 38000
    assert by_subject[second.code]["prices"][0]["price_cents"] == 45000

    async with env.db() as db:
        logs = list((await db.scalars(
            select(RensheAuditLog).where(
                RensheAuditLog.action == "batch.publish",
                RensheAuditLog.object_id == batch.id,
            )
        )).all())
    assert logs, "多项目批次发布必须写人社审计"


async def test_multi_project_submission_creates_project_order(pg_env, monkeypatch):
    env = pg_env
    monkeypatch.setattr("app.services.certification_journey.get_db_ctx", env.db)
    from app.models.certification_journey import CertificationJourneyContent

    async with env.db() as db:
        connection = await db.connection()
        await connection.run_sync(
            lambda bind: CertificationJourneyContent.__table__.create(
                bind, checkfirst=True
            )
        )
        await db.commit()
    first, second, batch, projects = await multi_project_batch(env, monkeypatch)
    chosen = projects[first.id]
    service = RensheApplicationService(env.storage)

    draft = await service.save_draft(
        1, draft_payload(batch, batch_project_id=chosen)
    )
    reused = await service.save_draft(
        1, draft_payload(batch, batch_project_id=chosen)
    )
    assert reused.id == draft.id
    assert reused.reused is True
    second_draft = await service.save_draft(
        1, draft_payload(batch, batch_project_id=projects[second.id])
    )
    assert second_draft.id != draft.id
    assert second_draft.batch_project_id == projects[second.id]
    listed = await service.list_applications(1, plan_id=batch.id)
    assert any(item.id == draft.id for item in listed.items)
    assert any(item.id == second_draft.id for item in listed.items)
    other_project = await service.list_applications(
        1, plan_id=batch.id, batch_project_id=projects[second.id]
    )
    assert other_project.total == 1
    async with env.db() as db:
        application = await db.get(RensheApplication, draft.id)
        assert application.batch_project_id == chosen

    await upload_project_materials(
        service, batch.id, graduated_project_config(), batch_project_id=chosen
    )
    submitted = await service.submit(1, draft.id)

    async with env.db() as db:
        order = await db.get(Order, submitted.order_id)
        version = await db.get(RensheApplicationVersion, submitted.version.id)
        project = await db.get(RensheBatchProject, chosen)
    assert order.product_type == first.code
    assert order.price == 38000
    assert order.extra_data["batch_project_id"] == chosen
    assert order.extra_data["project_config_id"] == project.config_id
    assert version.project_config_id == project.config_id

    journey = await user_journey("renshe", draft.id, 1)
    assert journey["subject"] == first.chinese_name
    detail = await service.get_application(1, draft.id)
    assert detail.plan.occupation_name == first.config.occupation_name
    assert detail.plan.skill_level == first.config.skill_level
    assert detail.plan.price_cents == 38000

    async with env.db() as db:
        order = await db.get(Order, submitted.order_id)
        locked = await OrderFulfillmentService()._lock_renshe_application(db, order)
        assert locked.id == draft.id
        order.payment_channel, order.offline_status = "wechat", None
        order.status, order.transaction_id = "paid", "multi-project-review"
        order.paid_at = datetime.now(timezone.utc)
        application = await db.get(RensheApplication, draft.id)
        application.status = "pending_initial_review"
        await db.commit()
    reviewed = await RensheReviewService().review(
        reviewer_id=1,
        application_id=draft.id,
        stage="initial",
        data=await review_payload(env, draft.id),
    )
    assert reviewed.decision == "approved"

    exporter = RensheExportService(env.storage)
    job = await exporter.create_job(plan_id=batch.id, admin_id=1)
    assert job.candidate_total == 1
    await exporter.process_next_job()
    done = await exporter.get_job(job.id)
    assert done.status == "succeeded", done.last_error
    async with env.db() as db:
        volume = await db.scalar(
            select(RensheExportVolume).where(RensheExportVolume.job_id == job.id)
        )
        archive_path = env.storage._local_path(volume.storage_key)
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names))
        assert any(name.startswith(f"bp{chosen}/") for name in names)
        with archive.open("批次信息.xlsx") as handle:
            sheet = load_workbook(handle).active
            info = {row[0]: row[1] for row in sheet.iter_rows(min_row=2, values_only=True)}
        assert "报考项目" in info and "¥380.00" in info["报考项目"]
        assert "规则 v1" in info["报考项目"]
        with archive.open("报名信息.xlsx") as handle:
            sheet = load_workbook(handle).active
        assert sheet.cell(2, 14).value == first.config.occupation_name

    now = datetime.now(timezone.utc)
    async with env.db() as db:
        refund = RensheRefundRequest(
            application_id=draft.id,
            order_id=submitted.order_id,
            user_id=1,
            request_kind="normal",
            reason_code="other",
            amount_cents=38000,
            status="approved",
            previous_application_status="pending_external_review",
            requested_at=now,
            due_at=now + timedelta(days=1),
            last_error="银行待退款",
            retry_count=2,
        )
        db.add(refund)
        await db.flush()
        refund_id = refund.id
        await db.commit()
    admin_detail = await RensheReviewService().get_application(draft.id)
    assert admin_detail.current_refund_id == refund_id
    assert admin_detail.current_refund_status == "approved"
    assert admin_detail.current_refund_last_error == "银行待退款"
    assert admin_detail.current_refund_retry_count == 2

async def test_multi_project_close_and_project_change_are_audited(pg_env, monkeypatch):
    env = pg_env
    monkeypatch.setattr("app.services.renshe_audit.get_db_ctx", env.db)
    first, _second, batch, projects = await multi_project_batch(env, monkeypatch)
    project_id = projects[first.id]

    await PlanService().update_multi_project(
        batch.id, project_id, price_cents=42000, admin_id=1
    )
    await PlanService().update_multi_project(
        batch.id, project_id, is_active=False, admin_id=1
    )
    await PlanService().close_registration(batch.id, None, admin_id=1)

    async with env.db() as db:
        plan = await db.get(Plan, batch.id)
        project_logs = list((await db.scalars(
            select(RensheAuditLog).where(
                RensheAuditLog.action == "renshe.batch.project_change",
                RensheAuditLog.object_id == project_id,
            )
        )).all())
        lifecycle = list((await db.scalars(
            select(RensheAuditLog).where(
                RensheAuditLog.action == "batch.close_registration",
                RensheAuditLog.object_id == batch.id,
            )
        )).all())
    assert plan.status == "registration_closed"
    assert project_logs[-1].summary["is_active"] == {"old": True, "new": False}
    assert lifecycle, "多项目批次关闭报名必须写人社审计"
    audit_page = await RensheAuditQueryService().list_logs(
        admin_id=1,
        ip_address=None,
        plan_id=batch.id,
        application_id=None,
        actor_admin_id=None,
        action=None,
        result=None,
        started_at=None,
        ended_at=None,
        page=1,
        page_size=50,
    )
    actions = {item.action for item in audit_page.items}
    assert {"renshe.batch.price_change", "renshe.batch.project_change"} <= actions


async def test_published_multi_project_price_change_is_audited(pg_env, monkeypatch):
    env = pg_env
    first, _second, batch, projects = await multi_project_batch(env, monkeypatch)
    updated = await PlanService().update_multi_project(
        batch.id, projects[first.id], price_cents=99000, admin_id=1
    )
    project = next(item for item in updated.projects if item.id == projects[first.id])
    assert project.price_cents == 99000
    async with env.db() as db:
        log = await db.scalar(
            select(RensheAuditLog)
            .where(
                RensheAuditLog.action == "renshe.batch.price_change",
                RensheAuditLog.object_id == projects[first.id],
            )
            .order_by(RensheAuditLog.id.desc())
        )
    assert log is not None
    assert log.summary["old"] == 38000
    assert log.summary["new"] == 99000
    with pytest.raises(Exception, match="大于 0"):
        await PlanService().update_multi_project(
            batch.id, projects[first.id], price_cents=0, admin_id=1
        )


async def test_multi_project_public_projection_and_published_edit(pg_env, monkeypatch):
    env = pg_env
    first, second, batch, projects = await multi_project_batch(env, monkeypatch)
    plans = await PlanService().list_renshe_plans(public=True)
    plan = next(item for item in plans if item.id == batch.id)
    public = public_plan(plan)
    assert {item["id"] for item in public["projects"]} == {
        projects[first.id],
        projects[second.id],
    }
    assert public["can_apply"] is True

    updated = await PlanService().update_renshe_multi_batch(
        batch.id,
        PlanUpdate(
            exam_location="新考点",
            exam_org_name="新机构",
            registration_notice="新注意事项",
        ),
        admin_id=1,
    )
    assert updated.exam_location == "新考点"
    assert updated.registration_notice == "新注意事项"
    async with env.db() as db:
        logs = list((await db.scalars(
            select(RensheAuditLog).where(
                RensheAuditLog.object_id == batch.id,
                RensheAuditLog.action.in_(("batch.update", "renshe.batch.org_change")),
            )
        )).all())
    assert any(log.action == "renshe.batch.org_change" for log in logs)

    with pytest.raises(Exception, match="不支持修改该字段"):
        await PlanService().update_renshe_multi_batch(
            batch.id, PlanUpdate(price_cents=99000), admin_id=1
        )
    with pytest.raises(Exception, match="不能修改报考项目集合"):
        await PlanService().update_renshe_multi_batch(
            batch.id,
            PlanUpdate(projects=[PlanProjectInput(product_id=first.id, price_cents=1)]),
            admin_id=1,
        )


async def test_renshe_multi_routes_reject_h3c_multi_product_plans(pg_env, monkeypatch):
    env = pg_env
    async with env.db() as db:
        foreign = Plan(product_type=None, name="H3C 多产品草稿", price_cents=0)
        db.add(foreign)
        await db.flush()
        foreign_id = foreign.id
        await db.commit()

    with pytest.raises(NotFoundException):
        await PlanService().update_renshe_multi_batch(
            foreign_id, PlanUpdate(name="不应修改"), admin_id=1
        )
    with pytest.raises(NotFoundException):
        await PlanService().update_multi_project(
            foreign_id, 1, is_active=False, admin_id=1
        )
    with pytest.raises(NotFoundException):
        await PlanService().finalize_plan(
            foreign_id, None, admin_id=1, multi_only=True
        )
    with pytest.raises(NotFoundException):
        await PlanService().preview_impact(
            foreign_id, None, action="cancel", multi_only=True
        )


async def test_catalog_hides_stopped_multi_project_products(pg_env, monkeypatch):
    env = pg_env
    monkeypatch.setattr("app.services.certification_journey.get_db_ctx", env.db)
    first, second, batch, projects = await multi_project_batch(env, monkeypatch)
    async with env.db() as db:
        from app.models.cert_product import CertProduct

        product = await db.get(CertProduct, first.id)
        product.renshe_status = "disabled"
        product.is_active = False
        await db.commit()
    catalog = await CertificationJourneyService().catalog("renshe")
    subjects = {
        item["subject_id"]
        for item in catalog["batches"]
        if item["plan_id"] == batch.id
    }
    assert first.code not in subjects
    assert second.code in subjects
    plans = await PlanService().list_renshe_plans(public=True)
    row = next(item for item in plans if item.id == batch.id)
    public_project = next(
        item for item in public_plan(row)["projects"]
        if item["id"] == projects[first.id]
    )
    assert public_project["product_is_active"] is False
    assert public_project["can_apply"] is False
