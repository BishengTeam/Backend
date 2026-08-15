from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import stat
from pathlib import Path
from typing import Mapping

from Crypto.PublicKey import RSA

from bootstrap_app.models import BootstrapConfigureRequest


SECRET_FILE_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
RUNTIME_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
WECHAT_APPID_RE = re.compile(r"^wx[A-Za-z0-9]{16}$")
MCHID_RE = re.compile(r"^[0-9]{8,16}$")
SERIAL_RE = re.compile(r"^[A-Fa-f0-9]{16,64}$")
OPTIONAL_EMPTY_SECRET_FILES = frozenset(
    {
        "aliyun_oss_access_key_id",
        "aliyun_oss_access_key_secret",
        "quiz_oss_access_key_id",
        "quiz_oss_access_key_secret",
        "recovery_oss_access_key_id",
        "recovery_oss_access_key_secret",
    }
)


class BootstrapValidationError(ValueError):
    """A submitted deployment value failed a safe offline validation."""


class InstallationCommitError(RuntimeError):
    """The atomic installation directory could not be committed safely."""


def _secret_value(value) -> str:
    return value.get_secret_value()


def _optional_secret_value(value) -> str:
    return value.get_secret_value() if value is not None else ""


def _validate_rsa_key(
    material: str,
    *,
    name: str,
    bits: int,
    require_private: bool,
) -> bytes:
    normalized = material.strip().replace("\\n", "\n")
    if len(normalized.encode("utf-8")) > 64 * 1024:
        raise BootstrapValidationError(f"{name} is too large")
    try:
        key = RSA.import_key(normalized.encode("utf-8"))
    except (ValueError, IndexError, TypeError) as exc:
        raise BootstrapValidationError(f"{name} is not a valid RSA PEM") from exc
    if require_private and not key.has_private():
        raise BootstrapValidationError(f"{name} must contain a private key")
    if not require_private and key.has_private():
        raise BootstrapValidationError(f"{name} must not contain a private key")
    if key.size_in_bits() < bits:
        raise BootstrapValidationError(f"{name} must be at least RSA {bits} bits")
    return key.export_key(format="PEM", passphrase=None, pkcs=8)


def _random_secret(byte_count: int) -> str:
    return secrets.token_urlsafe(byte_count)


