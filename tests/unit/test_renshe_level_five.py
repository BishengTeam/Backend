"""Confirmed five-level employment admission, review and reporting path."""

import io
import zipfile
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from app.domain.renshe.src.index import RensheApplicationVersion, RensheExportVolume, RensheMaterial
from app.domain.plan.src.index import Plan
from app.domain.user.src.index import UserRealname
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


def form(plan_id=1, occupation_name="信息安全管理员"):
    start = china_today().isoformat()
    return RensheDraftUpsert(
        plan_id=plan_id, contact_phone="13800138000", email="five@example.test", mailing_address="实际地址",
        reporting_info={"occupation_status": "职工", "ethnicity": "汉族", "political_status": "群众", "has_work_experience": True,
                        "work_start_date": start, "occupation_years": 0, "work_unit": "样例单位", "social_insurance_unit": "样例单位"},
        qualification_info={"cultural_education": "junior_high", "work_experiences": [
            {"work_unit": "样例单位", "position": "实际岗位", "occupation_name": occupation_name, "start_date": start, "end_date": None}]},
    )


def test_five_catalog_splits_conditions_materials_and_readiness():
    choices = [i for i in RensheProjectService.capabilities()["combinations"] if i["grade"] == 5]
    assert len(choices) == 3
    assert {i["occupation_key"] for i in choices} == {"information_security", "network_security", "internet_review"}
    for item in choices:
        assert item["adapted"] and item["reporting_confirmed"]
        assert not item["missing"]
        assert len(item["source_conditions"]) == 1
        kinds = {m["kind"] for m in item["config"]["materials"]}
        assert {"id_card_front", "id_card_back", "registration_portrait", "commitment_letter"} <= kinds
        assert item["application_type"] == "工龄型"
        assert item["implementation_ready"] and item["config"]["qualification_rule"] == LEVEL_FIVE_RULE
        assert {"work_proof", "social_security_proof"} <= kinds
        assert "_工龄证明" in item["export_instructions"]["file_tree"]
        assert "尚未确认" not in item["export_instructions"]["directory_note"]
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
    validate_qualification(data, config(), verified_birth_date="1990-01-01")  # first workday, declared 0
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
    for key in ("age", "birth_date", "age_as_of", "eligibility_snapshot"):
        with pytest.raises(ValueError):
            RensheDraftUpsert.model_validate({**form().model_dump(mode="json"), key: "1990-01-01"})


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
async def test_five_only_accepts_employment_type_and_still_requires_template(isolated):
    service = RensheProjectService()
    with pytest.raises(BusinessException, match="未找到"):
        await service.save(RensheProjectWrite(chinese_name="无类型不可选", selection_id="information_security:5:无类型"), admin_id=1)
    draft = await service.save(RensheProjectWrite(chinese_name="五级工龄", selection_id="information_security:5:工龄型"), admin_id=1)
    assert draft.config.qualification_rule == LEVEL_FIVE_RULE
    assert draft.validation_errors and all("承诺书" in error for error in draft.validation_errors)
    with pytest.raises(BusinessException, match="承诺书"):
        await service.save(RensheProjectWrite(chinese_name=draft.chinese_name, selection_id=draft.config.selection_id,
                                            status="available", expected_version=draft.version), product_id=draft.id, admin_id=1)
    assert any("承诺书" in error for error in activation_errors(draft.config.model_dump()))


@pytest.mark.asyncio
@pytest.mark.parametrize("occupation", ["information_security", "network_security", "internet_review"])
async def test_five_path_material_review_and_export(isolated, monkeypatch, occupation):
    original, _ = await graduated_batch(isolated, monkeypatch)
    project = await RensheProjectService().save(RensheProjectWrite(chinese_name="五级联调", selection_id=f"{occupation}:5:工龄型", commitment_template=original.config.commitment_template, status="available"), admin_id=1)
    batch = await create_batch(project)
    await PlanService().publish_plan(batch.id, project.code, admin_id=1)
    service = RensheApplicationService(isolated.storage)
    async with isolated.db() as db:
        identity = await db.scalar(select(UserRealname))
        identity.birth_date = (anniversary(china_today(), -16) + timedelta(days=1)).isoformat()
        await db.commit()
    with pytest.raises(BusinessException, match="16周岁"):
        await service.save_draft(1, form(batch.id, batch.occupation_name))
    async with isolated.db() as db:
        identity = await db.scalar(select(UserRealname))
        identity.birth_date = anniversary(china_today(), -16).isoformat()
        await db.commit()
    draft = await service.save_draft(1, form(batch.id, batch.occupation_name))

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
        assert workbook["Sheet1"]["N2"].value == batch.occupation_name
        assert workbook["Sheet1"]["T2"].value == batch.occupation_code
        assert workbook["Sheet1"]["S2"].value == 0
        assert workbook["Sheet1"]["U2"].value == "年满16周岁，从事本职业或相关职业工作。"
        assert workbook["Sheet1"]["V2"].value == "工龄型"
        workbook.close()


