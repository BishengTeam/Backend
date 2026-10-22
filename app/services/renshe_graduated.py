"""Versioned admission for already graduated, level-four applicants.

Commitment files are uploaded by administrators and stored by content hash.
Reporting columns, dates and filenames are maintained in the current code.
"""

import hashlib
import logging
import os
import re
import stat
import tempfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.port.config import settings
from app.port.exceptions import BusinessException, ConflictException, StorageUnavailableException
from app.services.renshe_reporting import REPORT_FIELDS

RULE = "related_major_graduated_degree_v1"
logger = logging.getLogger(__name__)


def commitment_directory() -> Path:
    return Path(settings.RENSHE_COMMITMENT_TEMPLATE_DIR or Path(settings.UPLOAD_DIR) / "private" / "renshe-commitment-templates").resolve()


def _storage_unavailable(operation: str, exc: OSError) -> StorageUnavailableException:
    logger.exception("renshe.commitment_template.storage_failed operation=%s errno=%s", operation, exc.errno)
    return StorageUnavailableException("承诺书模板存储不可用，请联系管理员检查目录权限和持久化配置")
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
REVIEW_CHECKS = {
    "identity": "实名信息与全部佐证材料属于同一人",
    "graduated": "已取得毕业证，不使用在读替代材料",
    "education": "学历证明与学信网电子注册表对应且可验证",
    "major": "本专业或规定相关专业（结合证书人工核验）",
    "employment": "社保、职业资格或职称佐证与报送信息相符",
    "photo": "二寸、免冠、近期，规格合格",
    "commitment": "承诺书完整，本人亲笔签名并按手印",
}
DEGREE_REVIEW_CHECKS = {
    "identity": "实名、毕业证及学历报告属于同一人",
    "graduated": "已取得毕业证，不接受在读替代证明",
    "education": "院校类型及学历符合本期学历型条件；参加工作日期可为空，工作信息由管理员人工审核",
    "major": "本专业或资料列明相关专业，别名及不明确匹配人工核验",
    "report": "学历备案表对应、可验证，验证有效期设置为6个月",
    "photo": "一寸白底近期证件照，不超过500 KB",
    "commitment": "承诺书完整、本人亲笔签名并按手印",
}


def review_checks(config: dict, form_data: dict | None = None) -> dict:
    from app.services.renshe_level_three import is_level_three, review_checks as three_checks
    if is_level_three(config):
        return three_checks(config, form_data=form_data)
    from app.services.renshe_employment import is_employment, review_checks as employment_checks
    if is_employment(config):
        return employment_checks(config)
    kinds = {item["kind"] for item in config.get("materials", [])}
    return DEGREE_REVIEW_CHECKS if "registration_portrait" in kinds else REVIEW_CHECKS


def is_graduated(config: dict | None) -> bool:
    return bool(config and config.get("qualification_rule") == RULE)


def is_self_reported(config: dict | None) -> bool:
    from app.services.renshe_employment import is_employment
    return is_graduated(config) or is_employment(config)


def validate_admission(form: dict, config: dict, *, as_of: date | None = None, verified_birth_date: str | None = None, age_as_of: date | None = None) -> dict:
    from app.services.renshe_level_three import is_level_three, validate_admission as three_admission
    if is_level_three(config):
        return three_admission(form, config, as_of=as_of)
    from app.services.renshe_employment import is_employment, validate_qualification
    from app.services.renshe_reporting import education_config
    if is_employment(config):
        if form.get("education_info"):
            raise BusinessException("工龄或证书路径不使用毕业学历资格，请按所选路径填写")
        return validate_qualification(form, config, as_of=as_of, verified_birth_date=verified_birth_date, age_as_of=age_as_of)
    if form.get("qualification_info"):
        raise BusinessException("学历型不使用工龄或技能证书路径")
    return {"education_info": validate_education(form.get("education_info"), education_config(config))}


def policy_errors(policy: dict) -> list[str]:
    if not isinstance(policy, dict):
        return ["报送口径配置格式无效"]
    errors = []
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
    return errors


def commitment_policy(config: dict) -> dict:
    return {"commitment_template": config.get("commitment_template") or config.get("reporting_policy", {}).get("commitment_template")}


def template_path(policy: dict) -> Path:
    if policy_errors(policy):
        raise ConflictException("承诺书模板尚未配置，请上传模板并保存项目")
    info = policy["commitment_template"]
    try:
        # Old administrator-uploaded files remain readable on the static RO mount.
        # Never copy or write anything to that directory.
        roots = (commitment_directory(), Path(settings.RENSHE_TEMPLATE_DIR).resolve())
        for root in dict.fromkeys(roots):
            path = (root / info["filename"]).resolve()
            if path.parent != root:
                raise ConflictException("承诺书模板路径无效，请联系管理员")
            try:
                metadata = path.stat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise ConflictException("承诺书模板文件无效，请联系管理员")
            if hashlib.sha256(path.read_bytes()).hexdigest() != info["sha256"]:
                raise ConflictException("承诺书模板与确认版本不一致，请联系管理员")
            return path
    except OSError as exc:
        raise _storage_unavailable("read", exc) from exc
    raise ConflictException("承诺书模板尚未部署，请联系管理员")


