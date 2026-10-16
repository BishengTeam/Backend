"""The client's 25-column import contract, independent of degree eligibility."""

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.port.exceptions import BusinessException, ConflictException

CONTRACT_VERSION = 2
DEGREE_CONDITION = "取得本专业或相关专业的技工院校或中等及以上职业院校、专科及以上普通高等学校毕业证书（含在读应届毕业生）。"
OCCUPATIONS = ["在校学生", "应届毕业生", "职工", "失业人员", "其他"]
EDUCATIONS = {
    "secondary_vocational": "中专",
    "technical_school": "技校",
    "vocational_high": "职高",
    "higher_vocational": "高职",
    "associate": "大专",
    "bachelor": "大学本科",
    "master": "硕士",
    "doctorate": "博士",
}
POLITICS = [
    "中共党员",
    "中共预备党员",
    "共青团员",
    "民革会员",
    "民盟盟员",
    "民建会员",
    "群众",
    "农工党党员",
    "致公党党员",
    "九三学社社员",
    "台盟盟员",
    "无党派民主人士",
    "民进会员",
]
ETHNICITIES = "汉族 蒙古族 回族 藏族 维吾尔族 苗族 彝族 壮族 布依族 朝鲜族 满族 侗族 瑶族 白族 土家族 哈尼族 哈萨克族 傣族 黎族 傈僳族 佤族 畲族 高山族 拉祜族 水族 东乡族 纳西族 景颇族 柯尔克孜族 土族 达斡尔族 仫佬族 羌族 布朗族 撒拉族 毛南族 仡佬族 锡伯族 阿昌族 普米族 塔吉克族 怒族 乌孜别克族 俄罗斯族 鄂温克族 德昂族 保安族 裕固族 京族 塔塔尔族 独龙族 鄂伦春族 赫哲族 门巴族 珞巴族 基诺族 其他".split()
HEADERS = [
    "姓名",
    "性别",
    "出生日期",
    "证件类型",
    "证件号码",
    "考生来源",
    "文化程度",
    "民族",
    "政治面貌",
    "联系电话",
    "通讯地址",
    "工作单位",
    "从事职业",
    "职业（工种）名称",
    "职业（工种）等级",
    "职称专业名称",
    "职称等级",
    "参加工作日期（入学日期）",
    "职业年限",
    "职业",
    "申报条件",
    "申报类型",
    "考试类型",
    "社保参保单位",
    "电子邮箱",
]
REPORT_FIELDS = {
    "occupation_status",
    "ethnicity",
    "political_status",
    "has_work_experience",
    "work_start_date",
    "enrollment_date",
    "occupation_years",
    "work_unit",
    "social_insurance_unit",
}
OCCUPATION_CODE = "4-04-04-02-网络与信息安全管理员-信息安全管理员-中级工"


def education_config(frozen: dict) -> dict:
    result = dict(frozen)
    if not is_v2(frozen.get("reporting_policy", {})):
        equivalent = {
            "technical_school": "secondary_vocational",
            "vocational_high": "secondary_vocational",
            "higher_vocational": "associate",
        }
        result["education_levels"] = [
            key
            for key in EDUCATIONS
            if equivalent.get(key, key) in frozen["education_levels"]
        ]
    return result


def is_v2(policy: dict) -> bool:
    return policy.get("contract_version") == CONTRACT_VERSION


def default_policy() -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "version": "registration-v2",
        "confirmed_by_client": False,
        "export_values": {
            "candidate_source": "社会人员",
            "application_condition": DEGREE_CONDITION,
        },
        "employment_reporting": {
            "confirmed": False,
            "date_rule": None,
            "years_rule": None,
            "allow_zero_years": None,
        },
    }


def employment_policy_errors(policy: dict) -> list[str]:
    rules = policy.get("employment_reporting")
    if (
        not isinstance(rules, dict)
        or rules.get("confirmed") is not True
        or rules.get("date_rule")
        not in {"work_only", "enrollment_only", "work_or_enrollment", "optional"}
        or rules.get("years_rule") not in {"required", "optional"}
        or not isinstance(rules.get("allow_zero_years"), bool)
    ):
        return ["请确认学历型R、S列的日期规则、年限必填规则及零年限是否可报送"]
    return []


