from __future__ import annotations

import base64
import json
import os

import pytest
from Crypto.PublicKey import RSA

from bootstrap_app.installation import InstallationStore
from bootstrap_app.recovery import (
    RecoveryBundleError,
    create_recovery_envelope,
    decrypt_recovery_envelope,
    recovery_oss_is_configured,
    restore_recovery_files,
    store_local_recovery_envelope,
    upload_recovery_envelope,
)


def _installation(tmp_path, recovery_key, *, recovery_oss=True):
    os.chmod(tmp_path, 0o700)
    installation_dir = tmp_path / "installation"
    control_dir = tmp_path / "control"
    control_dir.mkdir(mode=0o700)
    (control_dir / "release-manifest.json").write_text(
        '{"backend_commit":"abc","admin_commit":"def"}\n',
        encoding="utf-8",
    )
    (control_dir / "release-manifest.json").chmod(0o600)
    runtime = {
        "RECOVERY_OSS_ENDPOINT": (
            "https://oss-cn-shanghai.aliyuncs.com" if recovery_oss else ""
        ),
        "RECOVERY_OSS_BUCKET": "recovery-private" if recovery_oss else "",
        "RECOVERY_OSS_PREFIX": "wemini-recovery",
    }
    secrets_payload = {
        "jwt_secret": b"j" * 64,
        "pii_hash_key": b"p" * 64,
        "recovery_oss_access_key_id": b"recovery-id" if recovery_oss else b"",
        "recovery_oss_access_key_secret": (
            b"recovery-secret" if recovery_oss else b""
        ),
    }
    signing_key = b"s" * 64
    store = InstallationStore(installation_dir, signing_key)
    store.commit(
        installation_id="installation-123",
        secret_files=secrets_payload,
        runtime=runtime,
        recovery_public_key=recovery_key.public_key().export_key(),
    )
    return installation_dir, control_dir, signing_key


def test_recovery_round_trip_and_private_restore_permissions(tmp_path):
    recovery_key = RSA.generate(3072)
    installation_dir, control_dir, signing_key = _installation(tmp_path, recovery_key)
    envelope, digest = create_recovery_envelope(
        installation_dir=installation_dir,
        control_dir=control_dir,
        installation_id="installation-123",
        signing_key=signing_key,
    )
    assert len(digest) == 64
    assert b"recovery-secret" not in envelope
    payload = decrypt_recovery_envelope(envelope, recovery_key.export_key())
    assert payload["installation_id"] == "installation-123"
    assert base64.b64decode(payload["files"]["secrets/pii_hash_key"]) == b"p" * 64

    output = tmp_path / "restored"
    restore_recovery_files(payload, output)
    assert (output / "secrets" / "jwt_secret").read_bytes() == b"j" * 64
    assert (os.stat(output).st_mode & 0o777) == 0o700
    assert (os.stat(output / "secrets" / "jwt_secret").st_mode & 0o777) == 0o600
    with pytest.raises(RecoveryBundleError, match="already exists"):
        restore_recovery_files(payload, output)


