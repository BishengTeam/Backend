"""One version-bound definition for review presentation, validation and audit."""
from copy import deepcopy
import re

from app.domain.renshe.src.index import MATERIAL_SPECS
from app.models.renshe_project import RensheProjectConfig
from app.port.exceptions import BusinessException, ConflictException

GROUPS = {
    "identity": ("身份信息", ["realname_snapshot", "form_data.eligibility_snapshot.age_as_of"]),
    "work": ("工作经历及证明", ["form_data.qualification_info.work_experiences", "form_data.reporting_info.work_start_date", "form_data.reporting_info.occupation_years", "form_data.reporting_info.work_unit", "form_data.reporting_info.social_insurance_unit", "form_data.eligibility_snapshot.as_of", "form_data.eligibility_snapshot.required_years", "form_data.eligibility_snapshot.effective_years"]),
    "skill": ("技能证书及网查材料", ["form_data.qualification_info.skill_certificate"]),
    "title": ("职称证书及网查材料", ["form_data.qualification_info.title_certificate"]),
    "education": ("毕业学历及核验材料", ["form_data.education_info"]),
    "student": ("学生资料", ["student_snapshot"]),
    "photo": ("证件照", []), "commitment": ("承诺书", []),
    "other": ("其他报名信息", ["form_data.contact_phone", "form_data.email", "form_data.mailing_address", "form_data.qualification_info.cultural_education", "form_data.reporting_info.occupation_status", "form_data.reporting_info.ethnicity", "form_data.reporting_info.political_status", "form_data.reporting_info.has_work_experience", "form_data.reporting_info.enrollment_date"]),
    "extra": ("其他已提交材料", []),
}
MATERIAL_GROUPS = {
    "id_card_front": "identity", "id_card_back": "identity", "portrait": "photo", "registration_portrait": "photo",
    "work_proof": "work", "social_security_proof": "work", "vocational_qualification_certificate": "skill",
    "professional_title_certificate": "title", "graduation_certificate": "education", "education_report": "education",
    "student_card": "student", "education_proof": "student", "xuexin_registration": "student", "commitment_letter": "commitment",
}
CHECK_TITLES = {
    "identity": "身份一致", "age": "年满16周岁", "occupation": "职业及专业相关性", "employment": "工作年限",
    "work_proof": "工龄证明", "social_security": "社保证明", "photo": "照片规格", "commitment": "签字及手印",
    "certificate": "技能证书及网查", "title": "职称证书及网查", "path": "申报路径一致",
    "graduated": "已取得毕业证", "education": "院校及学历", "major": "专业相关性", "report": "学历核验材料",
    "training": "培养层次", "institution_evaluation": "院校评估论证依据",
}
PATH_LABELS = {"work_10": "累计相关工作满10年", "skill_4_work_4": "四级证书后累计工作满4年", "title_work_1": "初级职称后累计工作满1年", "skill_4_degree": "四级证书加毕业学历", "technical_senior": "技工院校高级工班及以上毕业", "evaluated_college": "经评估论证院校毕业"}
EDUCATION_FIELDS = ("graduated", "school", "institution_type", "education", "major", "graduation_date", "certificate_number", "training_level", "evaluation_reference")
LEGACY_FILE_CITATIONS = {
    "职业技能等级认定职业工种申报条件.pdf，第1页",
    "职业技能等级认定申报条件相关解答.pdf，第1—9页",
    "职业技能等级认定申报条件相关解答.pdf，第10—12页",
    "注意.txt；报名信息.xlsx／申报条件工作表",
}


def references(config):
    from app.services.renshe_references import reference_list
    return reference_list(config.get("rule_profile", {}).get("grade"))


def unresolved(version, reason, status="unresolved"):
    return {"status": status, "block_reason": reason, "version_id": version.id,
            "project_config_id": version.project_config_id, "path": {}, "groups": [],
            "material_requirements": [], "checks": [], "references": [], "source_notes": [], "correction_fields": []}


