"""Document-backed choices, readiness gates and immutable historical policies."""

import pytest
from sqlalchemy import select, update

from app.domain.plan.src.index import Plan
from app.models.renshe_project import RensheProjectConfig
from app.port.exceptions import BusinessException, ConflictException
from app.schemas.renshe_project import RensheProjectWrite
from app.services.renshe_catalog import combinations, CURRENT_SELECTION
from app.services.renshe_project import RensheProjectService, batch_configuration, name_review_notices
from app.services.renshe_graduated import MATERIALS, review_checks
from tests.unit.test_renshe_projects import isolated as _isolated, create_batch
from tests.unit.test_renshe_graduated import graduated_batch

isolated = _isolated


def test_catalog_uses_documented_grades_and_type_paths_only():
    items = combinations()
    assert sum(item["adapted"] for item in items) == 21
    assert any(item["selection_id"] == CURRENT_SELECTION and item["adapted"] for item in items)
    assert {item["application_type"] for item in items if item["grade"] in {1, 2}} == {"复合型"}
    assert {item["application_type"] for item in items if item["grade"] == 5} == {"无类型"}
    assert not any(item["occupation_key"] == "ai_trainer" and item["grade"] == 5 for item in items)
    assert {item["grade"] for item in items if item["occupation_key"] == "business_data"} == {3, 4}
    assert all(item["sources"] and item["source_conditions"] for item in items)


@pytest.mark.asyncio
async def test_pending_choice_saves_empty_technical_rules_and_cannot_enable_or_bind(isolated):
    service = RensheProjectService()
    chosen = "information_security:3:复合型"
    draft = await service.save(RensheProjectWrite(chinese_name="工龄及证书路径", selection_id=chosen), admin_id=1)
    assert draft.config.application_types == ["复合型"]
    assert draft.config.qualification_rule == "unadapted"
    assert draft.config.materials == [] and draft.config.form_fields == []
    assert draft.validation_errors and draft.admission_requirements["rule_profile"]["missing"]
    with pytest.raises(BusinessException, match="尚未适配"):
        await service.save(RensheProjectWrite(chinese_name=draft.chinese_name, expected_version=draft.version,
                                             selection_id=chosen, status="available"), product_id=draft.id, admin_id=1)
    with pytest.raises(BusinessException, match="不能"):
        await create_batch(draft)
    async with isolated.db() as db:
        version = await db.scalar(select(RensheProjectConfig).where(RensheProjectConfig.product_id == draft.id))
        with pytest.raises(ConflictException, match="尚未适配"):
            batch_configuration(Plan(renshe_config=version.config))


@pytest.mark.asyncio
async def test_adapted_six_materials_and_batch_type_cannot_be_reselected(isolated, monkeypatch):
    project, batch = await graduated_batch(isolated, monkeypatch)
    kinds = {item.kind for item in project.config.materials}
    assert kinds == {"id_card_front", "id_card_back", "registration_portrait", "graduation_certificate", "education_report", "commitment_letter"}
    assert "report" in review_checks(batch.renshe_config) and "employment" not in review_checks(batch.renshe_config)
    assert not any("社保证明" in path for path in project.export_instructions["example_paths"])
    frozen = batch.renshe_config.copy()
    with pytest.raises(BusinessException, match="不能更换"):
        await RensheProjectService().save(RensheProjectWrite(
            chinese_name=project.chinese_name, expected_version=project.version,
            selection_id="information_security:4:工龄型", status="draft",
        ), product_id=project.id, admin_id=1)
    assert batch.renshe_config == frozen


@pytest.mark.asyncio
async def test_historical_nine_material_policy_is_not_replaced_by_same_choice(isolated, monkeypatch):
    project, batch = await graduated_batch(isolated, monkeypatch)
    historical = project.config.model_dump()
    historical.pop("selection_id", None)
    historical.pop("rule_profile", None)
    from app.domain.renshe.src.index import MATERIAL_SPECS
    historical["materials"] = [{"kind": kind, "name": name, "required": True, "extensions": sorted(MATERIAL_SPECS[kind].extensions), "max_count": 1} for kind, name in MATERIALS.items()]
    async with isolated.db() as db:
        # Seed a pre-rollout immutable row; the production service never rewrites it.
        await db.execute(update(RensheProjectConfig).where(RensheProjectConfig.id == project.config_id).values(config=historical))
        plan = await db.get(Plan, batch.id)
        plan.renshe_config = historical
        await db.commit()
    result = await RensheProjectService().save(RensheProjectWrite(
        chinese_name="仅修改展示信息", expected_version=project.version, status="available", selection_id=CURRENT_SELECTION,
    ), product_id=project.id, admin_id=1)
    assert {item.kind for item in result.config.materials} == set(MATERIALS)
    assert any("社保证明" in path for path in result.export_instructions["example_paths"])
    assert "employment" in review_checks(historical)
    async with isolated.db() as db:
        assert (await db.get(Plan, batch.id)).renshe_config == historical


def test_names_are_flagged_without_guessing_or_changing_identity():
    config = {"occupation_name": "信息安全管理员"}
    assert name_review_notices("人工智能训练师招生", config)
    assert name_review_notices("秋季报考项目", config) == []
    assert name_review_notices("信息安全管理员高级工", {**config, "skill_level": "中级工", "export_skill_level": "四级"})


def test_public_save_api_rejects_operator_config_and_old_two_field_selection():
    from app.api.admin.renshe import assert_project_selection_payload
    for body in [RensheProjectWrite(chinese_name="产品", config={}),
                 RensheProjectWrite(chinese_name="产品", occupation_code="code", skill_level="中级工")]:
        with pytest.raises(BusinessException, match="不能提交底层配置"):
            assert_project_selection_payload(body, creating=True)
    assert_project_selection_payload(RensheProjectWrite(chinese_name="产品", selection_id=CURRENT_SELECTION), creating=True)
    assert_project_selection_payload(RensheProjectWrite(chinese_name="修改名称"), creating=False)
