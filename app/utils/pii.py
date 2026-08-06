"""Deterministic keyed hashes used for equality checks without logging PII."""

import hashlib
import hmac

from app.port.config import settings


def identity_hash(id_card_number: str) -> str:
    normalized = id_card_number.strip().upper()
    key = settings.PII_HASH_KEY.encode("utf-8")
    return hmac.new(key, normalized.encode("utf-8"), hashlib.sha256).hexdigest()
