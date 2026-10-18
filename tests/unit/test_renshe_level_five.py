"""Five-level draft gates and independent, locally simulated admission path."""

import io
import zipfile
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from openpyxl import load_workbook
from sqlalchemy import select, update

from app.domain.renshe.src.index import RensheApplicationVersion, RensheExportVolume
from app.domain.user.src.index import UserRealname
from app.models.renshe_project import RensheProjectConfig
from app.port.exceptions import BusinessException, ConflictException
from app.schemas.renshe import RensheDraftUpsert, RensheReviewCreate
from app.schemas.renshe_project import RensheProjectWrite
from app.services import renshe_catalog as catalog
from app.services.plan import PlanService
from app.services.renshe_application import RensheApplicationService
from app.services.renshe_employment import (
    LEVEL_FIVE_RULE,
    anniversary,
    review_checks,
    validate_qualification,
)
from app.services.renshe_export import RensheExportService
from app.services.renshe_graduated import activation_errors, china_today
from app.services.renshe_project import (
    RensheProjectService,
    batch_configuration,
    selection_configuration,
)
from app.services.renshe_review import RensheReviewService
from tests.unit.test_renshe_employment import pay
from tests.unit.test_renshe_graduated import graduated_batch
from tests.unit.test_renshe_projects import create_batch
from tests.unit.test_renshe_projects import isolated as _isolated

isolated = _isolated


def config():
    return selection_configuration(catalog.find_selection("information_security:5:工龄型"))


def form(plan_id=1):
    start = anniversary(china_today(), -1).isoformat()
    return RensheDraftUpsert(
        plan_id=plan_id, contact_phone="13800138000", email="five@example.test", mailing_address="实际地址",
        reporting_info={"occupation_status": "职工", "ethnicity": "汉族", "political_status": "群众", "has_work_experience": True,
                        "work_start_date": start, "occupation_years": 0, "work_unit": "样例单位", "social_insurance_unit": "样例单位"},
        qualification_info={"cultural_education": "junior_high", "work_experiences": [
            {"work_unit": "样例单位", "position": "实际岗位", "occupation_name": "信息安全管理员", "start_date": start, "end_date": None}]},
    )


def test_five_catalog_splits_conditions_materials_and_readiness():
    choices = [i for i in RensheProjectService.capabilities()["combinations"] if i["grade"] == 5]
    assert len(choices) == 3
    assert {i["occupation_key"] for i in choices} == {"information_security", "network_security", "internet_review"}
    for item in choices:
        assert not item["adapted"] and not item["reporting_confirmed"]
        assert len(item["source_conditions"]) == 1
        kinds = {m["kind"] for m in item["config"]["materials"]}
        assert {"id_card_front", "id_card_back", "registration_portrait", "commitment_letter"} <= kinds
        assert item["application_type"] == "工龄型"
        assert item["implementation_ready"] and item["config"]["qualification_rule"] == LEVEL_FIVE_RULE
        assert {"work_proof", "social_security_proof"} <= kinds
        assert "_工龄证明" in item["export_instructions"]["file_tree"]
        assert "尚未确认" in item["export_instructions"]["directory_note"]
        assert "age" in item["admission_requirements"]["review_checks"]
        assert not {"student_card", "graduation_certificate", "education_report", "vocational_qualification_certificate"} & kinds


@pytest.mark.parametrize("offset,allowed", [(0, True), (1, False), (-1, True)])
def test_age_uses_verified_birth_on_first_submission_day(offset, allowed):
    from datetime import timedelta
    today = china_today()
    birth = anniversary(today, -16) + timedelta(days=offset)
    if allowed:
        result = validate_qualification(form().model_dump(mode="json"), config(), verified_birth_date=birth.isoformat())
        assert result["eligibility_snapshot"]["required_years"] == 0
        assert result["eligibility_snapshot"]["age_as_of"] == today.isoformat()
    else:
        with pytest.raises(BusinessException, match="16周岁"):
            validate_qualification(form().model_dump(mode="json"), config(), verified_birth_date=birth.isoformat())


