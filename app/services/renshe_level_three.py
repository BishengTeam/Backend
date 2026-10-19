"""Grade-three paths from the client's conditions and material guide, pages 4–5."""

from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation

from app.port.exceptions import BusinessException

RULE = "level_three_v1"
BASE = ["id_card_front", "id_card_back", "registration_portrait", "commitment_letter"]
PATHS = {
    "work_10": {"type": "工龄型", "condition_index": 0, "years": 10, "work": True},
    "skill_4_work_4": {"type": "复合型", "condition_index": 0, "years": 4, "work": True, "skill": True},
    "title_work_1": {"type": "复合型", "condition_index": 1, "years": 1, "work": True, "title": True},
    "skill_4_degree": {"type": "复合型", "condition_index": 2, "years": 0, "skill": True, "education": True},
    "technical_senior": {"type": "学历型", "condition_index": 0, "years": 0, "education": True},
    "evaluated_college": {"type": "学历型", "condition_index": 1, "years": 0, "education": True},
}


def is_level_three(config: dict | None) -> bool:
    return bool(config and config.get("qualification_rule") == RULE)


def paths(application_type: str, conditions: list[str]) -> list[dict]:
    result = []
    for key, spec in PATHS.items():
        if spec["type"] != application_type:
            continue
        kinds = BASE + (["work_proof", "social_security_proof"] if spec.get("work") else [])
        kinds += ["vocational_qualification_certificate"] if spec.get("skill") else []
        kinds += ["professional_title_certificate"] if spec.get("title") else []
        kinds += ["graduation_certificate", "education_report"] if spec.get("education") else []
        result.append({"id": key, **spec, "condition": conditions[spec["condition_index"]], "material_kinds": kinds})
    return result


def configuration(selection: dict) -> dict:
    from app.domain.renshe.src.index import MATERIAL_SPECS
    from app.services.renshe_employment import BASE_MATERIALS, CERTIFICATE_NAME
    from app.services.renshe_project import default_configuration
    from app.services.renshe_reporting import EDUCATIONS, default_policy

    if selection["grade"] != 3 or not selection["adapted"]:
        raise BusinessException("该三级组合尚未适配")
    branches = selection["admission_paths"]
    names = {**BASE_MATERIALS, "vocational_qualification_certificate": CERTIFICATE_NAME,
             "professional_title_certificate": "初级职称证书及网查截图（合并为同一份JPEG材料）",
             "graduation_certificate": "毕业证书",
             "education_report": "学历核验材料（证书与学历路径为学信网备案表，其他学历路径为学历网查截图PDF）"}
    all_kinds = list(dict.fromkeys(kind for branch in branches for kind in branch["material_kinds"]))
    common = set.intersection(*(set(branch["material_kinds"]) for branch in branches))
    config = default_configuration()
    config.update(
        **{key: selection[key] for key in ("occupation_name", "occupation_code", "skill_level", "export_skill_level")},
        selection_id=selection["selection_id"], rule_profile=deepcopy(selection), qualification_rule=RULE,
        application_types=[selection["application_type"]], education_levels=list(EDUCATIONS),
        institution_types=["technical", "vocational"] + ([] if selection["occupation_key"] == "ai_trainer" else ["general_higher"]),
        reporting_policy=default_policy(), export_template="renshe_employment_v1", attachment_layout="flat",
        materials=[{"kind": kind, "name": names[kind], "required": kind in common,
                    "extensions": sorted(MATERIAL_SPECS[kind].extensions), "max_count": 1} for kind in all_kinds],
    )
    config["rule_profile"]["review_checks"] = review_checks(config)
    return config


def selected_path(config: dict, form: dict) -> dict:
    key = (form.get("qualification_info") or {}).get("admission_path")
    branch = next((item for item in config.get("rule_profile", {}).get("admission_paths", []) if item["id"] == key), None)
    if branch is None:
        raise BusinessException("请选择本三级批次支持的具体申报路径")
    return branch


def material_requirements(config: dict, form: dict) -> list[dict]:
    if not is_level_three(config):
        return config["materials"]
    branch = selected_path(config, form)
    return [{**item, "required": True} for item in config["materials"] if item["kind"] in branch["material_kinds"]]


