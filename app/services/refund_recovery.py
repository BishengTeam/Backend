"""Shared recovery semantics for WeChat Pay refund tasks."""

from __future__ import annotations

from datetime import datetime

from app.integrations.wechat_pay import WechatPayAPIError
from app.port.config import settings


def refund_retry_delay(retry_count: int) -> int:
    delays = [
        int(value)
        for value in settings.WECHAT_PAY_REFUND_RETRY_DELAYS_SECONDS.split(",")
        if value.strip()
    ]
    return delays[min(max(retry_count, 0), len(delays) - 1)]


def refund_retry_is_due(
    *, last_attempt_at: datetime | None, retry_count: int, now: datetime
) -> bool:
    if last_attempt_at is None:
        return True
    return now.timestamp() - last_attempt_at.timestamp() >= refund_retry_delay(retry_count)


def format_refund_error(exc: Exception) -> str:
    if isinstance(exc, WechatPayAPIError):
        return f"WechatPayAPIError:{exc.api_code}/HTTP {exc.status_code}"
    rendered = str(exc).strip()
    return f"{type(exc).__name__}:{rendered}" if rendered else type(exc).__name__


def provider_refund_is_missing(exc: WechatPayAPIError) -> bool:
    return exc.status_code == 404 or exc.api_code in {
        "NOT_FOUND",
        "RESOURCE_NOT_EXISTS",
        "REFUND_NOT_EXISTS",
    }


__all__ = [
    "format_refund_error",
    "provider_refund_is_missing",
    "refund_retry_delay",
    "refund_retry_is_due",
]
