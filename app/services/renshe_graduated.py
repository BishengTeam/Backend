"""Versioned admission for already graduated, level-four applicants.

Templates are uploaded by administrators and frozen by content hash. A batch
uses the reporting policy saved with its immutable project configuration.
"""

import hashlib
import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.port.config import settings
from app.port.exceptions import BusinessException, ConflictException
from app.services.renshe_reporting import is_v2, employment_policy_errors, DEGREE_CONDITION, REPORT_FIELDS

RULE = "related_major_graduated_degree_v1"
INSTITUTION_TYPES = {
    "technical": "技工院校",
    "vocational": "中等及以上职业院校",
    "general_higher": "普通高等学校（专科及以上）",
}
MATERIALS = {
    "id_card_front": "身份证正面",
    "id_card_back": "身份证背面",
    "portrait": "人像照片（二寸免冠照片）",
    "social_security_proof": "社保缴费证明",
    "xuexin_registration": "学信网电子注册表",
    "education_proof": "学历证明",
    "vocational_qualification_certificate": "职业资格证书",
    "professional_title_certificate": "职称证书",
    "commitment_letter": "承诺书（本人亲笔签名并按手印）",
}
LEGACY_MATERIALS = {
    "id_card_front": "身份证正面",
    "id_card_back": "身份证背面",
    "registration_portrait": "一寸白底近期证件照（≤500 KB）",
    "graduation_certificate": "毕业证",
    "education_report": "学信网学历备案表（在线验证有效期设置为6个月）",
    "commitment_letter": "承诺书（本人亲笔签名并按手印）",
}
EXPORT_FILE_NAMES = {
    "id_card_front": "证件正面",
    "id_card_back": "证件背面",
    "portrait": "二寸免冠照片",
    "social_security_proof": "社保证明",
    "xuexin_registration": "学信网电子注册表",
    "education_proof": "学历证明",
    "vocational_qualification_certificate": "职业资格证书",
    "professional_title_certificate": "职称证书",
    "commitment_letter": "承诺书",
}
REVIEW_CHECKS = {
    "identity": "实名信息与全部佐证材料属于同一人",
    "graduated": "已取得毕业证，不使用在读替代材料",
    "education": "学历证明与学信网电子注册表对应且可验证",
    "major": "本专业或规定相关专业（结合证书人工核验）",
    "employment": "社保、职业资格或职称佐证与报送信息相符",
    "photo": "二寸、免冠、近期，规格合格",
    "commitment": "承诺书完整，本人亲笔签名并按手印",
}


def is_graduated(config: dict | None) -> bool:
    return bool(config and config.get("qualification_rule") == RULE)


def policy_errors(policy: dict) -> list[str]:
    if not isinstance(policy, dict):
        return ["报送口径配置格式无效"]
    errors = []
    if policy.get("confirmed_by_client") is not True:
        errors.append("请确认承诺书模板和附件报送口径已获甲方确认")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(policy.get("version", ""))):
        errors.append("请选择甲方确认的报送口径版本")
    template = policy.get("commitment_template", {})
    if not isinstance(template, dict):
        template = {}
    filename = str(template.get("filename", ""))
    if (
        not filename
        or Path(filename).name != filename
        or ":" in filename
        or Path(filename).suffix.lower() not in {".pdf", ".docx"}
        or not re.fullmatch(r"[a-f0-9]{64}", str(template.get("sha256", "")))
    ):
        errors.append("缺少甲方确认的承诺书模板及校验值")
    labels = policy.get("attachment_names", {})
    if not isinstance(labels, dict):
        labels = {}
    if labels:
        # Historical versions froze their labels. New standard versions omit
        # this field and use code-owned names instead.
        if (
            set(labels) != set(LEGACY_MATERIALS)
            or len(set(str(value) for value in labels.values())) != len(LEGACY_MATERIALS)
            or any(
                not isinstance(value, str)
                or not re.fullmatch(r"[\w\u4e00-\u9fff -]{1,64}", value)
                for value in labels.values()
            )
        ):
            errors.append("历史版本附件命名配置无效")
    values = policy.get("export_values", {})
    if not isinstance(values, dict):
        values = {}
    if is_v2(policy):
        if values != {"candidate_source": "社会人员", "application_condition": DEGREE_CONDITION}:
            errors.append("考生来源与申报条件须使用模板原字典，不能自由改写")
        errors.extend(employment_policy_errors(policy))
    elif set(values) != {
        "candidate_source",
        "student_status",
        "application_condition",
    } or any(
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 256
        or "院校学生" in value
        or "应届" in value
        for value in values.values()
    ):
        errors.append("请确认已毕业学历型的报送身份、毕业状态及申报条件值")
    return errors


