"""Provider-safe payment identifiers."""

from __future__ import annotations

import re
import secrets

WECHAT_OUT_TRADE_NO_MAX_LENGTH = 32
_OUT_TRADE_PREFIX = re.compile(r"[^0-9A-Za-z_-]")


def generate_out_trade_no(prefix: str = "ORD") -> str:
    """Return a random WeChat-compatible merchant order number.

    V3 limits ``out_trade_no`` to 32 ASCII characters.  User IDs and
    millisecond timestamps can exceed that bound as the database grows, so
    they are deliberately not embedded in the provider identifier.
    """

    normalized_prefix = _OUT_TRADE_PREFIX.sub("", prefix)[:8] or "ORD"
    entropy_length = WECHAT_OUT_TRADE_NO_MAX_LENGTH - len(normalized_prefix)
    return normalized_prefix + secrets.token_hex(16)[:entropy_length]


__all__ = ["WECHAT_OUT_TRADE_NO_MAX_LENGTH", "generate_out_trade_no"]
