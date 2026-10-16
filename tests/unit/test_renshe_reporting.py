"""The real reporting workbook and immutable reporting revision boundaries."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from openpyxl import load_workbook
from sqlalchemy import func, select

from app.domain.order.src.index import Order
from app.domain.plan.src.index import Plan
from app.domain.renshe.src.index import RensheApplication, RensheApplicationVersion
from app.domain.user.src.index import UserRealname
from app.models.renshe_project import RensheProjectConfig
from app.port.exceptions import BusinessException, ConflictException
from app.schemas.renshe import RensheReviewCreate
from app.schemas.renshe_project import RensheProjectWrite
from app.services.renshe_application import RensheApplicationService
from app.services.renshe_export import ExportCandidate, build_registration_row
from app.services.renshe_graduated import (
    REVIEW_CHECKS,
    policy_errors,
    validate_education,
)
from app.services.renshe_project import RensheProjectService
from app.services.renshe_reporting import (
    DEGREE_CONDITION,
    EDUCATIONS,
    ETHNICITIES,
    HEADERS,
    assert_template,
    default_policy,
    public_options,
    report_date,
    resolve_contract,
    validate_reporting,
    validate_snapshot,
)
from app.services.renshe_review import RensheReviewService
from tests.unit.test_renshe_graduated import draft_payload, graduated_batch, uploads
from tests.unit.test_renshe_projects import isolated as _isolated

isolated = _isolated


def policy(**rules):
    result = default_policy()
    result["employment_reporting"] = {
        "confirmed": True,
        "date_rule": "work_or_enrollment",
        "years_rule": "required",
        "allow_zero_years": True,
        **rules,
    }
    return result


def info(**changes):
    return {
        "occupation_status": "失业人员",
        "ethnicity": "汉族",
        "political_status": "群众",
        "has_work_experience": False,
        "enrollment_date": "2019-09-01",
        "occupation_years": 0,
        **changes,
    }


def test_actual_client_workbook_structure_dictionaries_and_condition():
    original = (
        Path(__file__).resolve().parents[3]
        / "DOCS/docs/甲方/认证材料/人设报名模版/报名信息.xlsx"
    )
    source = Path(__file__).resolve().parents[2] / "docs/renshe/报名信息.xlsx"
    if original.exists():
        assert original.read_bytes() == source.read_bytes()
    workbook = load_workbook(source)
    try:
        assert_template(workbook)
        assert workbook["Sheet1"].max_column == 25
        assert workbook["模板示例"]["K1"].value.startswith("(必填)")
        assert workbook["模板示例"]["L1"].value.startswith("(必填)")
        assert set(ETHNICITIES) == {
            row[0]
            for row in workbook["模板说明"].iter_rows(
                min_row=267, max_row=323, max_col=1, values_only=True
            )
        }
        workbook["Sheet1"]["M1"] = "毕业状态"
        with pytest.raises(ValueError, match="标题"):
            assert_template(workbook)
    finally:
        workbook.close()


@pytest.mark.parametrize(
    "changes",
    [
        {"occupation_status": "已毕业"},
        {"ethnicity": ""},
        {"political_status": ""},
        {"has_work_experience": "false"},
        {"has_work_experience": True},
        {"occupation_years": None},
        {"occupation_years": -1},
        {"occupation_years": 2},
        {"occupation_years": "0.001"},
        {"enrollment_date": "2999-01-01"},
        {"enrollment_date": None},
        {"work_start_date": "2020-01-01"},
    ],
)
def test_reporting_rejects_missing_or_fabricated_fields(changes):
    with pytest.raises(BusinessException):
        validate_reporting(info(**changes), policy())


def test_zero_optional_columns_and_exact_date_source_are_explicit():
    result = validate_reporting(info(), policy())
    assert float(result["occupation_years"]) == 0
    assert report_date(result, policy()["employment_reporting"]) == "2019-09-01"
    with pytest.raises(BusinessException, match="零年限"):
        validate_reporting(info(), policy(allow_zero_years=False))
    assert (
        validate_reporting(
            info(enrollment_date=None, occupation_years=None),
            policy(date_rule="optional", years_rule="optional"),
        )["occupation_years"]
        is None
    )
    worker = info(
        has_work_experience=True, work_start_date="2020-02-01", occupation_years="3.25"
    )
    assert (
        report_date(
            validate_reporting(worker, policy()), policy()["employment_reporting"]
        )
        == "2020-02-01"
    )
    enrollment = policy(date_rule="enrollment_only")
    assert (
        report_date(
            validate_reporting(worker, enrollment), enrollment["employment_reporting"]
        )
        == "2019-09-01"
    )
    with pytest.raises(BusinessException, match="毕业日期不能替代"):
        validate_reporting(info(), policy(date_rule="work_only"))
    with pytest.raises(ConflictException, match="尚未确认"):
        validate_reporting(info(), policy(confirmed=False))


@pytest.mark.asyncio
async def test_rows_preserve_all_25_positions_and_frozen_reporting_policy(
    isolated, monkeypatch
):
    project, batch = await graduated_batch(isolated, monkeypatch)
    service = RensheApplicationService(isolated.storage)
    async with isolated.db() as db:
        realname = await db.scalar(select(UserRealname))
        realname.ethnicity, realname.political_status = None, None
        await db.commit()
    reporting = info(
        occupation_status="职工",
        has_work_experience=True,
        work_start_date="2020-02-01",
        occupation_years="3.25",
        work_unit="真实工作单位",
        social_insurance_unit="真实参保单位",
    )
    saved = await service.save_draft(1, draft_payload(batch, reporting_info=reporting))
    async with isolated.db() as db:
        plan = await db.get(Plan, batch.id)
        realname = await db.scalar(select(UserRealname))
        candidate = ExportCandidate(
            saved.id,
            1,
            0,
            service._realname_snapshot(realname),
            {},
            saved.draft_data,
            (),
        )
        values = build_registration_row(candidate, plan)
        assert len(values) == len(HEADERS) == 25
        assert values[11] == "真实工作单位" and values[23] == "真实参保单位"
        assert (
            values[12] == "职工" and values[17] == "2020-02-01" and values[18] == 3.25
        )
        assert values[20] == DEGREE_CONDITION and values[15:17] == [None, None]
        assert values[7:9] == ["汉族", "群众"]
        missing = deepcopy(saved.draft_data)
        missing["reporting_info"].pop("ethnicity")
        with pytest.raises(ConflictException, match="民族"):
            build_registration_row(
                ExportCandidate(
                    saved.id, 1, 0, candidate.realname_snapshot, {}, missing, ()
                ),
                plan,
            )
        config = deepcopy(plan.renshe_config)
        original = await resolve_contract(db, plan)
        for code, label in EDUCATIONS.items():
            education = {
                **saved.draft_data["education_info"],
                "education": code,
                "institution_type": "vocational",
            }
            validate_education(education, config)
            row = build_registration_row(
                ExportCandidate(
                    saved.id,
                    1,
                    0,
                    candidate.realname_snapshot,
                    {},
                    {**saved.draft_data, "education_info": education},
                    (),
                ),
                plan,
            )
            assert row[6] == label
        validate_education(
            {**saved.draft_data["education_info"], "education": "higher_vocational"},
            config,
        )
        with pytest.raises(BusinessException, match="专科及以上"):
            validate_education(
                {**saved.draft_data["education_info"], "education": "technical_school"},
                config,
            )
        corrupted = deepcopy(saved.draft_data)
        corrupted["reporting_contract"]["policy"]["employment_reporting"][
            "allow_zero_years"
        ] = False
        with pytest.raises(ConflictException, match="不一致"):
            await validate_snapshot(db, plan, corrupted)
    config["reporting_policy"]["employment_reporting"]["date_rule"] = "enrollment_only"
    updated = await RensheProjectService().save(
        RensheProjectWrite(
            chinese_name=project.chinese_name,
            expected_version=project.version,
            status="available",
            config=config,
        ),
        product_id=project.id,
        admin_id=1,
    )
    async with isolated.db() as db:
        plan = await db.get(Plan, batch.id)
        assert (await resolve_contract(db, plan))["config_id"] == original["config_id"]
        with pytest.raises(ConflictException, match="不能更换"):
            await resolve_contract(db, plan, updated.config_id)
    with pytest.raises(ConflictException, match="配置已变化"):
        await service.save_draft(
            1, draft_payload(batch, reporting_config_id=updated.config_id)
        )


@pytest.mark.asyncio
async def test_unconfirmed_reporting_cannot_activate_and_full_dictionary_text_is_required(
    isolated, monkeypatch
):
    project, _ = await graduated_batch(isolated, monkeypatch)
    config = project.config.model_dump()
    assert not policy_errors(config["reporting_policy"])
    config["reporting_policy"]["export_values"]["application_condition"] = (
        "已取得本专业毕业证书"
    )
    assert policy_errors(config["reporting_policy"])
    config = project.config.model_dump()
    config["reporting_policy"]["employment_reporting"]["confirmed"] = False
    with pytest.raises(BusinessException, match="R、S"):
        await RensheProjectService().save(
            RensheProjectWrite(
                chinese_name=project.chinese_name,
                expected_version=project.version,
                status="available",
                config=config,
            ),
            product_id=project.id,
            admin_id=1,
        )


@pytest.mark.asyncio
async def test_old_paid_snapshot_adopts_new_reporting_without_rewriting_order_or_qualification(
    isolated, monkeypatch
):
    project, batch = await graduated_batch(isolated, monkeypatch)
    legacy = project.config.model_dump()
    legacy["education_levels"] = ["bachelor"]
    legacy["reporting_policy"].pop("contract_version")
    legacy["reporting_policy"].pop("employment_reporting")
    legacy["reporting_policy"]["export_values"] = {
        "candidate_source": "社会人员",
        "student_status": "已毕业",
        "application_condition": "已取得本专业毕业证书",
    }
    old_form = draft_payload(batch).model_dump(
        mode="json", exclude={"reporting_info", "reporting_config_id"}
    )
    service = RensheApplicationService(isolated.storage)
    async with isolated.db() as db:
        legacy_row = RensheProjectConfig(
            product_id=project.id, version=2, config=legacy
        )
        db.add(legacy_row)
        await db.flush()
        plan = await db.get(Plan, batch.id)
        plan.renshe_config_id, plan.renshe_config = legacy_row.id, legacy
        plan.status, plan.apply_end = (
            "registration_closed",
            datetime.now(timezone.utc) - timedelta(days=1),
        )
        application = RensheApplication(
            user_id=1,
            plan_id=batch.id,
            status="pending_initial_review",
            draft_data=old_form,
        )
        db.add(application)
        await db.flush()
        realname = await db.scalar(select(UserRealname))
        old_version = RensheApplicationVersion(
            application_id=application.id,
            project_config_id=legacy_row.id,
            version_no=1,
            submitted_by_user_id=1,
            realname_snapshot=service._realname_snapshot(realname),
            student_snapshot={},
            form_data=old_form,
            submitted_at=datetime.now(timezone.utc),
        )
        db.add(old_version)
        await db.flush()
        application.current_version_id = old_version.id
        order = Order(
            user_id=1,
            plan_id=plan.id,
            application_id=application.id,
            product_type=plan.product_type,
            price=12345,
            status="paid",
            paid_at=datetime.now(timezone.utc),
        )
        db.add(order)
        await db.commit()
        legacy_config_id, old_version_id, application_id, order_id = (
            legacy_row.id,
            old_version.id,
            application.id,
            order.id,
        )
    updated = await RensheProjectService().save(
        RensheProjectWrite(
            chinese_name=project.chinese_name,
            expected_version=2,
            status="available",
            config=project.config.model_dump(),
        ),
        product_id=project.id,
        admin_id=1,
    )
    async with isolated.db() as db:
        plan = await db.get(Plan, batch.id)
        options = public_options(await resolve_contract(db, plan), plan.renshe_config)
        assert options["education_levels"] == ["bachelor"]
        assert options["config_id"] == updated.config_id
    review = RensheReviewService()
    with pytest.raises(ConflictException, match="缺少模板填报信息"):
        await review.review(
            reviewer_id=1,
            application_id=application_id,
            stage="initial",
            data=RensheReviewCreate(
                decision="approved",
                expected_version_id=old_version_id,
                checklist={key: True for key in REVIEW_CHECKS},
            ),
        )
    await review.review(
        reviewer_id=1,
        application_id=application_id,
        stage="initial",
        data=RensheReviewCreate(
            decision="rejected",
            expected_version_id=old_version_id,
            required_changes=["reporting_info.occupation_status"],
            reason="补齐模板填报信息",
            correction_due_at=datetime.now(timezone.utc) + timedelta(days=2),
        ),
    )
    await service.save_draft(
        1, draft_payload(batch, reporting_config_id=updated.config_id)
    )
    await uploads(service, batch)
    result = await service.submit(1, application_id)
    assert result.order_id == order_id and result.payment_required is False
    assert result.version.version_no == 2
    async with isolated.db() as db:
        assert (await db.get(Order, order_id)).price == 12345
        assert await db.scalar(select(func.count()).select_from(Order)) == 1
        old = await db.get(RensheApplicationVersion, old_version_id)
        assert old.form_data == old_form and old.project_config_id == legacy_config_id
        new = await db.get(RensheApplicationVersion, result.version.id)
        assert new.project_config_id == legacy_config_id
        assert new.form_data["reporting_contract"]["config_id"] == updated.config_id
        assert (await db.get(Plan, batch.id)).renshe_config == legacy
    await review.review(
        reviewer_id=1,
        application_id=application_id,
        stage="initial",
        data=RensheReviewCreate(
            decision="approved",
            expected_version_id=result.version.id,
            checklist={key: True for key in REVIEW_CHECKS},
        ),
    )