def build_installation_payload(
    request: BootstrapConfigureRequest,
    *,
    host_deploy_root: Path,
) -> tuple[dict[str, bytes], dict[str, str], bytes]:
    """Validate input and return secret files, runtime config and recovery key."""

    if not WECHAT_APPID_RE.fullmatch(request.wechat_appid):
        raise BootstrapValidationError("wechat_appid is invalid")
    if not WECHAT_APPID_RE.fullmatch(request.wechat_pay_appid):
        raise BootstrapValidationError("wechat_pay_appid is invalid")
    if request.wechat_appid != request.wechat_pay_appid:
        raise BootstrapValidationError("WeChat login and payment AppID must match")
    if not MCHID_RE.fullmatch(request.wechat_pay_mchid):
        raise BootstrapValidationError("wechat_pay_mchid is invalid")
    if not SERIAL_RE.fullmatch(request.wechat_pay_cert_serial_no):
        raise BootstrapValidationError("wechat_pay_cert_serial_no is invalid")
    if not request.wechat_pay_public_key_id.startswith("PUB_KEY_ID_"):
        raise BootstrapValidationError(
            "wechat_pay_public_key_id must start with PUB_KEY_ID_"
        )

    merchant_private_key = _validate_rsa_key(
        _secret_value(request.wechat_pay_private_key_pem),
        name="wechat_pay_private_key_pem",
        bits=2048,
        require_private=True,
    )
    if RSA.import_key(merchant_private_key).size_in_bits() != 2048:
        raise BootstrapValidationError("WeChat Pay merchant private key must be RSA 2048")
    payment_public_key = _validate_rsa_key(
        _secret_value(request.wechat_pay_public_key_pem),
        name="wechat_pay_public_key_pem",
        bits=2048,
        require_private=False,
    )
    if RSA.import_key(payment_public_key).size_in_bits() != 2048:
        raise BootstrapValidationError("WeChat Pay public key must be RSA 2048")
    recovery_public_key = _validate_rsa_key(
        _secret_value(request.recovery_public_key_pem),
        name="recovery_public_key_pem",
        bits=3072,
        require_private=False,
    )

    api_v3_key = _secret_value(request.wechat_pay_api_v3_key)
    if len(api_v3_key.encode("utf-8")) != 32:
        raise BootstrapValidationError("wechat_pay_api_v3_key must be exactly 32 bytes")

    scalar_secret_fields = {
        "wechat_secret": _secret_value(request.wechat_secret),
        "aliyun_oss_access_key_id": _optional_secret_value(
            request.renshe_oss_access_key_id
        ),
        "aliyun_oss_access_key_secret": _optional_secret_value(
            request.renshe_oss_access_key_secret
        ),
        "quiz_oss_access_key_id": _optional_secret_value(
            request.quiz_oss_access_key_id
        ),
        "quiz_oss_access_key_secret": _optional_secret_value(
            request.quiz_oss_access_key_secret
        ),
        "recovery_oss_access_key_id": _optional_secret_value(
            request.recovery_oss_access_key_id
        ),
        "recovery_oss_access_key_secret": _optional_secret_value(
            request.recovery_oss_access_key_secret
        ),
    }
    for name, value in scalar_secret_fields.items():
        if name in OPTIONAL_EMPTY_SECRET_FILES and not value:
            continue
        if not value or "\x00" in value or "\r" in value or "\n" in value:
            raise BootstrapValidationError(f"{name} must be a non-empty single-line value")
        if len(value.encode("utf-8")) > 4096:
            raise BootstrapValidationError(f"{name} is too large")

    if request.deployment_mode == "internal":
        postgres_password = _random_secret(32)
        redis_url = "redis://redis:6379/0"
        database_host = "db"
        database_port = 5432
    else:
        assert request.postgres_password is not None
        assert request.redis_url is not None
        postgres_password = _secret_value(request.postgres_password)
        redis_url = _secret_value(request.redis_url)
        database_host = request.postgres_host
        database_port = request.postgres_port
    for name, value in {
        "postgres_password": postgres_password,
        "redis_url": redis_url,
    }.items():
        if not value or "\x00" in value or "\r" in value or "\n" in value:
            raise BootstrapValidationError(f"{name} contains unsupported characters")

    secrets_payload: dict[str, bytes] = {
        "postgres_password": postgres_password.encode("utf-8"),
        "jwt_secret": _random_secret(64).encode("ascii"),
        "pii_hash_key": _random_secret(64).encode("ascii"),
        "quiz_metrics_bearer_token": _random_secret(48).encode("ascii"),
        "redis_url": redis_url.encode("utf-8"),
        "wechat_secret": scalar_secret_fields["wechat_secret"].encode("utf-8"),
        "wechat_pay_private_key": merchant_private_key,
        "wechat_pay_api_v3_key": api_v3_key.encode("utf-8"),
        "wechat_pay_public_key": payment_public_key,
        "aliyun_oss_access_key_id": scalar_secret_fields[
            "aliyun_oss_access_key_id"
        ].encode("utf-8"),
        "aliyun_oss_access_key_secret": scalar_secret_fields[
            "aliyun_oss_access_key_secret"
        ].encode("utf-8"),
        "quiz_oss_access_key_id": scalar_secret_fields[
            "quiz_oss_access_key_id"
        ].encode("utf-8"),
        "quiz_oss_access_key_secret": scalar_secret_fields[
            "quiz_oss_access_key_secret"
        ].encode("utf-8"),
        "recovery_oss_access_key_id": scalar_secret_fields[
            "recovery_oss_access_key_id"
        ].encode("utf-8"),
        "recovery_oss_access_key_secret": scalar_secret_fields[
            "recovery_oss_access_key_secret"
        ].encode("utf-8"),
    }
    secrets_dir = host_deploy_root / "installation" / "secrets"
    runtime = {
        "DEPLOYMENT_MODE": request.deployment_mode,
        "SECRETS_DIR": str(secrets_dir),
        "APP_ENV": "production",
        "APP_DEBUG": "false",
        "RUN_MIGRATIONS": "false",
        "DB_HOST": database_host,
        "DB_PORT": str(database_port),
        "DB_USER": request.postgres_user,
        "DB_NAME": request.postgres_database,
        "POSTGRES_USER": request.postgres_user,
        "POSTGRES_DB": request.postgres_database,
        # The URL always travels through the read-only redis_url Secret file.
        "REDIS_URL": "",
        "WECHAT_APPID": request.wechat_appid,
        "WECHAT_PAY_ENABLED": "true",
        "WECHAT_PAY_API_VERSION": "v3",
        "WECHAT_PAY_MCHID": request.wechat_pay_mchid,
        "WECHAT_PAY_APPID": request.wechat_pay_appid,
        "WECHAT_PAY_NOTIFY_URL": f"{request.api_origin}/api/payment/callback",
        "WECHAT_PAY_REFUND_NOTIFY_URL": (
            f"{request.api_origin}/api/payment/refund-callback"
        ),
        "WECHAT_PAY_CERT_SERIAL_NO": request.wechat_pay_cert_serial_no,
        "WECHAT_PAY_PUBLIC_KEY_ID": request.wechat_pay_public_key_id,
        "RENSHE_STORAGE_TYPE": (
            "aliyun_oss" if request.has_renshe_oss() else "disabled"
        ),
        "ALIYUN_OSS_ENDPOINT": request.renshe_oss_endpoint or "",
        "ALIYUN_OSS_BUCKET": request.renshe_oss_bucket or "",
        "QUIZ_IMPORT_STORAGE_TYPE": (
            "aliyun_oss" if request.has_quiz_oss() else "disabled"
        ),
        "QUIZ_OSS_ENDPOINT": request.quiz_oss_endpoint or "",
        "QUIZ_OSS_BUCKET": request.quiz_oss_bucket or "",
        "RECOVERY_OSS_ENDPOINT": request.recovery_oss_endpoint or "",
        "RECOVERY_OSS_BUCKET": request.recovery_oss_bucket or "",
        "RECOVERY_OSS_PREFIX": request.recovery_oss_prefix,
        "API_ORIGIN": request.api_origin,
        "ADMIN_ORIGIN": request.admin_origin,
        "CORS_ORIGINS": json.dumps([request.admin_origin], separators=(",", ":")),
    }
    for key, value in runtime.items():
        if not RUNTIME_KEY_RE.fullmatch(key):
            raise BootstrapValidationError("runtime configuration key is invalid")
        if "\x00" in value or "\r" in value or "\n" in value:
            raise BootstrapValidationError(f"runtime configuration {key} is invalid")
    return secrets_payload, runtime, recovery_public_key


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _write_private_file(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb", closefd=True) as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(path, 0o600, follow_symlinks=False)


class InstallationStore:
    def __init__(self, installation_dir: Path, signing_key: bytes) -> None:
        self.installation_dir = installation_dir
        self.signing_key = signing_key

    def _signature(self, payload: object) -> str:
        return hmac.new(
            self.signing_key,
            _canonical_json(payload),
            hashlib.sha256,
        ).hexdigest()

    def commit(
        self,
        *,
        installation_id: str,
        secret_files: Mapping[str, bytes],
        runtime: Mapping[str, str],
        recovery_public_key: bytes,
    ) -> str:
        if self.installation_dir.exists() or self.installation_dir.is_symlink():
            raise InstallationCommitError("installation directory already exists")
        parent = self.installation_dir.parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent_info = parent.lstat()
        if stat.S_ISLNK(parent_info.st_mode) or stat.S_IMODE(parent_info.st_mode) & 0o077:
            raise InstallationCommitError("deployment root must be a private directory")

        stage = parent / f".installation-{secrets.token_hex(8)}.tmp"
        try:
            stage.mkdir(mode=0o700)
            secrets_dir = stage / "secrets"
            secrets_dir.mkdir(mode=0o700)
            secret_macs: dict[str, str] = {}
            for name in sorted(secret_files):
                if not SECRET_FILE_RE.fullmatch(name):
                    raise InstallationCommitError("invalid secret file name")
                content = secret_files[name]
                if not content and name not in OPTIONAL_EMPTY_SECRET_FILES:
                    raise InstallationCommitError("secret files cannot be empty")
                _write_private_file(secrets_dir / name, content)
                secret_macs[name] = hmac.new(
                    self.signing_key, content, hashlib.sha256
                ).hexdigest()

            runtime_lines = []
            for key in sorted(runtime):
                value = runtime[key]
                if not RUNTIME_KEY_RE.fullmatch(key):
                    raise InstallationCommitError("invalid runtime key")
                if "\x00" in value or "\r" in value or "\n" in value:
                    raise InstallationCommitError("invalid runtime value")
                runtime_lines.append(f"{key}={value}")
            runtime_bytes = ("\n".join(runtime_lines) + "\n").encode("utf-8")
            _write_private_file(stage / "runtime.env", runtime_bytes)
            _write_private_file(stage / "recovery_public_key.pem", recovery_public_key)

            payload = {
                "version": 1,
                "installation_id": installation_id,
                "runtime_sha256": hashlib.sha256(runtime_bytes).hexdigest(),
                "recovery_public_key_sha256": hashlib.sha256(
                    recovery_public_key
                ).hexdigest(),
                "secret_macs": secret_macs,
            }
            signature = self._signature(payload)
            envelope = {"payload": payload, "signature": signature}
            _write_private_file(
                stage / "installation-manifest.json",
                _canonical_json(envelope) + b"\n",
            )

            for directory in (secrets_dir, stage):
                descriptor = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            os.rename(stage, self.installation_dir)
            parent_descriptor = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(parent_descriptor)
            finally:
                os.close(parent_descriptor)
            return signature
        except BaseException:
            if stage.exists() and not stage.is_symlink():
                shutil.rmtree(stage, ignore_errors=True)
            raise

    def verify_existing(self, installation_id: str) -> str:
        manifest_path = self.installation_dir / "installation-manifest.json"
        try:
            info = manifest_path.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise InstallationCommitError("installation manifest is unsafe")
            envelope = json.loads(manifest_path.read_bytes())
            payload = envelope["payload"]
            signature = envelope["signature"]
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise InstallationCommitError("installation manifest cannot be read") from exc
        if not isinstance(payload, dict) or not isinstance(signature, str):
            raise InstallationCommitError("installation manifest is invalid")
        if not hmac.compare_digest(signature, self._signature(payload)):
            raise InstallationCommitError("installation manifest signature is invalid")
        if payload.get("installation_id") != installation_id:
            raise InstallationCommitError("installation ID does not match")

        runtime_path = self.installation_dir / "runtime.env"
        try:
            runtime_bytes = runtime_path.read_bytes()
        except OSError as exc:
            raise InstallationCommitError("runtime configuration cannot be read") from exc
        runtime_sha256 = hashlib.sha256(runtime_bytes).hexdigest()
        if not hmac.compare_digest(
            str(payload.get("runtime_sha256", "")), runtime_sha256
        ):
            raise InstallationCommitError("runtime configuration checksum is invalid")
        expected_secret_macs = payload.get("secret_macs")
        if not isinstance(expected_secret_macs, dict) or not expected_secret_macs:
            raise InstallationCommitError("secret manifest is invalid")
        secrets_dir = self.installation_dir / "secrets"
        try:
            actual_names = {
                item.name
                for item in secrets_dir.iterdir()
                if item.is_file() and not item.is_symlink()
            }
        except OSError as exc:
            raise InstallationCommitError("secret directory cannot be read") from exc
        if actual_names != set(expected_secret_macs):
            raise InstallationCommitError("secret file set does not match manifest")
        for name, expected_mac in expected_secret_macs.items():
            if not SECRET_FILE_RE.fullmatch(str(name)) or not isinstance(expected_mac, str):
                raise InstallationCommitError("secret manifest entry is invalid")
            path = secrets_dir / str(name)
            try:
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                    raise InstallationCommitError("secret file is unsafe")
                if stat.S_IMODE(info.st_mode) & 0o077:
                    raise InstallationCommitError("secret file permissions are unsafe")
                actual_mac = hmac.new(
                    self.signing_key, path.read_bytes(), hashlib.sha256
                ).hexdigest()
            except OSError as exc:
                raise InstallationCommitError("secret file cannot be read") from exc
            if not hmac.compare_digest(expected_mac, actual_mac):
                raise InstallationCommitError("secret file integrity check failed")
        return signature