def test_no_four_level_threshold_or_certificate_and_no_client_age_override():
    data = form().model_dump(mode="json")
    validate_qualification(data, config(), verified_birth_date="1990-01-01")  # one year, declared 0
    with pytest.raises(BusinessException, match="实名出生日期"):
        validate_qualification(data, config())
    with pytest.raises(BusinessException, match="独立资格规则"):
        validate_qualification(data, {**config(), "export_skill_level": "四级"}, verified_birth_date="1990-01-01")
    for key in ("work_unit", "social_insurance_unit"):
        with pytest.raises(BusinessException, match="工作单位"):
            validate_qualification({**data, "reporting_info": {**data["reporting_info"], key: ""}}, config(), verified_birth_date="1990-01-01")
    data["qualification_info"]["skill_certificate"] = {"occupation_name": "信息安全管理员", "grade": 5, "certificate_number": "TEST", "issued_on": "2020-01-01"}
    with pytest.raises(BusinessException, match="不使用技能证书"):
        validate_qualification(data, config(), verified_birth_date="1990-01-01")


@pytest.mark.asyncio
@pytest.mark.parametrize("eligible", [True, False])
async def test_correction_preserves_first_submission_china_date(eligible):
    today = china_today()
    first_day = today - timedelta(days=2)
    # 16:00 UTC is the start of the following China calendar day.
    first_utc = datetime.combine(first_day - timedelta(days=1), datetime.min.time(), tzinfo=UTC).replace(hour=16)
    db = SimpleNamespace(scalar=AsyncMock(return_value=first_utc))
    identity = SimpleNamespace(birth_date=anniversary(first_day, -16).isoformat() if eligible else anniversary(first_day + timedelta(days=1), -16).isoformat())
    if eligible:
        result = await RensheApplicationService._admission_values(db, SimpleNamespace(id=1), identity, config(), form().model_dump(mode="json"))
        assert result["eligibility_snapshot"]["age_as_of"] == first_day.isoformat()
    else:
        with pytest.raises(BusinessException, match="首次正式提交"):
            await RensheApplicationService._admission_values(db, SimpleNamespace(id=1), identity, config(), form().model_dump(mode="json"))


@pytest.mark.asyncio
async def test_pending_five_saves_draft_but_cannot_enable_bind_or_reinterpret_history(isolated):
    service = RensheProjectService()
    with pytest.raises(BusinessException, match="未找到"):
        await service.save(RensheProjectWrite(chinese_name="不能新建无类型", selection_id="information_security:5:无类型"), admin_id=1)
    original = await service.save(RensheProjectWrite(chinese_name="历史五级草稿", selection_id="information_security:5:工龄型"), admin_id=1)
    frozen = original.config.model_dump()
    # Seed a pre-removal historical contract; never delete or reclassify it.
    frozen.update(selection_id="information_security:5:无类型", application_types=["无类型"], qualification_rule="unadapted")
    async with isolated.db() as db:
        await db.execute(update(RensheProjectConfig).where(RensheProjectConfig.id == original.config_id).values(config=deepcopy(frozen)))
        await db.commit()
    changed = await service.save(RensheProjectWrite(chinese_name="仅改介绍", expected_version=original.version), product_id=original.id, admin_id=1)
    assert changed.config.model_dump() == frozen
    draft = await service.save(RensheProjectWrite(chinese_name="五级工龄草稿", selection_id="information_security:5:工龄型"), admin_id=1)
    assert draft.config.qualification_rule == LEVEL_FIVE_RULE
    assert any("外部系统" in error for error in draft.validation_errors)
    with pytest.raises(BusinessException, match="尚未确认"):
        await service.save(RensheProjectWrite(chinese_name=draft.chinese_name, status="available", expected_version=draft.version), product_id=draft.id, admin_id=1)
    with pytest.raises(BusinessException):
        await create_batch(draft)
    from app.domain.plan.src.index import Plan
    with pytest.raises(ConflictException, match="尚未确认"):
        batch_configuration(Plan(renshe_config=draft.config.model_dump()))
    assert activation_errors(draft.config.model_dump())


