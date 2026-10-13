from datetime import datetime, timedelta, timezone

from app.integrations.wechat_pay import WechatPayAPIError
from app.services.refund_recovery import (
    format_refund_error,
    provider_refund_is_missing,
    refund_retry_delay,
    refund_retry_is_due,
)


def test_format_refund_error_keeps_provider_code_and_http_status():
    error = WechatPayAPIError("NOT_ENOUGH", 403)

    assert format_refund_error(error) == "WechatPayAPIError:NOT_ENOUGH/HTTP 403"


def test_provider_missing_error_is_recognized():
    assert provider_refund_is_missing(WechatPayAPIError("RESOURCE_NOT_EXISTS", 404))
    assert not provider_refund_is_missing(WechatPayAPIError("NOT_ENOUGH", 403))


def test_retry_backoff_reaches_configured_ceiling():
    assert refund_retry_delay(0) < refund_retry_delay(1)
    assert refund_retry_delay(100) == refund_retry_delay(99)


def test_retry_is_due_only_after_elapsed_delay():
    now = datetime.now(timezone.utc)

    assert not refund_retry_is_due(
        last_attempt_at=now - timedelta(seconds=1),
        retry_count=0,
        now=now,
    )
    assert refund_retry_is_due(
        last_attempt_at=now - timedelta(seconds=61),
        retry_count=0,
        now=now,
    )


def test_missing_last_attempt_is_due():
    assert refund_retry_is_due(
        last_attempt_at=None,
        retry_count=10,
        now=datetime.now(timezone.utc),
    )
