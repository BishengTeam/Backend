"""Explicit program selection and preservation of persisted admission contracts."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.port.exceptions import BusinessException
from app.schemas.renshe_project import RensheProjectWrite
from app.services.renshe_project import RensheProjectService, supported_programs, graduated_configuration
from app.services.renshe_graduated import save_template
from tests.unit.test_renshe_projects import isolated as _isolated, project_data, create_batch

isolated = _isolated


@pytest.mark.asyncio
async def test_selected_combination_always_uses_current_rules_and_keeps_template(isolated):
    service = RensheProjectService()
    config = graduated_configuration()
    selection_id = config.pop("selection_id")
    config.pop("rule_profile")
    config["commitment_template"] = save_template("承诺书.pdf", b"%PDF-1.4\nfixture")
    draft = await service.save(RensheProjectWrite(chinese_name="报考产品", config=config), admin_id=1)
    result = await service.save(RensheProjectWrite(
        chinese_name=draft.chinese_name, selection_id=selection_id,
        expected_version=draft.version, status="available",
    ), product_id=draft.id, admin_id=1)
    assert result.status == "available"
    assert result.config.selection_id == selection_id
    assert result.config.commitment_template == draft.config.commitment_template
    assert result.config.materials == RensheProjectWrite(chinese_name="对照", config=graduated_configuration()).config.materials


@pytest.mark.asyncio
async def test_new_project_requires_explicit_selection_and_ignores_name_as_program(isolated):
    service = RensheProjectService()
    with pytest.raises(BusinessException, match="请选择"):
        await service.save(RensheProjectWrite(chinese_name="只填项目名称"), admin_id=1)
    program = supported_programs()[0]
    result = await service.save(RensheProjectWrite(
        chinese_name="自定义招生项目名称", occupation_code=program["occupation_code"],
        skill_level=program["skill_level"],
    ), admin_id=1)
    assert result.config.occupation_name == program["occupation_name"]
    assert result.config.export_skill_level == program["export_skill_level"]
    assert result.config.qualification_rule == program["config"]["qualification_rule"]
    assert result.admission_requirements["scope"]["occupation_code"] == result.config.occupation_code


@pytest.mark.parametrize("code,level", [("unadapted", "中级工"), (None, "高级工")])
@pytest.mark.asyncio
async def test_unadapted_selection_cannot_even_be_silently_saved_as_default(isolated, code, level):
    with pytest.raises(BusinessException, match="尚未适配"):
        await RensheProjectService().save(RensheProjectWrite(
            chinese_name="信息安全管理员", occupation_code=code or supported_programs()[0]["occupation_code"],
            skill_level=level, status="available",
        ), admin_id=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("include_selection", [False, True])
async def test_editing_metadata_preserves_legacy_admission_and_version(isolated, include_selection):
    service = RensheProjectService()
    project = await service.save(project_data(education_levels=["bachelor"]), admin_id=1)
    await create_batch(project)
    original = deepcopy(project.config.model_dump())
    selection = ({"occupation_code": project.config.occupation_code, "skill_level": project.config.skill_level}
                 if include_selection else {})
    updated = await service.save(RensheProjectWrite(
        chinese_name="改名不换人社组合", description="更新介绍", home_cover_url="https://example.test/cover.png",
        expected_version=project.version, status="available", **selection,
    ), product_id=project.id, admin_id=1)
    assert updated.config.model_dump() == original
    assert updated.version == project.version
    assert updated.admission_requirements["scope"]["qualification_rule"] == original["qualification_rule"]
    assert [item["value"] for item in updated.admission_requirements["education_options"]] == ["bachelor"]
    assert updated.admission_requirements["materials"][-1]["kind"] == original["materials"][-1]["kind"]


@pytest.mark.asyncio
async def test_readonly_scope_uses_actual_draft_config_even_if_not_adapted(isolated):
    project = await RensheProjectService().save(RensheProjectWrite(
        chinese_name="待适配草稿", config={**project_data().config.model_dump(),
                                           "occupation_name": "其他职业", "skill_level": "高级工"},
    ), admin_id=1)
    assert project.admission_requirements["scope"]["occupation_name"] == "其他职业"
    assert project.admission_requirements["scope"]["skill_level"] == "高级工"


def test_partial_or_ambiguous_selection_is_rejected():
    with pytest.raises(ValidationError, match="职业和对应技能等级"):
        RensheProjectWrite(chinese_name="项目", occupation_code="code")
    with pytest.raises(ValidationError, match="不能同时提交"):
        RensheProjectWrite(chinese_name="项目", occupation_code="code", skill_level="中级工", config={})


@pytest.mark.asyncio
async def test_registry_selection_uses_its_own_config_and_locks_used_projects(isolated, monkeypatch):
    programs = supported_programs()
    second = deepcopy(programs[0])
    for target in (second, second["config"]):
        target.update(occupation_name="测试适配职业", occupation_code="test-adapted", skill_level="高级工", export_skill_level="三级")
    monkeypatch.setattr("app.services.renshe_project.supported_programs", lambda: [*programs, second])
    service = RensheProjectService()
    selected = await service.save(RensheProjectWrite(
        chinese_name="新增适配项测试", occupation_code=second["occupation_code"], skill_level=second["skill_level"],
    ), admin_id=1)
    assert selected.config.occupation_name == "测试适配职业"
    assert selected.config.export_skill_level == "三级"
    project = await service.save(project_data(), admin_id=1)
    await create_batch(project)
    with pytest.raises(BusinessException, match="不能更换职业或等级"):
        await service.save(RensheProjectWrite(
            chinese_name=project.chinese_name, expected_version=project.version,
            occupation_code=second["occupation_code"], skill_level=second["skill_level"],
        ), product_id=project.id, admin_id=1)
