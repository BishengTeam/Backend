"""Shared utilities for order services."""

PRICE_TIER_STUDENT = "student"
PRICE_TIER_NORMAL = "normal"


def resolve_price_tier(user_type: str | None) -> str:
    return PRICE_TIER_STUDENT if user_type == PRICE_TIER_STUDENT else PRICE_TIER_NORMAL
