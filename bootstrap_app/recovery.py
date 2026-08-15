from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import stat
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable

from Crypto.Cipher import AES, PKCS1_OAEP
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA

from bootstrap_app.installation import InstallationStore
from bootstrap_app.runtime import read_runtime_env


RECOVERY_FORMAT = "wemini-bootstrap-recovery-v1"
RECOVERY_AAD = b"wemini-bootstrap-recovery-v1\x00RSA-OAEP-SHA256\x00AES-256-GCM"
MAX_ENVELOPE_BYTES = 4 * 1024 * 1024
MAX_RECOVERED_FILE_BYTES = 1024 * 1024
SAFE_INSTALLATION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class RecoveryBundleError(RuntimeError):
    """Recovery material could not be safely encrypted, uploaded, or restored."""


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _unb64(value: object, name: str) -> bytes:
    if not isinstance(value, str):
        raise RecoveryBundleError(f"{name} is invalid")
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise RecoveryBundleError(f"{name} is invalid") from exc


def _private_regular_file(path: Path, *, max_bytes: int) -> bytes:
    try:
        info = path.lstat()
    except OSError as exc:
        raise RecoveryBundleError("recovery source file is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RecoveryBundleError("recovery source file is unsafe")
    if info.st_size > max_bytes:
        raise RecoveryBundleError("recovery source file is too large")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise RecoveryBundleError("recovery source file cannot be read") from exc


def _payload_files(installation_dir: Path, control_dir: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {
        "runtime.env": _private_regular_file(
            installation_dir / "runtime.env", max_bytes=MAX_RECOVERED_FILE_BYTES
        ),
        "installation-manifest.json": _private_regular_file(
            installation_dir / "installation-manifest.json",
            max_bytes=MAX_RECOVERED_FILE_BYTES,
        ),
        "recovery_public_key.pem": _private_regular_file(
            installation_dir / "recovery_public_key.pem",
            max_bytes=MAX_RECOVERED_FILE_BYTES,
        ),
    }
    secrets_dir = installation_dir / "secrets"
    try:
        secret_paths = sorted(secrets_dir.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise RecoveryBundleError("secret directory cannot be read") from exc
    if not secret_paths:
        raise RecoveryBundleError("secret directory is empty")
    for path in secret_paths:
        if not path.name or "/" in path.name or path.name.startswith("."):
            raise RecoveryBundleError("secret file name is invalid")
        files[f"secrets/{path.name}"] = _private_regular_file(
            path,
            max_bytes=MAX_RECOVERED_FILE_BYTES,
        )

    release_manifest = control_dir / "release-manifest.json"
    files["release-manifest.json"] = _private_regular_file(
        release_manifest,
        max_bytes=MAX_RECOVERED_FILE_BYTES,
    )
    return files


def create_recovery_envelope(
    *,
    installation_dir: Path,
    control_dir: Path,
    installation_id: str,
    signing_key: bytes,
) -> tuple[bytes, str]:
    # Re-verify the committed installation before reading it into the bundle.
    InstallationStore(installation_dir, signing_key).verify_existing(installation_id)
    public_key_bytes = _private_regular_file(
        installation_dir / "recovery_public_key.pem",
        max_bytes=MAX_RECOVERED_FILE_BYTES,
    )
    try:
        public_key = RSA.import_key(public_key_bytes)
    except (ValueError, IndexError, TypeError) as exc:
        raise RecoveryBundleError("recovery public key is invalid") from exc
    if public_key.has_private() or public_key.size_in_bits() < 3072:
        raise RecoveryBundleError("recovery public key must be public RSA 3072+")

    files = _payload_files(installation_dir, control_dir)
    payload = {
        "format": RECOVERY_FORMAT,
        "installation_id": installation_id,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "files": {name: _b64(content) for name, content in sorted(files.items())},
    }
    plaintext = _canonical_json(payload)
    if len(plaintext) > MAX_ENVELOPE_BYTES:
        raise RecoveryBundleError("recovery payload is too large")
    plaintext_sha256 = hashlib.sha256(plaintext).hexdigest()

    data_key = secrets.token_bytes(32)
    nonce = secrets.token_bytes(12)
    cipher = AES.new(data_key, AES.MODE_GCM, nonce=nonce, mac_len=16)
    cipher.update(RECOVERY_AAD)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext)
    encrypted_key = PKCS1_OAEP.new(public_key, hashAlgo=SHA256).encrypt(data_key)
    envelope = {
        "format": RECOVERY_FORMAT,
        "key_algorithm": "RSA-OAEP-SHA256",
        "data_algorithm": "AES-256-GCM",
        "installation_id": installation_id,
        "plaintext_sha256": plaintext_sha256,
        "encrypted_key": _b64(encrypted_key),
        "nonce": _b64(nonce),
        "tag": _b64(tag),
        "ciphertext": _b64(ciphertext),
    }
    envelope_bytes = _canonical_json(envelope) + b"\n"
    return envelope_bytes, hashlib.sha256(envelope_bytes).hexdigest()


def decrypt_recovery_envelope(envelope_bytes: bytes, private_key_bytes: bytes) -> dict:
    if not envelope_bytes or len(envelope_bytes) > MAX_ENVELOPE_BYTES:
        raise RecoveryBundleError("recovery envelope size is invalid")
    try:
        envelope = json.loads(envelope_bytes)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise RecoveryBundleError("recovery envelope is invalid") from exc
    if not isinstance(envelope, dict) or envelope.get("format") != RECOVERY_FORMAT:
        raise RecoveryBundleError("recovery envelope format is unsupported")
    if envelope.get("key_algorithm") != "RSA-OAEP-SHA256":
        raise RecoveryBundleError("recovery key algorithm is unsupported")
    if envelope.get("data_algorithm") != "AES-256-GCM":
        raise RecoveryBundleError("recovery data algorithm is unsupported")
    try:
        private_key = RSA.import_key(private_key_bytes)
    except (ValueError, IndexError, TypeError) as exc:
        raise RecoveryBundleError("recovery private key is invalid") from exc
    if not private_key.has_private() or private_key.size_in_bits() < 3072:
        raise RecoveryBundleError("recovery private key must be RSA 3072+")
    try:
        data_key = PKCS1_OAEP.new(private_key, hashAlgo=SHA256).decrypt(
            _unb64(envelope.get("encrypted_key"), "encrypted_key")
        )
        cipher = AES.new(
            data_key,
            AES.MODE_GCM,
            nonce=_unb64(envelope.get("nonce"), "nonce"),
            mac_len=16,
        )
        cipher.update(RECOVERY_AAD)
        plaintext = cipher.decrypt_and_verify(
            _unb64(envelope.get("ciphertext"), "ciphertext"),
            _unb64(envelope.get("tag"), "tag"),
        )
    except (ValueError, TypeError) as exc:
        raise RecoveryBundleError("recovery envelope authentication failed") from exc
    expected_sha256 = envelope.get("plaintext_sha256")
    if not isinstance(expected_sha256, str) or not secrets.compare_digest(
        expected_sha256,
        hashlib.sha256(plaintext).hexdigest(),
    ):
        raise RecoveryBundleError("recovery plaintext checksum is invalid")
    try:
        payload = json.loads(plaintext)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise RecoveryBundleError("recovery plaintext is invalid") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("format") != RECOVERY_FORMAT
        or payload.get("installation_id") != envelope.get("installation_id")
        or not isinstance(payload.get("files"), dict)
    ):
        raise RecoveryBundleError("recovery payload is invalid")
    return payload


def restore_recovery_files(
    payload: dict,
    output_dir: Path,
) -> None:
    if output_dir.exists() or output_dir.is_symlink():
        raise RecoveryBundleError("recovery output directory already exists")
    parent = output_dir.parent
    try:
        parent_info = parent.lstat()
    except OSError as exc:
        raise RecoveryBundleError("recovery output parent is unavailable") from exc
    if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
        raise RecoveryBundleError("recovery output parent is unsafe")
    files = payload.get("files")
    if not isinstance(files, dict) or not files:
        raise RecoveryBundleError("recovery payload has no files")

    stage = parent / f".{output_dir.name}.{secrets.token_hex(8)}.tmp"
    created_files: list[Path] = []
    try:
        stage.mkdir(mode=0o700)
        for raw_name, encoded in sorted(files.items()):
            if not isinstance(raw_name, str):
                raise RecoveryBundleError("recovery file name is invalid")
            pure = PurePosixPath(raw_name)
            if (
                pure.is_absolute()
                or not pure.parts
                or any(part in {"", ".", ".."} for part in pure.parts)
                or len(pure.parts) > 2
            ):
                raise RecoveryBundleError("recovery file path is unsafe")
            content = _unb64(encoded, "recovery file")
            if len(content) > MAX_RECOVERED_FILE_BYTES:
                raise RecoveryBundleError("recovery file is too large")
            destination = stage.joinpath(*pure.parts)
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(destination, flags, 0o600)
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(destination, 0o600, follow_symlinks=False)
            created_files.append(destination)
        os.rename(stage, output_dir)
    except BaseException:
        for path in reversed(created_files):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        for directory in sorted(
            (item for item in stage.rglob("*") if item.is_dir()),
            key=lambda item: len(item.parts),
            reverse=True,
        ) if stage.exists() and not stage.is_symlink() else ():
            try:
                directory.rmdir()
            except OSError:
                pass
        try:
            stage.rmdir()
        except OSError:
            pass
        raise


def _recovery_oss_configuration(
    installation_dir: Path,
) -> tuple[dict[str, str], str, str] | None:
    runtime = read_runtime_env(installation_dir / "runtime.env")
    secret_dir = installation_dir / "secrets"
    access_id = _private_regular_file(
        secret_dir / "recovery_oss_access_key_id",
        max_bytes=4096,
    ).decode("utf-8").strip()
    access_secret = _private_regular_file(
        secret_dir / "recovery_oss_access_key_secret",
        max_bytes=4096,
    ).decode("utf-8").strip()
    values = {
        "RECOVERY_OSS_ENDPOINT": runtime.get("RECOVERY_OSS_ENDPOINT", "").strip(),
        "RECOVERY_OSS_BUCKET": runtime.get("RECOVERY_OSS_BUCKET", "").strip(),
        "recovery_oss_access_key_id": access_id,
        "recovery_oss_access_key_secret": access_secret,
    }
    present = {name: bool(value) for name, value in values.items()}
    if not any(present.values()):
        return None
    if not all(present.values()):
        raise RecoveryBundleError("recovery OSS configuration is incomplete")
    prefix = runtime.get("RECOVERY_OSS_PREFIX", "").strip()
    if not prefix:
        raise RecoveryBundleError("recovery OSS prefix is missing")
    return runtime, access_id, access_secret


def recovery_oss_is_configured(installation_dir: Path) -> bool:
    """Return whether the optional recovery OSS group is fully configured."""

    return _recovery_oss_configuration(installation_dir) is not None


def store_local_recovery_envelope(
    *,
    control_dir: Path,
    installation_id: str,
    envelope_bytes: bytes,
    envelope_sha256: str,
) -> str:
    """Persist an encrypted local fallback when remote recovery OSS is disabled."""

    if not SAFE_INSTALLATION_ID_RE.fullmatch(installation_id):
        raise RecoveryBundleError("installation ID is invalid")
    if not re.fullmatch(
        r"[0-9a-f]{64}", envelope_sha256
    ) or not secrets.compare_digest(
        hashlib.sha256(envelope_bytes).hexdigest(),
        envelope_sha256,
    ):
        raise RecoveryBundleError("recovery envelope checksum is invalid")
    try:
        envelope = json.loads(envelope_bytes)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise RecoveryBundleError("recovery envelope is invalid") from exc
    if (
        not isinstance(envelope, dict)
        or envelope.get("installation_id") != installation_id
    ):
        raise RecoveryBundleError("recovery envelope installation mismatch")

    directory = control_dir / "recovery-bundles"
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = directory.lstat()
    except OSError as exc:
        raise RecoveryBundleError("local recovery directory is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise RecoveryBundleError("local recovery directory is unsafe")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise RecoveryBundleError("local recovery directory permissions are unsafe")

    destination = directory / f"{installation_id}.recovery.json"
    temporary = directory / f".{installation_id}.{secrets.token_hex(8)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(envelope_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600, follow_symlinks=False)
        os.replace(temporary, destination)
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise RecoveryBundleError("local recovery envelope cannot be stored") from exc
    return f"local-only:{destination.name}"


def upload_recovery_envelope(
    *,
    installation_dir: Path,
    installation_id: str,
    envelope_bytes: bytes,
    envelope_sha256: str,
    bucket_factory: Callable | None = None,
) -> str:
    configuration = _recovery_oss_configuration(installation_dir)
    if configuration is None:
        raise RecoveryBundleError("recovery OSS is not configured")
    runtime, access_id, access_secret = configuration

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    object_key = (
        f"{runtime['RECOVERY_OSS_PREFIX'].strip('/')}/{installation_id}/"
        f"{timestamp}-{envelope_sha256[:16]}.recovery.json"
    )
    if bucket_factory is None:
        try:
            import oss2
        except ImportError as exc:
            raise RecoveryBundleError("OSS SDK is unavailable") from exc
        auth = oss2.Auth(access_id, access_secret)
        bucket = oss2.Bucket(
            auth,
            runtime["RECOVERY_OSS_ENDPOINT"],
            runtime["RECOVERY_OSS_BUCKET"],
        )
        private_acl = oss2.BUCKET_ACL_PRIVATE
    else:
        bucket = bucket_factory(
            access_id,
            access_secret,
            runtime["RECOVERY_OSS_ENDPOINT"],
            runtime["RECOVERY_OSS_BUCKET"],
        )
        private_acl = "private"
    try:
        acl = bucket.get_bucket_acl().acl
        if acl != private_acl:
            raise RecoveryBundleError("recovery OSS bucket must be private")
        headers = {
            "x-oss-meta-sha256": envelope_sha256,
            "x-oss-meta-installation-id": installation_id,
            "Content-Type": "application/json",
        }
        bucket.put_object(object_key, envelope_bytes, headers=headers)
        metadata = bucket.get_object_meta(object_key)
        normalized_headers = {str(k).lower(): str(v) for k, v in metadata.headers.items()}
        if normalized_headers.get("x-oss-meta-sha256") != envelope_sha256:
            raise RecoveryBundleError("recovery OSS checksum metadata mismatch")
        if normalized_headers.get("x-oss-meta-installation-id") != installation_id:
            raise RecoveryBundleError("recovery OSS installation metadata mismatch")
    except RecoveryBundleError:
        raise
    except Exception as exc:
        raise RecoveryBundleError("recovery OSS upload failed") from exc
    return object_key