def test_recovery_wrong_key_and_ciphertext_tamper_fail(tmp_path):
    recovery_key = RSA.generate(3072)
    installation_dir, control_dir, signing_key = _installation(tmp_path, recovery_key)
    envelope, _ = create_recovery_envelope(
        installation_dir=installation_dir,
        control_dir=control_dir,
        installation_id="installation-123",
        signing_key=signing_key,
    )
    with pytest.raises(RecoveryBundleError, match="authentication failed"):
        decrypt_recovery_envelope(envelope, RSA.generate(3072).export_key())

    parsed = json.loads(envelope)
    ciphertext = bytearray(base64.b64decode(parsed["ciphertext"]))
    ciphertext[len(ciphertext) // 2] ^= 1
    parsed["ciphertext"] = base64.b64encode(ciphertext).decode()
    tampered = json.dumps(parsed, separators=(",", ":"), sort_keys=True).encode()
    with pytest.raises(RecoveryBundleError, match="authentication failed"):
        decrypt_recovery_envelope(tampered, recovery_key.export_key())


class _ACL:
    def __init__(self, acl):
        self.acl = acl


class _Meta:
    def __init__(self, headers):
        self.headers = headers


class _FakeBucket:
    def __init__(self, *, acl="private", corrupt_metadata=False):
        self.acl = acl
        self.corrupt_metadata = corrupt_metadata
        self.objects = {}
        self.metadata = {}

    def get_bucket_acl(self):
        return _ACL(self.acl)

    def put_object(self, key, body, headers):
        self.objects[key] = body
        self.metadata[key] = dict(headers)

    def get_object_meta(self, key):
        headers = dict(self.metadata[key])
        if self.corrupt_metadata:
            headers["x-oss-meta-sha256"] = "bad"
        return _Meta(headers)


def test_recovery_upload_requires_private_bucket_and_verifies_metadata(tmp_path):
    recovery_key = RSA.generate(3072)
    installation_dir, control_dir, signing_key = _installation(tmp_path, recovery_key)
    envelope, digest = create_recovery_envelope(
        installation_dir=installation_dir,
        control_dir=control_dir,
        installation_id="installation-123",
        signing_key=signing_key,
    )
    bucket = _FakeBucket()
    captured = {}

    def factory(access_id, access_secret, endpoint, bucket_name):
        captured.update(
            access_id=access_id,
            access_secret=access_secret,
            endpoint=endpoint,
            bucket_name=bucket_name,
        )
        return bucket

    object_key = upload_recovery_envelope(
        installation_dir=installation_dir,
        installation_id="installation-123",
        envelope_bytes=envelope,
        envelope_sha256=digest,
        bucket_factory=factory,
    )
    assert object_key.startswith("wemini-recovery/installation-123/")
    assert bucket.objects[object_key] == envelope
    assert captured["access_secret"] == "recovery-secret"
    assert bucket.metadata[object_key]["x-oss-meta-sha256"] == digest

    with pytest.raises(RecoveryBundleError, match="must be private"):
        upload_recovery_envelope(
            installation_dir=installation_dir,
            installation_id="installation-123",
            envelope_bytes=envelope,
            envelope_sha256=digest,
            bucket_factory=lambda *_: _FakeBucket(acl="public-read"),
        )
    with pytest.raises(RecoveryBundleError, match="checksum metadata"):
        upload_recovery_envelope(
            installation_dir=installation_dir,
            installation_id="installation-123",
            envelope_bytes=envelope,
            envelope_sha256=digest,
            bucket_factory=lambda *_: _FakeBucket(corrupt_metadata=True),
        )


def test_recovery_oss_can_be_omitted_and_encrypted_bundle_stays_local(tmp_path):
    recovery_key = RSA.generate(3072)
    installation_dir, control_dir, signing_key = _installation(
        tmp_path,
        recovery_key,
        recovery_oss=False,
    )
    envelope, digest = create_recovery_envelope(
        installation_dir=installation_dir,
        control_dir=control_dir,
        installation_id="installation-123",
        signing_key=signing_key,
    )

    assert recovery_oss_is_configured(installation_dir) is False
    local_key = store_local_recovery_envelope(
        control_dir=control_dir,
        installation_id="installation-123",
        envelope_bytes=envelope,
        envelope_sha256=digest,
    )

    assert local_key == "local-only:installation-123.recovery.json"
    local_path = control_dir / "recovery-bundles" / "installation-123.recovery.json"
    assert local_path.read_bytes() == envelope
    assert (os.stat(local_path).st_mode & 0o777) == 0o600
    with pytest.raises(RecoveryBundleError, match="not configured"):
        upload_recovery_envelope(
            installation_dir=installation_dir,
            installation_id="installation-123",
            envelope_bytes=envelope,
            envelope_sha256=digest,
        )
