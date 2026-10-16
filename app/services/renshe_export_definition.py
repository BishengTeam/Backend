"""Current reporting contract. Product editors cannot change these rules.

Material stems follow the client's current standard package. New batches reuse
the verified two-inch portrait; historical batches keep their admission rules.
Extensions always describe the actual file.
"""

import hashlib
import inspect
import json
import re
from datetime import date
from pathlib import Path

from app.port.exceptions import BusinessException

REGISTRATION_TEMPLATE_NAME = "报名信息.xlsx"
WORK_HISTORY_TEMPLATE_NAME = "工作经历.xlsx"
MATERIAL_STEMS = {
    "id_card_front": "证件正面",
    "id_card_back": "证件背面",
    "registration_portrait": "二寸免冠照片",
    "portrait": "二寸免冠照片",
    "graduation_certificate": "学历证明",
    "education_proof": "学历证明",
    "education_report": "学信网电子注册表",
    "xuexin_registration": "学信网电子注册表",
    "commitment_letter": "承诺书",
    "student_card": "学生证",
    "social_security_proof": "社保证明",
    "vocational_qualification_certificate": "职业资格证书",
    "professional_title_certificate": "职称证书",
}
EMPLOYMENT_REPORTING = {
    "confirmed": True,
    "date_rule": "work_only",
    "years_rule": "required",
    "allow_zero_years": True,
}
RULE_ID = "renshe-current-standard-package-work-date-20261017"


def archive_download_name(plan_id: int | str, volume_no: int) -> str:
    return f"人社报名_批次{plan_id}_第{volume_no}卷.zip"


def employment_rules() -> dict:
    return dict(EMPLOYMENT_REPORTING)


def work_date(info: dict) -> str:
    value = info.get("work_start_date")
    if not value:
        raise BusinessException(
            "请补齐真实参加工作日期；入学日期、毕业日期不能替代，未参加工作请联系管理员"
        )
    try:
        parsed = date.fromisoformat(str(value))
    except ValueError as exc:
        raise BusinessException("参加工作日期格式无效") from exc
    return f"{parsed.year}-{parsed.month}-{parsed.day}"


def material_archive_path(id_card: str, kind: str, extension: str) -> str:
    if not re.fullmatch(r"\d{17}[\dXx]", id_card):
        raise BusinessException("导出快照中的身份证号无效")
    if kind not in MATERIAL_STEMS or extension.lower() not in {".jpg", ".jpeg", ".pdf"}:
        raise BusinessException("导出材料类型或真实扩展名不受支持")
    return f"{id_card}_{MATERIAL_STEMS[kind]}{extension.lower()}"


def definition_fingerprint(registration: Path, work: Path) -> str:
    from app.services.renshe_reporting import HEADERS, DEGREE_CONDITION
    from app.services.renshe_export import build_registration_row

    content = {
        "rule": RULE_ID,
        "headers": HEADERS,
        "condition": DEGREE_CONDITION,
        "mapping": inspect.getsource(build_registration_row),
        "materials": MATERIAL_STEMS,
        "employment": employment_rules(),
        "templates": [
            hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (registration, work)
        ],
    }
    return hashlib.sha256(
        json.dumps(content, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def export_instructions() -> dict:
    from app.services.renshe_reporting import HEADERS

    examples = [
        ("id_card_front", ".jpg"),
        ("id_card_back", ".jpeg"),
        ("portrait", ".jpg"),
        ("social_security_proof", ".pdf"),
        ("xuexin_registration", ".pdf"),
        ("education_proof", ".jpeg"),
        ("vocational_qualification_certificate", ".jpeg"),
        ("professional_title_certificate", ".jpeg"),
        ("commitment_letter", ".pdf"),
    ]
    masked = "110101********123X"
    paths = [REGISTRATION_TEMPLATE_NAME, WORK_HISTORY_TEMPLATE_NAME]
    paths += [
        material_archive_path("11010119900101123X", kind, ext).replace(
            "11010119900101123X", masked
        )
        for kind, ext in examples
    ]
    sources = [
        "本人实名姓名",
        "本人实名性别",
        "本人实名出生日期",
        "居民身份证",
        "本人实名身份证号",
        "社会人员",
        "本次报名学历（按模板文化程度转换）",
        "本次填报民族",
        "本次填报政治面貌",
        "本次联系电话",
        "本次通讯地址",
        "本次工作单位（选填）",
        "本人实际从事职业",
        "信息安全管理员",
        "四级",
        "学历型不填写职称专业",
        "学历型不填写职称等级",
        "真实参加工作日期，格式如2022-9-21；不使用入学或毕业日期",
        "实际职业年限，必填，允许0",
        "模板职业代码",
        "模板完整学历型申报条件原文",
        "学历型",
        "批次考试类型（新考／补考）",
        "实际社保参保单位（选填）",
        "本次电子邮箱",
    ]
    tree = [
        archive_download_name("编号", 1),
        f"├─ {paths[0]}",
        f"├─ {paths[1]}",
    ]
    for i, path in enumerate(paths[2:]):
        tree.append(
            f"{'└─' if i == len(paths[2:]) - 1 else '├─'} {path.rsplit('/', 1)[-1]}"
        )
    return {
        "registration_template": REGISTRATION_TEMPLATE_NAME,
        "work_template": WORK_HISTORY_TEMPLATE_NAME,
        "work_description": "原样附带甲方工作经历模板，目前不会按考生自动填写经历；后续按报送要求补充。",
        "directory_note": "目录示例，实际内容以报名材料为准；文件保留真实扩展名。",
        "portrait_note": "新标准批次复用已审核实名二寸免冠照片；历史批次仍按原报名要求办理，导出使用固定材料名称。",
        "example_paths": paths,
        "file_tree": "\n".join(tree),
        "columns": [
            {"column": i, "header": h, "source": s}
            for i, (h, s) in enumerate(zip(HEADERS, sources), 1)
        ],
    }