def activation_errors(config: dict) -> list[str]:
    from app.services.renshe_catalog import find_selection
    if config.get("selection_id"):
        selected = find_selection(config["selection_id"])
        if selected is None or not selected["adapted"]:
            return ["该组合尚未适配，仅可保存草稿，不能启用或发布招生"] + (selected["missing"] if selected else [])
    if not is_self_reported(config):
        return [
            "新开放批次须绑定已适配的报考项目配置；请检查项目、职业等级和申报类型，历史批次保持原规则"
        ]
    selected = find_selection(config.get("selection_id"))
    from app.services.renshe_project import selection_configuration
    expected = selection_configuration(selected) if selected and selected["adapted"] else None
    if expected is None or config.get("materials") != expected["materials"] or config.get("qualification_rule") != expected["qualification_rule"]:
        return ["批次资格规则或材料与所选报考组合不一致，请重新选择对应项目配置；历史批次保持原要求"]
    try:
        template_path(commitment_policy(config))
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
    temporary = None
    try:
        root = commitment_directory()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = root / f"commitment_{digest}{extension}"
        if path.resolve().parent != root:
            raise ConflictException("模板存储路径无效，请联系管理员")
        # Publish only complete files. Concurrent identical retries are safe;
        # interrupted writes never expose partial PDFs under the final hash.
        if not path.exists():
            with tempfile.NamedTemporaryFile(dir=root, prefix=".commitment_", delete=False) as target:
                temporary = Path(target.name)
                target.write(content)
                target.flush()
                os.fsync(target.fileno())
            try:
                # Same-directory hard link publishes a complete file atomically
                # without replacing an existing hash, including concurrent reads.
                os.link(temporary, path)
            except FileExistsError:
                pass
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ConflictException("模板存储校验失败，请联系管理员")
    except OSError as exc:
        raise _storage_unavailable("write", exc) from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                logger.warning("renshe.commitment_template.temporary_cleanup_failed", exc_info=True)
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
    if data["education"] in {"primary", "junior_high", "senior_high", "other"}:
        raise BusinessException("文化程度可如实填写，但该学历不属于本期已毕业学历型支持范围，请联系管理员")
    if (
        data["institution_type"] == "general_higher"
        and data["education"] not in {"higher_vocational", "associate", "bachelor", "master", "doctorate"}
    ):
        raise BusinessException("普通高等学校须为专科及以上学历")
    return data


def validate_review(data, *, stage: str, now: datetime, version_id: int, config: dict | None = None, form_data: dict | None = None, requirements: dict | None = None) -> None:
    if requirements is not None:
        from app.services.renshe_review_requirements import validate_requirements
        validate_requirements(data, requirements, stage=stage)
        if data.decision == "rejected":
            deadline = data.correction_due_at
            if deadline is None or deadline.tzinfo is None or deadline <= now:
                raise BusinessException("驳回补正必须设置晚于当前时间且带时区的补正截止时间")
        return
    if data.expected_version_id != version_id:
        raise ConflictException("报名版本已变化，请刷新当前版本并重新核验")
    if data.decision == "rejected":
        deadline = data.correction_due_at
        if deadline is None or deadline.tzinfo is None or deadline <= now:
            raise BusinessException(
                "驳回补正必须设置晚于当前时间且带时区的补正截止时间"
            )
        allowed = (
            (set(MATERIALS) | set(LEGACY_MATERIALS) if config is None else {item["kind"] for item in config["materials"]})
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
        from app.services.renshe_employment import is_employment, QUALIFICATION_FIELDS
        from app.services.renshe_level_three import is_level_three
        if config is not None and is_level_three(config):
            allowed |= {"qualification_info.admission_path", "education_info.training_level", "education_info.evaluation_reference"}
            allowed |= {"qualification_info.title_certificate." + key for key in ("title_name", "level", "major", "certificate_number", "issued_on")}
        if config is not None and is_employment(config):
            if not is_level_three(config):
                allowed -= {"education_info." + key for key in ("school", "institution_type", "education", "major", "graduation_date", "certificate_number")}
            allowed |= set(QUALIFICATION_FIELDS)
        if not data.required_changes or not set(data.required_changes) <= allowed:
            raise BusinessException("请选择需要补正的具体学历字段、联系方式或材料")
    elif stage == "initial" and (
        set(data.checklist or {}) != set(review_checks(config) if config is not None else REVIEW_CHECKS)
        or not all(value is True for value in data.checklist.values())
    ):
        raise BusinessException("请逐项完成七项学历型初审核验后再通过" if config is None or is_graduated(config) else "请逐项完成当前工龄或证书路径的初审核验后再通过")


def china_today() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()