def review_checks(config: dict) -> dict:
    from app.services.renshe_employment import REVIEW_CHECKS
    result = {key: REVIEW_CHECKS[key] for key in ("identity", "photo", "commitment")}
    result["path"] = "核对所选三级申报路径与实际证书、职称、工作或毕业学历材料一致；不得借用其他路径条件"
    result["occupation"] = "逐项核对本职业、相关职业或专业对应关系，不仅核验附件存在"
    if config["application_types"] != ["学历型"]:
        result["qualification"] = "工龄型满10年；复合型分别核验四级证书后满4年、初级职称后满1年或四级证书加对应毕业学历；仅核验当前所选分支"
        result["proof"] = "当前路径要求的工作证明、社保、技能或初级职称证书与网查一致；单位、时间、验证码及经办人有效"
    if config["application_types"] != ["工龄型"]:
        result["education"] = "涉及学历时核对已取得毕业证、相关专业及学历报告；技工院校须高级工班及以上；评估院校须核验其备案等级认定评价机构资格；证书加学历路径须高职或专科以上，备案表有效期6个月；学历型参加工作日期可为空，工作信息由管理员人工审核"
    return result


def validate_admission(form: dict, config: dict, *, as_of: date | None = None) -> dict:
    from app.schemas.renshe import RensheLevelThreeQualification
    from app.services.renshe_employment import anniversary, cumulative_years
    from app.services.renshe_graduated import china_today, validate_education

    try:
        info = RensheLevelThreeQualification.model_validate(form.get("qualification_info") or {}).model_dump(mode="json")
    except ValueError as exc:
        raise BusinessException("请完整填写三级申报路径、文化程度及本路径要求的证书信息") from exc
    branch = selected_path(config, {"qualification_info": info})
    today = as_of or china_today()
    if config["rule_profile"].get("grade") != 3 or config["export_skill_level"] != "三级":
        raise BusinessException("三级资格规则与报考等级不一致")
    skill, title = info.get("skill_certificate"), info.get("title_certificate")
    if bool(skill) != bool(branch.get("skill")) or (skill and skill["grade"] != 4):
        raise BusinessException("当前申报路径须如实提供四级／中级工技能证书，不使用其他等级或无关证书")
    if bool(title) != bool(branch.get("title")):
        raise BusinessException("当前申报路径须如实提供初级职称证书及专业对应信息")
    education = None
    if branch.get("education"):
        education = validate_education(form.get("education_info"), config)
        if branch["id"] == "technical_senior":
            if education["institution_type"] != "technical" or education.get("training_level") not in {"senior_worker", "preparatory_technician", "technician"}:
                raise BusinessException("本路径仅接受技工院校高级工班及以上毕业证书，请核对院校类型及培养层次")
        else:
            if education["institution_type"] not in {"vocational", "general_higher"} or education["education"] not in {"higher_vocational", "associate", "bachelor", "master", "doctorate"}:
                raise BusinessException("本路径须为高等职业学校或专科及以上普通高等学校毕业学历")
            if branch["id"] == "evaluated_college" and not education.get("evaluation_reference"):
                raise BusinessException("请填写院校评估论证依据，由初审核验其备案等级认定评价机构资格")
    elif form.get("education_info"):
        raise BusinessException("当前工龄或职称路径不使用毕业学历资格")
    after = date.fromisoformat((skill if branch.get("skill") else title)["issued_on"]) if branch["years"] and (skill or title) else None
    if any(date.fromisoformat(item["issued_on"]) > today for item in (skill, title) if item):
        raise BusinessException("证书取得日期不得晚于核算日期")
    report = form.get("reporting_info") or {}
    total = Decimal(0)
    if branch.get("work"):
        experiences = info["work_experiences"]
        if not experiences or not report.get("has_work_experience") or not str(report.get("work_unit") or "").strip() or not str(report.get("social_insurance_unit") or "").strip():
            raise BusinessException("本路径须填写实际工作经历、工作单位及社保参保单位")
        if not report.get("work_start_date") or str(report["work_start_date"]) > min(item["start_date"] for item in experiences):
            raise BusinessException("参加工作日期不能晚于工作经历最早开始日期")
        total = cumulative_years(experiences, as_of=today, after=after)
        if total < branch["years"] or (after and anniversary(after, branch["years"]) > today):
            raise BusinessException(f"本三级路径须{'证书取得后' if after else ''}累计相关工作满{branch['years']}年，重叠经历不重复计算")
        try:
            declared = Decimal(str(report.get("occupation_years")))
        except (InvalidOperation, TypeError) as exc:
            raise BusinessException("请填写真实职业年限") from exc
        if not declared.is_finite() or declared < branch["years"] or declared > cumulative_years(experiences, as_of=today):
            raise BusinessException("职业年限须满足所选三级路径且不能超过实际去重经历")
    elif info["work_experiences"]:
        raise BusinessException("当前学历或证书加学历路径不使用工作经历资格，请清除其他路径的数据")
    result = {"qualification_info": info, "eligibility_snapshot": {"as_of": today.isoformat(), "admission_path": branch["id"], "application_condition": branch["condition"], "required_years": branch["years"], "effective_years": str(total)}}
    if education:
        result["education_info"] = education
    return result
