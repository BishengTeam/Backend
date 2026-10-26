"""Directions that agree across all three lists in the supplied 2026 workbook."""

DIRECTIONS = {
    "SCSA-S": "安全服务认证工程师 SCSA-S",
    "SCSA-T": "安全技术认证工程师 SCSA-T",
    "SCCA-C": "云计算认证工程师 SCCA-C",
    "SCSP-S": "安全服务认证资深工程师 SCSP-S",
    "SCCP-C": "云计算认证资深工程师 SCCP-C",
    "SCSE-T": "安全技术认证专家SCSE-T",
    "SCSE-S": "安全服务认证专家SCSE-S",
    "SCCE-C": "云计算认证专家SCCE-C",
}
SOURCE = "2026年深信服 认证报名表模板.xlsx：Sheet1 J2、J3、J4:J198 的一致目录"
HEADERS = ["考生姓名*", "身份证号码*（请仔细核对）", "手机号*（请仔细核对）", "考试时间*",
           "邮寄地址", "邮箱地址", "所属机构*", "firstName*", "lastName*", "报考方向*", "券码*", "备注1", "备注2"]


def require_direction(code: str) -> str:
    from app.port.exceptions import BusinessException
    if code not in DIRECTIONS:
        raise BusinessException("该深信服报考方向尚未确认，不能启用或发布招生")
    return DIRECTIONS[code]
