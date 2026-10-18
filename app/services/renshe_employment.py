"""Four-level work/skill paths, separate from frozen degree/student policies."""

from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation

from app.port.exceptions import BusinessException

RULE = "documented_employment_v1"
BASE_MATERIALS = {
    "id_card_front": "身份证正面", "id_card_back": "身份证背面",
    "registration_portrait": "一寸白底近期证件照（≤500 KB）",
    "work_proof": "工龄证明（单位鲜章、编号、手写内容及经办人信息）",
    "social_security_proof": "社保缴费证明（单位一致、验证码完整且有效）",
    "commitment_letter": "承诺书（本人亲笔签名并按手印）",
}
CERTIFICATE_NAME = "职业资格证书及网查截图（合并为同一份JPEG材料）"
REVIEW_CHECKS = {
    "identity": "实名与全部证书、工作及社保材料属于同一人",
    "occupation": "实际从事本职业或资料列明相关职业，结合工作单位与岗位核验",
    "employment": "逐段核对工作经历，合并重叠时间，累计有效年限满足申报条件",
    "work_proof": "工龄证明单位鲜章及编号、手写内容、经办人及电话齐全，经办人不是考生本人",
    "social_security": "社保单位与工作单位一致，累计时长符合要求，验证码完整且在有效期内可验证",
    "photo": "一寸白底近期证件照，不超过500 KB",
    "commitment": "承诺书完整、本人亲笔签名并按手印",
}
QUALIFICATION_FIELDS = {
    "qualification_info.cultural_education": "文化程度",
    "qualification_info.work_experiences": "工作经历（单位、岗位、职业及起止日期）",
    "qualification_info.skill_certificate.occupation_name": "证书职业／工种",
    "qualification_info.skill_certificate.grade": "证书等级",
    "qualification_info.skill_certificate.certificate_number": "证书编号",
    "qualification_info.skill_certificate.issued_on": "证书取得日期",
}


def is_employment(config: dict | None) -> bool:
    return bool(config and config.get("qualification_rule") == RULE)


def review_checks(config: dict) -> dict:
    result = dict(REVIEW_CHECKS)
    if config.get("application_types") == ["复合型"]:
        result["certificate"] = "五级本职业或相关职业证书与同一材料内网查截图一致、可验证；证书取得后相关工作满3年"
    return result


def configuration(selection: dict) -> dict:
    from app.domain.renshe.src.index import MATERIAL_SPECS
    from app.services.renshe_project import FORM_FIELDS, default_configuration
    from app.services.renshe_reporting import EDUCATIONS, default_policy

    if selection["grade"] != 4 or selection["application_type"] not in {"工龄型", "复合型"} or not selection["adapted"]:
        raise BusinessException("该工龄或复合申报路径尚未适配")
    materials = dict(BASE_MATERIALS)
    if selection["application_type"] == "复合型":
        materials["vocational_qualification_certificate"] = CERTIFICATE_NAME
    result = default_configuration()
    result.update(
        **{key: selection[key] for key in ("occupation_name", "occupation_code", "skill_level", "export_skill_level")},
        selection_id=selection["selection_id"], rule_profile=deepcopy(selection),
        qualification_rule=RULE, application_types=[selection["application_type"]],
        education_levels=list(EDUCATIONS), institution_types=[], reporting_policy=default_policy(),
        form_fields=list(FORM_FIELDS), export_template="renshe_employment_v1", attachment_layout="flat",
        materials=[{"kind": kind, "name": name, "required": True, "extensions": sorted(MATERIAL_SPECS[kind].extensions), "max_count": 1} for kind, name in materials.items()],
    )
    return result


def anniversary(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, day=28)


def cumulative_years(experiences: list[dict], *, as_of: date, after: date | None = None) -> Decimal:
    """Merge intervals first; each interval uses calendar anniversaries, not 365d.

    End dates are included. An ongoing interval ends on the assessment date.
    Certificate paths only count days on/after the certificate issue date.
    """
    from datetime import timedelta

    intervals = []
    for item in experiences:
        start = date.fromisoformat(str(item["start_date"]))
        end = date.fromisoformat(str(item.get("end_date") or as_of))
        if end > as_of or start > end:
            raise BusinessException("工作经历起止日期无效或晚于核算日期")
        start = max(start, after) if after else start
        if start <= end:
            intervals.append((start, end + timedelta(days=1)))
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    total = Decimal(0)
    for start, end in merged:
        years = end.year - start.year
        if anniversary(start, years) > end:
            years -= 1
        last, following = anniversary(start, years), anniversary(start, years + 1)
        total += Decimal(years) + Decimal((end - last).days) / Decimal((following - last).days)
    return total


def validate_qualification(form: dict, config: dict, *, as_of: date | None = None) -> dict:
    from app.schemas.renshe import RensheEmploymentQualification
    from app.services.renshe_graduated import china_today

    try:
        info = RensheEmploymentQualification.model_validate(form.get("qualification_info") or {}).model_dump(mode="json")
    except ValueError as exc:
        raise BusinessException("请完整填写文化程度、工作经历及本路径要求的证书信息；日期不得晚于今天") from exc
    today = as_of or china_today()
    report = form.get("reporting_info") or {}
    if not report.get("has_work_experience") or not report.get("work_unit") or not report.get("social_insurance_unit"):
        raise BusinessException("工龄型及复合型须填写实际工作单位、社保参保单位及工作经历")
    first_start = min(item["start_date"] for item in info["work_experiences"])
    if not report.get("work_start_date") or str(report["work_start_date"]) > first_start:
        raise BusinessException("参加工作日期不能晚于所填工作经历的最早开始日期")
    compound = config["application_types"] == ["复合型"]
    certificate = info.get("skill_certificate")
    if compound and (not certificate or certificate["grade"] != 5):
        raise BusinessException("本复合型路径须提供本职业或相关职业五级／初级工证书")
    if not compound and certificate:
        raise BusinessException("工龄型不使用技能证书路径，请选择对应申报类型")
    after = date.fromisoformat(certificate["issued_on"]) if compound else None
    if after and after > today:
        raise BusinessException("证书取得日期不得晚于核算日期")
    required = 3 if compound else 5
    total = cumulative_years(info["work_experiences"], as_of=today, after=after)
    if total < required or (after and anniversary(after, required) > today):
        raise BusinessException(f"{'取得五级证书后' if compound else ''}累计本职业或相关职业工作须满{required}年，重叠经历不重复计算")
    try:
        declared = Decimal(str(report.get("occupation_years")))
    except (InvalidOperation, TypeError) as exc:
        raise BusinessException("请填写实际职业年限") from exc
    all_years = cumulative_years(info["work_experiences"], as_of=today)
    if not declared.is_finite() or declared < required or declared > all_years:
        raise BusinessException("职业年限须满足申报条件，且不能超过所填去重工作经历的累计年限")
    return {"qualification_info": info, "eligibility_snapshot": {"as_of": today.isoformat(), "effective_years": str(total), "required_years": required}}