def build_requirements(config, version, materials):
    from app.services.renshe_graduated import review_checks
    from app.services.renshe_level_three import is_level_three, material_requirements, selected_path
    if version.sensitive_cleared_at is not None:
        return unresolved(version, "本版本敏感资料已按保留规则清理", "cleared")
    if config.get("qualification_rule") not in {"related_major_current_graduating_student", "related_major_graduated_degree_v1", "documented_employment_v1", "level_five_employment_v1", "level_three_v1"}:
        return unresolved(version, "该报名规则尚未适配，不能套用其他组合")
    profile = config.get("rule_profile", {})
    if profile and not profile.get("adapted"):
        return unresolved(version, "该组合尚未适配，不能审核")
    form = version.form_data or {}
    try:
        branch = selected_path(config, form) if is_level_three(config) else None
        required = material_requirements(config, form)
        definitions = review_checks(config, form_data=form)
    except (BusinessException, KeyError, TypeError, ValueError) as exc:
        return unresolved(version, getattr(exc, "message", "申报路径或冻结规则无法解析"))
    kinds = {item["kind"] for item in required}
    active_groups = {MATERIAL_GROUPS.get(kind, "extra") for kind in kinds} | {"identity", "other"}
    if any(item.kind not in kinds for item in materials):
        active_groups.add("extra")
    groups = []
    for key, (title, paths) in GROUPS.items():
        if key not in active_groups:
            continue
        paths = list(paths)
        if key == "identity" and profile.get("grade") != 5:
            paths.remove("form_data.eligibility_snapshot.age_as_of")
        if key == "other" and "work" not in active_groups:
            # Work reporting remains reviewable for degree applicants, without
            # inventing a mandatory work-qualification group for their path.
            paths = [path for path in paths if not path.startswith("form_data.reporting_info.")] + ["form_data.reporting_info"]
        groups.append({"id": key, "title": title, "field_paths": paths})
    primary = next((key for key in ("work", "skill", "title", "education", "student") if key in active_groups), "other")
    correction_fields = {"contact_phone", "email", "mailing_address"}
    from app.services.renshe_reporting import REPORT_FIELDS
    correction_fields |= {"reporting_info." + key for key in REPORT_FIELDS}
    if "education" in active_groups:
        correction_fields |= {"education_info." + key for key in EDUCATION_FIELDS if key not in {"training_level", "evaluation_reference"}}
        if branch and branch["id"] == "technical_senior":
            correction_fields.add("education_info.training_level")
        if branch and branch["id"] == "evaluated_college":
            correction_fields.add("education_info.evaluation_reference")
    if "work" in active_groups:
        correction_fields |= {"qualification_info.work_experiences", "qualification_info.cultural_education"}
    for group, prefix in (("skill", "skill_certificate"), ("title", "title_certificate")):
        if group in active_groups:
            correction_fields |= {f"qualification_info.{prefix}.{key}" for key in ("occupation_name", "grade", "title_name", "level", "major", "certificate_number", "issued_on") if (key not in {"title_name", "level", "major"} if group == "skill" else key not in {"occupation_name", "grade"})}
    if branch:
        correction_fields.add("qualification_info.admission_path")
    checks = []
    for key, description in definitions.items():
        group = ("identity" if key in {"identity", "age"} else "photo" if key == "photo" else "commitment" if key == "commitment"
                 else "skill" if key == "certificate" else "title" if key == "title"
                 else "education" if key in {"education", "graduated", "major", "report", "training", "institution_evaluation"} and "education" in active_groups
                 else "work" if key in {"employment", "work_proof", "social_security"} and "work" in active_groups else primary)
        related = list(kinds) if key == "identity" else [kind for kind in kinds if MATERIAL_GROUPS.get(kind) == group]
        checks.append({"key": key, "label": CHECK_TITLES.get(key, "资格核验"), "description": description, "group_id": group,
                       "related_material_kinds": sorted(related), "related_field_paths": GROUPS[group][1]})
    records = {item.kind: item for item in materials}
    material_rules = []
    for item in required:
        record = records.get(item["kind"])
        spec = MATERIAL_SPECS.get(item["kind"])
        valid = bool(record and not record.is_deleted and record.storage_key and re.fullmatch(r"[a-f0-9]{64}", record.sha256 or "")
                     and spec and 0 < record.size_bytes <= spec.max_bytes and record.extension.lower() in item["extensions"]
                     and record.content_type.split(";", 1)[0].lower() in spec.content_types
                     and (record.content_type.split(";", 1)[0].lower() == "application/pdf" if record.extension.lower() == ".pdf" else record.content_type.split(";", 1)[0].lower() in {"image/jpeg", "image/pjpeg"}))
        material_rules.append({**item, "group_id": MATERIAL_GROUPS.get(item["kind"], "extra"), "max_bytes": spec.max_bytes if spec else None,
                               "material_id": record.id if record else None, "valid": valid,
                               "error": None if valid else "材料已清理" if record and record.is_deleted else "未提交或材料格式、大小及快照记录无效"})
    path_id = branch["id"] if branch else config.get("selection_id") or config["qualification_rule"]
    condition = branch["condition"] if branch else "；".join(profile.get("source_conditions", []))
    return {"status": "resolved", "block_reason": None, "version_id": version.id, "project_config_id": version.project_config_id,
            "path": {"id": path_id, "label": PATH_LABELS.get(path_id, "／".join(config.get("application_types", []))),
                     "occupation": config["occupation_name"], "level": "／".join(filter(None, (config.get("export_skill_level"), config["skill_level"]))), "application_type": "／".join(config.get("application_types", [])),
                     "condition": condition, "related_majors": profile.get("related_majors"), "related_occupations": profile.get("related_occupations"),
                     "institution_types": config.get("institution_types", [])},
            "groups": groups, "checks": checks, "material_requirements": material_rules, "correction_fields": sorted(correction_fields),
            "references": profile.get("references") or references(config),
            "source_notes": list(dict.fromkeys([*profile.get("project_limits", []), *(source for source in profile.get("sources", []) if source not in LEGACY_FILE_CITATIONS)]))}