def template_path(policy: dict) -> Path:
    if policy_errors(policy):
        raise ConflictException("甲方报送口径尚未确认")
    info = policy["commitment_template"]
    root = Path(settings.RENSHE_TEMPLATE_DIR).resolve()
    path = (root / info["filename"]).resolve()
    if path.parent != root or not path.is_file():
        raise ConflictException("承诺书模板尚未部署，请联系管理员")
    if hashlib.sha256(path.read_bytes()).hexdigest() != info["sha256"]:
        raise ConflictException("承诺书模板与确认版本不一致，请联系管理员")
    return path


def activation_errors(config: dict) -> list[str]:
    if not is_graduated(config):
        return [
            "新开放批次仅支持信息安全管理员四级／中级工、已毕业学历型；历史批次保持原规则"
        ]
    if not is_v2(config.get("reporting_policy", {})):
        return ["新批次须使用模板填报第二版，确认R、S列口径；旧批次仅办理历史报名"]
    if {item.get("kind") for item in config.get("materials", [])} != set(MATERIALS):
        return ["新批次必须使用甲方标准九类材料清单"]
    try:
        template_path(config.get("reporting_policy", {}))
    except ConflictException as exc:
        return [exc.message]
    return []


def save_template(filename: str, content: bytes) -> dict:
    """Content-addressed immutable assets shared by all batch versions."""
    import io
    import zipfile

    extension = Path(filename).suffix.lower()
    if not content or len(content) > 10 * 1024 * 1024:
        raise BusinessException("模板不能为空且不得超过10 MB")
    if extension == ".pdf":
        if not content.startswith(b"%PDF-"):
            raise BusinessException("承诺书模板PDF格式不正确")
    elif extension == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if "word/document.xml" not in archive.namelist():
                    raise ValueError()
        except (ValueError, zipfile.BadZipFile) as exc:
            raise BusinessException("承诺书模板DOCX格式不正确") from exc
    else:
        raise BusinessException("模板仅支持PDF或DOCX")
    digest = hashlib.sha256(content).hexdigest()
    root = Path(settings.RENSHE_TEMPLATE_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"commitment_{digest}{extension}"
    # Hash paths are immutable and retries are idempotent.
    if not path.exists():
        try:
            with path.open("xb") as target:
                target.write(content)
        except FileExistsError:
            pass
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ConflictException("模板存储校验失败，请联系管理员")
    return {
        "filename": path.name,
        "sha256": digest,
        "original_filename": Path(filename).name,
    }


def validate_education(info: dict | None, config: dict) -> dict:
    from app.schemas.renshe import RensheGraduatedEducation

    try:
        data = RensheGraduatedEducation.model_validate(info or {}).model_dump(
            mode="json"
        )
    except ValueError as exc:
        raise BusinessException(
            "请完整填写已取得毕业证的学历信息，毕业日期不得晚于今天"
        ) from exc
    if data["institution_type"] not in config["institution_types"]:
        raise BusinessException("本批次不支持该院校类型")
    if data["education"] not in config["education_levels"]:
        raise BusinessException("本批次不支持该学历层次")
    if (
        data["institution_type"] == "general_higher"
        and data["education"] not in {"higher_vocational", "associate", "bachelor", "master", "doctorate"}
    ):
        raise BusinessException("普通高等学校须为专科及以上学历")
    return data


def validate_review(data, *, stage: str, now: datetime, version_id: int) -> None:
    if data.expected_version_id != version_id:
        raise ConflictException("报名版本已变化，请刷新当前版本并重新核验")
    if data.decision == "rejected":
        deadline = data.correction_due_at
        if deadline is None or deadline.tzinfo is None or deadline <= now:
            raise BusinessException(
                "驳回补正必须设置晚于当前时间且带时区的补正截止时间"
            )
        allowed = (
            set(MATERIALS)
            | {
                "education_info." + key
                for key in (
                    "school",
                    "institution_type",
                    "education",
                    "major",
                    "graduation_date",
                    "certificate_number",
                )
            }
            | {"contact_phone", "email", "mailing_address"}
            | {"reporting_info." + key for key in REPORT_FIELDS}
        )
        if not data.required_changes or not set(data.required_changes) <= allowed:
            raise BusinessException("请选择需要补正的具体学历字段、联系方式或材料")
    elif stage == "initial" and (
        set(data.checklist or {}) != set(REVIEW_CHECKS)
        or not all(value is True for value in data.checklist.values())
    ):
        raise BusinessException("请逐项完成七项学历型初审核验后再通过")


def china_today() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()
