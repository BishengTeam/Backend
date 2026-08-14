"""Password hashing primitives shared by runtime and one-time bootstrap.

This module deliberately has no application-settings or database imports so
the isolated bootstrap service can create the first administrator only after
the schema migration has completed.
"""

from __future__ import annotations

import hashlib
import secrets


SALT_LENGTH = 32
HASH_ITERATIONS = 600_000


def hash_admin_password(password: str) -> str:
    salt = secrets.token_bytes(SALT_LENGTH)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        HASH_ITERATIONS,
    )
    return salt.hex() + ":" + digest.hex()


def verify_admin_password(password: str, stored_hash: str) -> bool:
    try:
        salt_hex, digest_hex = stored_hash.split(":", 1)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        HASH_ITERATIONS,
    )
    return secrets.compare_digest(actual, expected)
