"""Shared pure functions and constants for the points mall domain."""

from datetime import datetime, timedelta, timezone

CATEGORY_LABELS = {
    "certification": "认证报名",
    "course": "课程",
    "quiz": "题库",
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def discount_label(discount_type: str, discount_value: int) -> str:
    if discount_type == "percent":
        return f"{discount_value / 10}折"
    return f"¥{discount_value / 100:.2f}"


def scope_label(scope_type: str, scope_value: str | None) -> str:
    if scope_type == "global":
        return "全部商品"
    if scope_type == "category":
        return CATEGORY_LABELS.get(scope_value or "", scope_value or "指定分类")
    return f"指定商品: {scope_value}"


def calculate_discounted_price(
    *,
    original_price_cents: int,
    discount_type: str,
    discount_value: int,
) -> int:
    if discount_type == "percent":
        return round(original_price_cents * discount_value / 100)
    return max(0, original_price_cents - discount_value)


def check_coupon_scope(
    *,
    scope_type: str,
    scope_value: str | None,
    product_type: str,
    product_category: str | None,
) -> bool:
    if scope_type == "global":
        return True
    if scope_type == "category":
        return product_category == scope_value
    if scope_type == "product":
        return product_type == scope_value
    return False


def effective_status(status: str, expires_at: datetime) -> str:
    if status == "used":
        return "used"
    if expires_at <= now_utc():
        return "expired"
    return "unused"
