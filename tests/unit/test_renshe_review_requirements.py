from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from app.port.exceptions import BusinessException, ConflictException
from app.schemas.renshe import RensheReviewCreate
from app.services.renshe_catalog import combinations
from app.services.renshe_level_three import material_requirements
from app.services.renshe_project import selection_configuration
from app.services.renshe_review_requirements import build_requirements, resolve_requirements, validate_requirements, checklist_snapshot


CASES = [(selection, branch.get("id")) for selection in combinations() if selection["adapted"]
         for branch in (selection.get("admission_paths") or [{}])]


def sample(selection, branch=None):
    config = selection_configuration(selection)
    form = {"qualification_info": {"admission_path": branch}} if branch else {}
    version = NS(id=12, project_config_id=7, sensitive_cleared_at=None, form_data=form)
    rules = material_requirements(config, form)
    materials = []
    for index, rule in enumerate(rules):
        extension = ".pdf" if ".pdf" in rule["extensions"] else ".jpg"
        materials.append(NS(id=index + 1, kind=rule["kind"], is_deleted=False, storage_key=f"private/{index}",
            extension=extension, original_filename=f"random_{index}{extension}", content_type="application/pdf" if extension == ".pdf" else "image/jpeg", size_bytes=120, sha256="a" * 64))
    return config, version, materials


@pytest.mark.parametrize("selection,branch", CASES, ids=[f"{selection['selection_id']}:{branch or 'default'}" for selection, branch in CASES])
def test_every_open_trade_and_actual_path_has_exact_requirements(selection, branch):
    config, version, materials = sample(selection, branch)
    result = build_requirements(config, version, materials)
    assert result["status"] == "resolved"
    assert {item["kind"] for item in result["material_requirements"]} == {item.kind for item in materials}
    expected = {"id_card_front", "id_card_back", "registration_portrait", "commitment_letter"}
    if branch in {"work_10", "skill_4_work_4", "title_work_1"} or selection["grade"] == 5 or selection["grade"] == 4 and selection["application_type"] != "学历型":
        expected |= {"work_proof", "social_security_proof"}
    if branch in {"skill_4_degree", "technical_senior", "evaluated_college"} or selection["grade"] == 4 and selection["application_type"] == "学历型":
        expected |= {"graduation_certificate", "education_report"}
    if branch in {"skill_4_work_4", "skill_4_degree"} or selection["grade"] == 4 and selection["application_type"] == "复合型":
        expected.add("vocational_qualification_certificate")
    if branch == "title_work_1":
        expected.add("professional_title_certificate")
    assert {item["kind"] for item in result["material_requirements"]} == expected
    assert all(item["valid"] for item in result["material_requirements"])
    other = next(group for group in result["groups"] if group["id"] == "other")
    if selection["grade"] == 4 and selection["application_type"] == "学历型":
        assert "form_data.reporting_info" in other["field_paths"]
    checks = result["checks"]
    assert len({item["key"] for item in checks}) == len(checks)
    assert all(item["group_id"] in {group["id"] for group in result["groups"]} for item in checks)
    payload = RensheReviewCreate(expected_version_id=12, decision="approved", checklist={item["key"]: True for item in checks})
    validate_requirements(payload, result, stage="initial")
    if branch:
        spec = next(item for item in selection["admission_paths"] if item["id"] == branch)
        keys = {item["key"] for item in checks}
        assert ("employment" in keys) == bool(spec.get("work"))
        assert ("certificate" in keys) == bool(spec.get("skill"))
        assert ("title" in keys) == bool(spec.get("title"))
        assert ("education" in keys) == bool(spec.get("education"))
        assert result["path"]["condition"] == spec["condition"]
        assert ("education_info.training_level" in result["correction_fields"]) == (branch == "technical_senior")
        assert ("education_info.evaluation_reference" in result["correction_fields"]) == (branch == "evaluated_college")
    # Counts do not authorize a different key, even when every submitted value is true.
    forged = dict(payload.checklist); forged.pop(next(iter(forged))); forged["wrong-path"] = True
    with pytest.raises(BusinessException):
        validate_requirements(payload.model_copy(update={"checklist": forged}), result, stage="initial")
    materials.pop()
    result = build_requirements(config, version, materials)
    with pytest.raises(BusinessException, match="材料"):
        validate_requirements(payload, result, stage="initial")


def test_extra_unknown_materials_cleared_versions_and_snapshot_isolation():
    selection, branch = CASES[0]
    config, version, materials = sample(selection, branch)
    materials.append(NS(id=99, kind="unknown-historical-proof"))
    result = build_requirements(config, version, materials)
    assert any(group["id"] == "extra" for group in result["groups"])
    snapshot = checklist_snapshot(result)
    result["checks"][0]["description"] = "changed"
    assert snapshot["checks"][0]["description"] != "changed"
    version.sensitive_cleared_at = datetime.now(timezone.utc)
    result = build_requirements(config, version, [])
    assert result["status"] == "cleared" and result["material_requirements"] == []


def test_wrong_path_corrections_and_unadapted_combinations_are_rejected():
    selection, branch = next((selection, branch) for selection, branch in CASES if branch == "title_work_1")
    config, version, materials = sample(selection, branch)
    result = build_requirements(config, version, materials)
    with pytest.raises(BusinessException, match="适用"):
        validate_requirements(RensheReviewCreate(expected_version_id=12, decision="rejected", reason="补正", required_changes=["graduation_certificate"]), result, stage="initial")
    version.form_data["qualification_info"]["admission_path"] = "other"
    assert build_requirements(config, version, materials)["status"] == "unresolved"
    config["qualification_rule"] = "unadapted"
    assert build_requirements(config, version, materials)["status"] == "unresolved"


@pytest.mark.asyncio
async def test_missing_version_configuration_never_uses_current_product_defaults():
    selection, branch = CASES[0]
    config, version, materials = sample(selection, branch)
    result = await resolve_requirements(NS(get=AsyncMock(return_value=None)), NS(renshe_config_id=123), version, materials)
    assert result["status"] == "unresolved" and "不存在" in result["block_reason"]
    version.project_config_id = None
    result = await resolve_requirements(NS(), NS(renshe_config_id=123), version, materials)
    assert result["status"] == "unresolved" and "缺少配置关联" in result["block_reason"]