def validate_reporting(info: dict | None, policy: dict) -> dict:
    from app.schemas.renshe import RensheReportingInfo

    if not is_v2(policy) or employment_policy_errors(policy):
        raise ConflictException("学历型报送口径尚未确认，请联系管理员")
    try:
        model = RensheReportingInfo.model_validate(info or {})
    except ValueError as exc:
        raise BusinessException(
            "请完整填写从事职业、民族、政治面貌及真实工作经历；日期不得晚于今天"
        ) from exc
    if (
        model.occupation_status not in OCCUPATIONS
        or model.ethnicity not in ETHNICITIES
        or model.political_status not in POLITICS
    ):
        raise BusinessException("从事职业、民族或政治面貌不在模板允许选项中")
    if not model.has_work_experience and model.work_start_date is not None:
        raise BusinessException("未参加过工作时不能填写参加工作日期")
    if model.has_work_experience and model.work_start_date is None:
        raise BusinessException("请填写真实参加工作日期")
    if not model.has_work_experience and model.occupation_years not in (
        None,
        Decimal(0),
    ):
        raise BusinessException("未参加过工作时职业年限不能大于零")
    rules = policy["employment_reporting"]
    report_date(model.model_dump(mode="json"), rules)
    if rules["years_rule"] == "required" and model.occupation_years is None:
        raise BusinessException(
            "本批次报送要求填写实际职业年限，不以工龄作为学历型报考门槛"
        )
    if model.occupation_years == 0 and not rules["allow_zero_years"]:
        raise BusinessException(
            "本批次尚不接受零年限报送，请联系管理员核实；不要填写虚假年限"
        )
    return model.model_dump(mode="json")


def report_date(info: dict, rules: dict) -> str | None:
    work, enrollment = info.get("work_start_date"), info.get("enrollment_date")
    value = {
        "work_only": work,
        "enrollment_only": enrollment,
        "work_or_enrollment": work or enrollment,
        "optional": work or enrollment,
    }.get(rules["date_rule"])
    if value is None and rules["date_rule"] != "optional":
        raise BusinessException(
            "请按本批次报送口径填写参加工作日期或入学日期；毕业日期不能替代"
        )
    return value


async def resolve_contract(db, plan, config_id: int | None = None) -> dict:
    """An old application can explicitly adopt a confirmed reporting revision.

    Admission, price, plan and orders stay bound to the original batch. Only
    reporting data is snapshotted when the applicant saves a new submission.
    """
    from app.models.cert_product import CertProduct
    from app.models.renshe_project import RensheProjectConfig
    from app.services.renshe_graduated import is_graduated, template_path
    from app.services.renshe_project import batch_configuration, configuration_errors

    frozen = batch_configuration(plan)
    if not is_graduated(frozen):
        raise ConflictException("历史学生批次使用原报名规则")
    product = await db.scalar(
        select(CertProduct).where(
            CertProduct.code == plan.product_type, CertProduct.type == "renshe"
        )
    )
    if product is None:
        raise ConflictException("报送配置缺少项目归属")
    if config_id is not None:
        row = await db.get(RensheProjectConfig, config_id)
    elif is_v2(frozen.get("reporting_policy", {})):
        row = await db.get(RensheProjectConfig, plan.renshe_config_id)
    else:
        row = await db.scalar(
            select(RensheProjectConfig)
            .where(RensheProjectConfig.product_id == product.id)
            .order_by(RensheProjectConfig.version.desc())
            .limit(1)
        )
    if row is None or row.product_id != product.id:
        raise ConflictException("报送配置不属于本批次项目")
    if is_v2(frozen.get("reporting_policy", {})) and row.id != plan.renshe_config_id:
        raise ConflictException("不能更换已冻结批次的报送口径")
    config = row.config
    if (
        not is_graduated(config)
        or not is_v2(config.get("reporting_policy", {}))
        or configuration_errors(config)
        or any(
            config.get(key) != frozen.get(key)
            for key in (
                "occupation_name",
                "occupation_code",
                "skill_level",
                "export_skill_level",
            )
        )
    ):
        raise ConflictException(
            "项目尚未确认新的模板报送口径，请联系管理员；历史报名资料已保留"
        )
    template_path(config["reporting_policy"])
    return {"config_id": row.id, "config": config}


