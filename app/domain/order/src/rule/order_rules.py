"""订单业务规则：状态转换矩阵、报名数据校验、支付超时配置。"""

ORDER_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"paid", "closed", "completed"},
    "paid": {"completed", "refunded"},
    "completed": {"refunded"},
    "refunded": set(),
    "closed": set(),
}

ORDER_PAYMENT_EXPIRE_MINUTES = 1440

EXTRA_DATA_SCHEMA: dict[str, list[str]] = {
    "H3C-NE": ["gender", "education", "organization", "country", "language",
               "first_name", "last_name", "exam_date"],
    "H3C-SE": ["gender", "education", "organization", "country", "language",
               "first_name", "last_name", "exam_date"],
    "SF-CSE": ["exam_date", "email", "first_name", "last_name",
                "mailing_address", "organization", "exam_direction"],
    "NISP-1": ["pinyin", "major", "school", "province"],
    "NISP-2": ["pinyin", "school", "gender", "age", "education",
               "major", "province", "address", "zip_code"],
    "RS-ZY":  ["branch"],
}

from app.port.exceptions import BusinessException

def validate_extra_data(cert_type: str, extra_data: dict | None) -> None:
    """校验认证类型的报名必填字段。"""
    required = EXTRA_DATA_SCHEMA.get(cert_type)
    if required is None:
        return
    if not extra_data:
        raise BusinessException(f"认证类型 {cert_type} 需要填写报名信息")
    missing = [k for k in required if k not in extra_data or extra_data.get(k) is None]
    if missing:
        raise BusinessException(f"缺少必填字段: {', '.join(missing)}")
