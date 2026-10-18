"""Source-backed choices; catalog existence is distinct from implementation readiness.

Sources are local client documents in DOCS/docs/甲方/认证材料/人设报名模版.
No catalog entry grants eligibility without an adapted admission profile.
"""

from copy import deepcopy

LEVELS = {
    5: ("五级", "初级工"), 4: ("四级", "中级工"), 3: ("三级", "高级工"),
    2: ("二级", "技师"), 1: ("一级", "高级技师"),
}
CURRENT_SELECTION = "information_security:4:学历型"
ADAPTED_DEGREE_OCCUPATIONS = {
    "information_security", "network_security", "internet_review", "human_resources",
    "labor_relations", "ai_trainer", "robot_operator",
}
CONDITION_SOURCE = "职业技能等级认定职业工种申报条件.pdf，第1页"
MATERIAL_SOURCE = "职业技能等级认定申报条件相关解答.pdf，第1—9页"
CONDITIONS = {
    5: {
        "工龄型": ["年满16周岁，从事本职业或相关职业工作。"],
    },
    4: {
        "工龄型": ["累计从事本职业或相关职业工作满5年。"],
        "复合型": ["取得本职业或相关职业五级/初级工职业资格（职业技能等级）证书后，累计从事本职业或相关职业工作满3年。"],
        "学历型": ["取得本专业或相关专业的技工院校或中等及以上职业院校、专科及以上普通高等学校毕业证书（含在读应届毕业生）。"],
    },
    3: {
        "工龄型": ["累计从事本职业或相关职业工作满10年。"],
        "复合型": ["取得本职业或相关职业四级/中级工证书后累计从事本职业或相关职业工作满4年。", "取得符合专业对应关系的初级职称后累计从事本职业或相关职业工作满1年。", "取得本职业或相关职业四级证书，并取得本专业或相关专业高职、专科及以上毕业证书（含在读应届毕业生）。"],
        "学历型": ["取得本专业或相关专业的技工院校高级工班及以上毕业证书（含在读应届毕业生）。", "取得经评估论证的高职、专科及以上本专业或相关专业毕业证书（含在读应届毕业生）。"],
    },
    2: {"复合型": ["三级证书后累计从事本职业或相关职业工作满5年。", "初级职称后累计工作满5年，且三级证书后工作满1年。", "中级职称后累计工作满1年。", "具有三级证书的高级技校、技师学院毕业生累计工作满2年。", "取得三级证书满2年的技师学院预备技师班、技师班学生。"]},
    1: {"复合型": ["二级证书后累计从事本职业或相关职业工作满5年。", "中级职称后累计工作满5年，且二级证书后工作满1年。", "高级职称后累计从事本职业或相关职业工作满1年。"]},
}
SECURITY_MAJORS = "信息安全、网络空间安全、网络信息安全、网络与信息安全、信息安全与管理、网络安全与执法、保密技术、大数据技术与应用、电子技术应用、电子商务技术、电子与计算机工程、电子与信息技术、工业互联网技术应用、计算机程序设计、计算机科学与技术、计算机网络技术、计算机网络应用、网络工程、计算机系统与维护、计算机信息管理、计算机应用技术、计算机应用与维修、计算机与数码产品维修、空间信息与数字技术、区块链工程、人工智能技术服务、人工智能技术应用、软件工程、软件技术、软件与信息服务、数据科学与大数据技术、数字媒体技术、数字媒体技术应用、通信技术、通信网络应用、通信系统工程安装与维护、通信运营服务、网络安防系统安装与维护、网站建设与管理、物联网工程、物联网技术应用、物联网应用技术、新媒体技术、虚拟现实技术、虚拟现实技术应用、虚拟现实应用技术、移动应用技术与服务、移动应用开发、云计算技术应用、云计算技术与应用、智能科学与技术等。"

# key, occupation, concrete trade, official code, applicable grades, professional scope
OCCUPATIONS = [
    ("information_security", "网络与信息安全管理员", "信息安全管理员", "4-04-04-02", [5, 4, 3, 2, 1], SECURITY_MAJORS),
    ("network_security", "网络与信息安全管理员", "网络安全管理员", "4-04-04-02", [5, 4, 3, 2, 1], SECURITY_MAJORS),
    ("internet_review", "网络与信息安全管理员", "互联网信息审核员", "4-04-04-02", [5, 4, 3, 2, 1], "含网络与信息安全专业，以及传播学、法学、社会学等扩展专业；完整范围见申报条件PDF第3页，须逐项人工核对。"),
    ("human_resources", "企业人力资源管理师", "企业人力资源管理师", "4-07-03-04", [4, 3, 2, 1], "工商企业管理、行政管理、管理科学、劳动与社会保障、劳动经济、劳动关系等。"),
    ("labor_relations", "劳动关系协调员", "劳动关系协调员", "4-07-03-02", [4, 3, 2, 1], "劳动与社会保障、劳动经济学、人力资源管理、工商企业管理、法学、社会学等。"),
    ("ai_trainer", "人工智能训练师", "人工智能训练师", "4-04-05-05", [4, 3, 2, 1], "技工学校：电工电子类、信息类等；中等及以上职业学校：电子与信息大类、医学卫生大类、装备制造大类、教育与体育大类等。普通高等学校专业范围待核实。"),
    ("robot_operator", "工业机器人系统操作员", "工业机器人系统操作员", "6-30-99-00", [4, 3, 2], "加工制造类、机电设备类、机械类、电气类、自动化类、电子信息类、计算机类、通信类等。"),
    ("business_data", "商务数据分析师", "商务数据分析师", "4-07-02-05", [4, 3], "现有模板提供四级、三级职业代码及条件，相关专业与院校细则待补齐。"),
]
RELATED = {
    "information_security": "信息安全测试员、信息通信网络运行管理员、信息通信信息化系统管理员等。",
    "human_resources": "企业管理、行政管理、管理咨询、管理研究等；相关资格包括劳动关系协调员等。",
    "labor_relations": "人力资源管理、劳动保障事务处理、社会工作等；相关资格包括企业人力资源管理师、劳动保障协理员、劳动保障专理员、社会工作者等。",
    "ai_trainer": "人工智能工程技术人员、呼叫中心服务员、电子商务师等。",
    "robot_operator": "焊工、机械设备维修人员、机械冷加工人员、金属加工机械制造人员、信息通信网络运行管理人员、计算机制造人员等。",
}