def public_options(contract: dict, frozen: dict) -> dict:
    config = education_config(frozen)
    policy = contract["config"]["reporting_policy"]
    return {
        "config_id": contract["config_id"],
        "version": policy["version"],
        "education_levels": config["education_levels"],
        "education_labels": EDUCATIONS,
        "institution_types": config["institution_types"],
        "occupations": OCCUPATIONS,
        "ethnicities": ETHNICITIES,
        "political_statuses": POLITICS,
        "candidate_source": "社会人员",
        "application_condition": DEGREE_CONDITION,
        "employment_reporting": policy["employment_reporting"],
        "commitment_template": policy["commitment_template"],
    }


def validate_row(values: list, info: dict, policy: dict) -> None:
    """Always run before new jobs, including historical versions being retried."""
    required = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 13, 14, 19, 20, 21, 22]
    missing = [
        HEADERS[index]
        for index in required
        if values[index] is None or values[index] == ""
    ]
    if missing:
        raise ConflictException("模板必填字段缺失：" + "、".join(missing))
    if (
        values[1] not in {"男", "女"}
        or values[3] != "居民身份证"
        or values[5] != "社会人员"
        or values[6] not in EDUCATIONS.values()
        or values[7] not in ETHNICITIES
        or values[8] not in POLITICS
        or values[12] not in OCCUPATIONS
        or values[14] != "四级"
        or values[19] != OCCUPATION_CODE
        or values[20] != DEGREE_CONDITION
        or values[21] != "学历型"
        or values[22] not in {"新考", "补考"}
    ):
        raise ConflictException("导出值不符合报名模板的下拉字典")
    try:
        date.fromisoformat(str(values[2]))
        validate_reporting(info, policy)
    except ValueError as exc:
        raise ConflictException("模板出生日期格式不正确") from exc


async def validate_snapshot(db, plan, form: dict) -> dict:
    snapshot = form.get("reporting_contract", {})
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("config_id"), int):
        raise ConflictException("该报名版本缺少模板填报信息，请补正后重新初审并导出")
    resolved = await resolve_contract(db, plan, snapshot["config_id"])
    if (
        snapshot.get("policy") != resolved["config"]["reporting_policy"]
        or snapshot.get("attachment_layout") != resolved["config"]["attachment_layout"]
    ):
        raise ConflictException("报名版本的报送口径与确认版本不一致")
    return snapshot["policy"]


def assert_template(workbook) -> None:
    """Validate the actual client workbook, not only the number of columns."""
    if workbook.sheetnames != ["Sheet1", "模板示例", "模板说明", "申报条件"]:
        raise ValueError("报名模板工作表结构不匹配")
    sheet = workbook["Sheet1"]
    if [sheet.cell(1, index).value for index in range(1, 26)] != HEADERS:
        raise ValueError("报名模板25列标题不匹配")
    expected = {
        "B": {"男", "女"},
        "D": {"居民身份证"},
        "F": {"社会人员"},
        "G": set(EDUCATIONS.values()),
        "I": set(POLITICS),
        "M": set(OCCUPATIONS),
        "O": {"四级"},
        "T": {OCCUPATION_CODE},
        "V": {"学历型"},
        "W": {"新考", "补考"},
    }
    for column, choices in expected.items():
        cell = f"{column}2"
        rules = [
            rule
            for rule in sheet.data_validations.dataValidation
            if cell in rule.sqref and rule.type == "list"
        ]
        if not any(
            rule.formula1
            and rule.formula1.startswith('"')
            and choices <= set(rule.formula1.strip('"').split(","))
            for rule in rules
        ):
            raise ValueError(f"模板{column}列下拉字典不匹配")
    condition = workbook["申报条件"]
    if not any(
        row[0] == OCCUPATION_CODE and row[1] == DEGREE_CONDITION
        for row in condition.iter_rows(values_only=True)
    ):
        raise ValueError("模板缺少本期完整学历型申报条件")
    if not any(
        "U2" in rule.sqref
        and rule.type == "list"
        and "申报条件!" in (rule.formula1 or "")
        for rule in sheet.data_validations.dataValidation
    ):
        raise ValueError("申报条件与职业的联动字典不匹配")
