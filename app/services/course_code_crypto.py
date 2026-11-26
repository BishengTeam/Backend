"""课程兑换码加解密（vweb001 收编）。

算法与视频站 `videoweb/backend/app/services/codes.py` 完全一致：
- 规范化：大写、只保留字母数字，去掉 `VW` 前缀
- 哈希：`sha256(f"{pepper}:{normalized}")`，pepper 来自 CODE_HASH_PEPPER
- 密文：Fernet(CODE_ENCRYPTION_KEY) 加密原文 `VW-XXXX-XXXX-XXXX-XXXX`

迁移前后 pepper / key 不得轮换，否则存量码全部失配。
"""

import hashlib
import secrets

from cryptography.fernet import Fernet, InvalidToken

from app.port.config import settings

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def normalize_code(raw_code: str) -> str:
    normalized = "".join(char for char in raw_code.upper() if char.isalnum())
    return normalized[2:] if normalized.startswith("VW") else normalized


def generate_code() -> str:
    body = "".join(secrets.choice(CODE_ALPHABET) for _ in range(16))
    return f"VW-{body[:4]}-{body[4:8]}-{body[8:12]}-{body[12:]}"


def hash_code(raw_code: str) -> str:
    normalized = normalize_code(raw_code)
    return hashlib.sha256(
        f"{settings.CODE_HASH_PEPPER}:{normalized}".encode("utf-8")
    ).hexdigest()


def _fernet() -> Fernet | None:
    key = settings.CODE_ENCRYPTION_KEY
    return Fernet(key.encode("utf-8")) if key else None


def encrypt_code(raw_code: str) -> str:
    encryptor = _fernet()
    return (
        encryptor.encrypt(raw_code.encode("utf-8")).decode("utf-8")
        if encryptor
        else raw_code
    )


def decrypt_code(cipher: str) -> str:
    encryptor = _fernet()
    if not encryptor:
        return cipher
    try:
        return encryptor.decrypt(cipher.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return ""


def code_matches(cipher: str, expected_hash: str) -> bool:
    """自校验：hash(decrypt(cipher)) 是否等于库里的 code_hash。"""
    raw = decrypt_code(cipher)
    return bool(raw) and hash_code(raw) == expected_hash