@pytest.mark.asyncio
async def test_paid_five_correction_after_registration_closes_keeps_first_age_and_order(isolated, monkeypatch):
    original, _ = await graduated_batch(isolated, monkeypatch)
    project = await RensheProjectService().save(RensheProjectWrite(chinese_name="五级补正", selection_id="information_security:5:工龄型", commitment_template=original.config.commitment_template, status="available"), admin_id=1)
    batch = await create_batch(project)
    await PlanService().publish_plan(batch.id, project.code, admin_id=1)
    applicant = RensheApplicationService(isolated.storage)
    draft = await applicant.save_draft(1, form(batch.id))
    for item in batch.renshe_config["materials"]:
        if item["kind"].startswith("id_card_"):
            continue
        pdf = item["kind"] != "registration_portrait"
        await applicant.upload_application_material(1, batch.id, item["kind"], filename="材料.pdf" if pdf else "照片.jpg", content_type="application/pdf" if pdf else "image/jpeg", content=b"%PDF-1.4\nfixture" if pdf else b"\xff\xd8\xfffixture")
    submitted = await applicant.submit(1, draft.id)
    await pay(isolated, submitted)
    reviewer = RensheReviewService()
    await reviewer.review(reviewer_id=1, application_id=draft.id, stage="initial", data=RensheReviewCreate(
        decision="rejected", expected_version_id=submitted.version.id, reason="请补正工龄证明经办人", required_changes=["work_proof"], correction_due_at=datetime.now(UTC) + timedelta(days=3)))
    async with isolated.db() as db:
        stored = await db.get(Plan, batch.id)
        stored.status = "registration_closed"
        stored.apply_end = datetime.now(UTC) - timedelta(days=1)
        first = await db.get(RensheApplicationVersion, submitted.version.id)
        first_age = first.form_data["eligibility_snapshot"]["age_as_of"]
        await db.commit()
    await applicant.save_draft(1, form(batch.id))
    await applicant.upload_application_material(1, batch.id, "work_proof", filename="补正证明.pdf", content_type="application/pdf", content=b"%PDF-1.4\ncorrected")
    corrected = await applicant.submit(1, draft.id)
    assert corrected.order_id == submitted.order_id and not corrected.payment_required
    async with isolated.db() as db:
        current = await db.get(RensheApplicationVersion, corrected.version.id)
        assert current.form_data["eligibility_snapshot"]["age_as_of"] == first_age
        assert current.form_data["eligibility_snapshot"]["required_years"] == 0
        proofs = (await db.execute(select(RensheMaterial).where(RensheMaterial.application_id == draft.id, RensheMaterial.kind == "work_proof"))).scalars().all()
        assert len(proofs) == 2 and proofs[0].sha256 != proofs[1].sha256
    await reviewer.review(reviewer_id=1, application_id=draft.id, stage="initial", data=RensheReviewCreate(decision="approved", expected_version_id=corrected.version.id, checklist={key: True for key in review_checks(batch.renshe_config)}))
    with pytest.raises(ConflictException, match="导出"):
        await reviewer.review(reviewer_id=1, application_id=draft.id, stage="external", data=RensheReviewCreate(decision="approved", expected_version_id=corrected.version.id))
    exporter = RensheExportService(isolated.storage)
    job = await exporter.create_job(plan_id=batch.id, admin_id=1)
    await exporter.process_next_job()
    assert (await exporter.get_job(job.id)).status == "succeeded"
    await reviewer.review(reviewer_id=1, application_id=draft.id, stage="external", data=RensheReviewCreate(decision="approved", expected_version_id=corrected.version.id))