@pytest.mark.asyncio
@pytest.mark.parametrize("occupation", ["information_security", "network_security", "internet_review"])
async def test_five_path_material_review_and_export_in_confirmation_simulation(isolated, monkeypatch, occupation):
    original, _ = await graduated_batch(isolated, monkeypatch)
    # Only this isolated test simulates future business confirmation. Production
    # catalog remains closed; no runtime setting or operator override exists.
    selections = deepcopy(catalog.combinations())
    for item in selections:
        if item["grade"] == 5 and item["application_type"] == "工龄型":
            item.update(adapted=True, reporting_confirmed=True)
    monkeypatch.setattr(catalog, "combinations", lambda: deepcopy(selections))
    project = await RensheProjectService().save(RensheProjectWrite(chinese_name="五级联调", selection_id=f"{occupation}:5:工龄型", commitment_template=original.config.commitment_template, status="available"), admin_id=1)
    batch = await create_batch(project)
    await PlanService().publish_plan(batch.id, project.code, admin_id=1)
    service = RensheApplicationService(isolated.storage)
    async with isolated.db() as db:
        identity = await db.scalar(select(UserRealname))
        identity.birth_date = (anniversary(china_today(), -16) + timedelta(days=1)).isoformat()
        await db.commit()
    with pytest.raises(BusinessException, match="16周岁"):
        await service.save_draft(1, form(batch.id))
    async with isolated.db() as db:
        identity = await db.scalar(select(UserRealname))
        identity.birth_date = anniversary(china_today(), -16).isoformat()
        await db.commit()
    draft = await service.save_draft(1, form(batch.id))

    async def upload(kind):
        pdf = kind != "registration_portrait"
        await service.upload_application_material(1, batch.id, kind, filename="证明.pdf" if pdf else "照片.jpg", content_type="application/pdf" if pdf else "image/jpeg", content=b"%PDF-1.4\nfixture" if pdf else b"\xff\xd8\xfffixture")

    await upload("registration_portrait")
    await upload("commitment_letter")
    with pytest.raises(BusinessException, match="工龄证明"):
        await service.submit(1, draft.id)
    await upload("work_proof")
    with pytest.raises(BusinessException, match="社保"):
        await service.submit(1, draft.id)
    await upload("social_security_proof")
    submitted = await service.submit(1, draft.id)
    async with isolated.db() as db:
        version = await db.get(RensheApplicationVersion, submitted.version.id)
        assert version.student_snapshot == {}
        assert version.form_data["eligibility_snapshot"]["required_years"] == 0
    await pay(isolated, submitted)
    reviewer = RensheReviewService()
    with pytest.raises(BusinessException, match="逐项"):
        await reviewer.review(reviewer_id=1, application_id=draft.id, stage="initial", data=RensheReviewCreate(decision="approved", expected_version_id=submitted.version.id))
    await reviewer.review(reviewer_id=1, application_id=draft.id, stage="initial", data=RensheReviewCreate(decision="approved", expected_version_id=submitted.version.id, checklist={key: True for key in review_checks(batch.renshe_config)}))
    exporter = RensheExportService(isolated.storage)
    job = await exporter.create_job(plan_id=batch.id, admin_id=1)
    await exporter.process_next_job()
    finished = await exporter.get_job(job.id)
    assert finished.status == "succeeded", finished.last_error
    async with isolated.db() as db:
        volume = await db.scalar(select(RensheExportVolume).where(RensheExportVolume.job_id == job.id))
        archive_path = isolated.storage._local_path(volume.storage_key)
    with zipfile.ZipFile(archive_path) as archive:
        files = archive.namelist()
        assert any(name.endswith("_工龄证明.pdf") for name in files)
        assert any(name.endswith("_社保证明.pdf") for name in files)
        assert not any("学历" in name or "职业资格证书" in name for name in files)
        workbook = load_workbook(io.BytesIO(archive.read("报名信息.xlsx")))
        assert workbook["Sheet1"]["O2"].value == "五级"
        assert workbook["Sheet1"]["S2"].value == 0
        assert workbook["Sheet1"]["U2"].value == "年满16周岁，从事本职业或相关职业工作。"
        assert workbook["Sheet1"]["V2"].value == "工龄型"
        workbook.close()
