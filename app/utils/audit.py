"""PII-safe values for append-only audit records."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any


# Keys that are routinely populated from user supplied or credential data.
# The audit table is intentionally append-only, so redaction happens before a
# row reaches SQLAlchemy rather than relying on log viewers to hide it later.
_SENSITIVE_KEY = re.compile(
    r"(?:^|[_-])(id[_-]?card|phone|mobile|openid|token|secret|password|"
    r"private[_-]?key|certificate|email|address|real[_-]?name|candidate[_-]?name|"
    r"storage[_-]?key|object[_-]?key|source[_-]?object[_-]?key|"
    r"report[_-]?object[_-]?key|signed[_-]?url|url|oss|source[_-]?storage[_-]?key)"
    r"(?:$|[_-])",
    re.IGNORECASE,
)
_ID_CARD = re.compile(r"(?<!\d)(?:\d{17}[0-9Xx]|\d{15})(?!\d)")
_MOBILE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~-]+")
_OSS_URL = re.compile(
    r"(?i)https?://[^\s\"']*(?:aliyuncs\.com|\.oss[-.]|/oss/)[^\s\"']*"
)
_OSS_URI = re.compile(r"(?i)oss://[^\s\"']+")

REDACTED = "[REDACTED]"


def _redact_string(value: str) -> str:
    value = _ID_CARD.sub(REDACTED, value)
    value = _MOBILE.sub(REDACTED, value)
    value = _BEARER.sub(r"\1" + REDACTED, value)
    value = _OSS_URL.sub(REDACTED, value)
    return _OSS_URI.sub(REDACTED, value)


def redact_sensitive_text(value: str | None) -> str | None:
    """Redact common PII/credential patterns in free-form diagnostics.

    This helper is for exception text and operational log fields where a
    structured key is not available.  Structured audit rows should continue
    to use :func:`sanitize_audit_value` so their shape is preserved.
    """

    if value is None:
        return None
    return _redact_string(value)


def sanitize_audit_value(value: Any, *, key: str | None = None) -> Any:
    """Recursively remove sensitive values while retaining audit structure.

    Numeric counters and status fields remain intact.  Strings under known
    sensitive keys are replaced wholesale; ID-card and mainland mobile
    patterns are also redacted when nested under an otherwise innocuous key.
    """

    if value is None:
        return None
    if key and _SENSITIVE_KEY.search(key):
        return REDACTED
    if isinstance(value, Mapping):
        return {
            str(item_key): sanitize_audit_value(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [sanitize_audit_value(item) for item in value]
    if isinstance(value, str):
        return _redact_string(value)
    # Dates, integers and booleans are JSON serializable (or converted by the
    # SQLAlchemy JSON type) and do not contain PII by themselves.
    return value


def sanitize_audit_summary(summary: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if summary is None:
        return None
    sanitized = sanitize_audit_value(summary)
    return sanitized if isinstance(sanitized, dict) else {"value": sanitized}