def combinations() -> list[dict]:
    results = []
    for key, occupation, trade, code, grades, majors in OCCUPATIONS:
        for grade in grades:
            export_level, skill_level = LEVELS[grade]
            for application_type, conditions in CONDITIONS[grade].items():
                selection_id = f"{key}:{grade}:{application_type}"
                adapted = key in ADAPTED_DEGREE_OCCUPATIONS and grade == 4
                five_work = grade == 5 and application_type == "工龄型"
                degree = application_type == "学历型"
                institution_types = ["technical", "vocational"] + ([] if key == "ai_trainer" else ["general_higher"])
                missing = []
                if not adapted:
                    missing = ["尚未实现该申报路径的专项字段、材料、初审核验及导出联调"]
                    if application_type in {"工龄型", "复合型"}:
                        missing += ["需适配工作经历、工龄与社保证明；涉及技能证书或职称时须校验证书取得日期及对应关系"]
                    if grade == 3 and application_type == "学历型":
                        missing += ["需区分技工院校高级工班与经评估论证院校路径，补齐培养层次和院校核验"]
                    if grade == 5:
                        missing += ["五级申报类型在PDF与个人申报表间存在差异，待业务确认"]
                    if key == "business_data":
                        missing += ["相关职业、专业及院校细则待补齐"]
                if five_work:
                    missing = ["PDF将本路径标为工龄型，个人申报表归为无类型；外部系统的五级工龄型分类及申报条件值尚未确认，须完成甲方导入验证后方可启用"]
                sources = [CONDITION_SOURCE, MATERIAL_SOURCE]
                sources.append("职业技能等级认定申报条件相关解答.pdf，第10—12页" if key != "business_data" else "注意.txt；报名信息.xlsx／申报条件工作表")
                if grade == 5:
                    sources.append("四川省职业技能等级认定个人申报表.docx，填表说明第1条：两条年龄路径均为无类型；解答PDF第2页分类存在差异，待确认")
                results.append({
                    "selection_id": selection_id, "occupation_key": key,
                    "occupation_title": occupation, "occupation_name": trade,
                    "official_code": code, "grade": grade, "skill_level": skill_level,
                    "export_skill_level": export_level, "level_label": f"{export_level}／{skill_level}",
                    "occupation_code": f"{code}-{occupation}-{trade}-{skill_level}" if occupation != trade else f"{code}-{trade}-{skill_level}",
                    "application_type": application_type, "adapted": adapted,
                    "implementation_ready": adapted or five_work,
                    "reporting_confirmed": grade != 5 and adapted,
                    "status": "已适配" if adapted else "待适配",
                    "adaptation_status": "已适配" if adapted else "待业务确认" if grade == 5 or key == "business_data" else "待开发",
                    "missing": missing,
                    "institution_types": institution_types if adapted and degree else [],
                    "source_conditions": deepcopy(conditions), "sources": sources,
                    "related_majors": majors,
                    "related_occupations": RELATED.get(key, RELATED["information_security"] if key in {"network_security", "internet_review"} else "相关职业细则待补齐，不套用其他职业。"),
                    "project_limits": ((["本期只接受已取得毕业证，不开放在读应届替代证明", "不能提供既定学历报告时联系管理员，不开放替代材料线上报名"] + (["人工智能训练师仅开放资料明确的技工院校、中等及以上职业院校；普通高等学校专业范围待核实"] if key == "ai_trainer" else [])) if degree else ["工作经历须为本职业或资料列明相关职业，重叠时间不重复累计", "工龄证明单独提交；证书与网查截图合并在同一份JPEG材料中", "复合型仅开放取得五级证书后累计相关工作满3年路径" if application_type == "复合型" else "累计相关职业工作满5年，工龄及社保须逐项初审核验"]) if adapted else ["仅可保存草稿，禁止启用、发布及新报名"],
                })
                if grade == 5:
                    results[-1]["project_limits"] = [
                        "年满16周岁，按北京时间首次正式提交日期核算；补正沿用首次提交基准",
                        "工龄型须提供相关职业工作经历、工龄证明及社保证明；不设置最低工作年数，不要求前置证书或毕业学历材料" if five_work else "拟从事相关职业；PDF本行未列额外佐证材料，报名及报送规则待适配",
                        "拟适配要求，仅可保存草稿；报送分类未确认，禁止启用及发布招生",
                    ]
    return results


def find_selection(selection_id: str | None) -> dict | None:
    return next((item for item in combinations() if item["selection_id"] == selection_id), None)