async def version_configuration(db, plan, version):
    from app.services.renshe_project import batch_configuration
    from app.schemas.renshe_project import RensheProjectConfiguration
    if plan is None:
        raise ConflictException("关联批次不存在")
    if version.project_config_id is not None:
        row = await db.get(RensheProjectConfig, version.project_config_id)
        if row is None:
            raise ConflictException("本版本绑定的项目配置不存在")
        # Validate the frozen structure, never compare it to today's product rules.
        config = RensheProjectConfiguration.model_validate(deepcopy(row.config)).model_dump()
        from app.models.cert_product import CertProduct
        product = await db.get(CertProduct, row.product_id)
        if product is None or product.code != plan.product_type or any(config.get(key) != getattr(plan, key) for key in ("occupation_name", "occupation_code", "skill_level")) or plan.application_type not in config.get("application_types", []):
            raise ConflictException("报名版本配置与报考项目或批次不一致")
        return config
    if plan.renshe_config_id is not None:
        raise ConflictException("历史版本缺少配置关联，无法可靠确认适用规则")
    return batch_configuration(plan)


async def resolve_requirements(db, plan, version, materials):
    if version.sensitive_cleared_at is not None:
        return unresolved(version, "本版本敏感资料已清理", "cleared")
    try:
        return build_requirements(await version_configuration(db, plan, version), version, materials)
    except (BusinessException, ConflictException, KeyError, TypeError, ValueError) as exc:
        return unresolved(version, getattr(exc, "message", "版本规则无法可靠解析"))


def validate_requirements(data, requirements, *, stage):
    if requirements["status"] != "resolved":
        raise ConflictException(requirements["block_reason"] or "本版本审核要求无法解析")
    if data.expected_version_id != requirements["version_id"]:
        raise ConflictException("报名版本已变化，请刷新当前版本并重新核验")
    if data.decision == "rejected":
        allowed = set(requirements["correction_fields"]) | {item["kind"] for item in requirements["material_requirements"]}
        if not data.required_changes or not set(data.required_changes) <= allowed:
            raise BusinessException("请选择当前实际申报路径适用的字段或材料进行补正")
    elif stage == "initial":
        missing = [item["name"] for item in requirements["material_requirements"] if item["required"] and not item["valid"]]
        if missing:
            raise BusinessException("请补齐有效报名材料：" + "、".join(missing))
        keys = {item["key"] for item in requirements["checks"]}
        if set(data.checklist or {}) != keys or not all(value is True for value in (data.checklist or {}).values()):
            raise BusinessException("请刷新页面并逐项完成当前路径初审核验" + ("（七项）" if len(keys) == 7 else ""))


def checklist_snapshot(requirements):
    return deepcopy({key: requirements[key] for key in ("version_id", "project_config_id", "path", "checks")})
