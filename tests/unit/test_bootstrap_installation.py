from __future__ import annotations

import json
import os

import pytest
from Crypto.PublicKey import RSA

from bootstrap_app.installation import (
    BootstrapValidationError,
    InstallationCommitError,
    InstallationStore,
    build_installation_payload,
)
from bootstrap_app.models import BootstrapConfigureRequest


@pytest.fixture(scope="module")
def rsa_materials():
    merchant = RSA.generate(2048)
    payment = RSA.generate(2048)
    recovery = RSA.generate(3072)
    return {
        "merchant_private": merchant.export_key().decode(),
        "payment_public": payment.public_key().export_key().decode(),
        "recovery_public": recovery.public_key().export_key().decode(),
    }


def configure_payload(rsa_materials, **overrides):
    payload = {
        "deployment_mode": "internal",
        "api_origin": "https://api.example.com",
        "admin_origin": "https://admin.example.com",
        "postgres_host": "db",
        "postgres_port": 5432,
        "postgres_user": "wemini",
        "postgres_database": "wemini_app",
        "postgres_password": None,
        "redis_url": None,
        "wechat_appid": "wx08157fb5562f4ebe",
        "wechat_secret": "wechat-secret",
        "wechat_pay_mchid": "1113740961",
        "wechat_pay_appid": "wx08157fb5562f4ebe",
        "wechat_pay_cert_serial_no": "1234567890ABCDEF1234567890ABCDEF12345678",
        "wechat_pay_private_key_pem": rsa_materials["merchant_private"],
        "wechat_pay_api_v3_key": "a" * 32,
        "wechat_pay_public_key_pem": rsa_materials["payment_public"],
        "wechat_pay_public_key_id": "PUB_KEY_ID_0111",
        "renshe_oss_endpoint": "https://oss-cn-hangzhou.aliyuncs.com",
        "renshe_oss_bucket": "renshe-private",
        "renshe_oss_access_key_id": "renshe-id",
        "renshe_oss_access_key_secret": "renshe-secret",
        "quiz_oss_endpoint": "https://oss-cn-hangzhou.aliyuncs.com",
        "quiz_oss_bucket": "quiz-private",
        "quiz_oss_access_key_id": "quiz-id",
        "quiz_oss_access_key_secret": "quiz-secret",
        "recovery_oss_endpoint": "https://oss-cn-shanghai.aliyuncs.com",
        "recovery_oss_bucket": "recovery-private",
        "recovery_oss_prefix": "wemini-recovery",
        "recovery_oss_access_key_id": "recovery-id",
        "recovery_oss_access_key_secret": "recovery-secret",
        "recovery_public_key_pem": rsa_materials["recovery_public"],
    }
    payload.update(overrides)
    return payload


def test_build_internal_payload_generates_separate_secrets(rsa_materials, tmp_path):
    request = BootstrapConfigureRequest.model_validate(configure_payload(rsa_materials))
    first, runtime, recovery_key = build_installation_payload(
        request,
        host_deploy_root=tmp_path,
    )
    second, _, _ = build_installation_payload(request, host_deploy_root=tmp_path)

    assert first["jwt_secret"] != first["pii_hash_key"]
    assert first["jwt_secret"] != second["jwt_secret"]
    assert len(first["wechat_pay_api_v3_key"]) == 32
    assert runtime["RUN_MIGRATIONS"] == "false"
    assert runtime["DB_HOST"] == "db"
    assert runtime["WECHAT_PAY_NOTIFY_URL"].startswith("https://api.example.com/")
    assert "wechat-secret" not in json.dumps(runtime)
    assert RSA.import_key(recovery_key).has_private() is False


def test_external_mode_keeps_credentials_out_of_runtime(rsa_materials, tmp_path):
    request = BootstrapConfigureRequest.model_validate(
        configure_payload(
            rsa_materials,
            deployment_mode="external",
            postgres_host="postgres.example.internal",
            postgres_port=3306,
            postgres_password="db-password",
            redis_url="rediss://:redis-password@redis.example.internal:6380/4",
        )
    )
    secret_files, runtime, _ = build_installation_payload(
        request,
        host_deploy_root=tmp_path,
    )
    assert runtime["DB_PORT"] == "3306"
    assert runtime["REDIS_URL"] == ""
    assert secret_files["postgres_password"] == b"db-password"
    assert secret_files["redis_url"].startswith(b"rediss://")
    assert "db-password" not in json.dumps(runtime)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("wechat_pay_api_v3_key", "short", "32 bytes"),
        ("wechat_pay_public_key_id", "not-an-id", "PUB_KEY_ID_"),
        ("wechat_secret", "bad\nsecret", "single-line"),
    ],
)
def test_offline_validation_rejects_unsafe_payment_input(
    rsa_materials,
    tmp_path,
    field,
    value,
    expected,
):
    request = BootstrapConfigureRequest.model_validate(
        configure_payload(rsa_materials, **{field: value})
    )
    with pytest.raises(BootstrapValidationError, match=expected):
        build_installation_payload(request, host_deploy_root=tmp_path)


def test_installation_directory_is_atomic_private_and_verifiable(
    rsa_materials,
    tmp_path,
):
    os.chmod(tmp_path, 0o700)
    request = BootstrapConfigureRequest.model_validate(configure_payload(rsa_materials))
    secret_files, runtime, recovery_key = build_installation_payload(
        request,
        host_deploy_root=tmp_path,
    )
    installation_dir = tmp_path / "installation"
    store = InstallationStore(installation_dir, b"k" * 64)
    fingerprint = store.commit(
        installation_id="install-1",
        secret_files=secret_files,
        runtime=runtime,
        recovery_public_key=recovery_key,
    )

    assert store.verify_existing("install-1") == fingerprint
    assert (os.stat(installation_dir).st_mode & 0o777) == 0o700
    assert (os.stat(installation_dir / "secrets").st_mode & 0o777) == 0o700
    for path in (installation_dir / "secrets").iterdir():
        assert (os.stat(path).st_mode & 0o777) == 0o600
    runtime_text = (installation_dir / "runtime.env").read_text(encoding="utf-8")
    assert "wechat-secret" not in runtime_text
    assert "JWT_SECRET=" not in runtime_text

    with pytest.raises(InstallationCommitError, match="already exists"):
        store.commit(
            installation_id="install-1",
            secret_files=secret_files,
            runtime=runtime,
            recovery_public_key=recovery_key,
        )


def test_installation_tamper_and_symlink_are_rejected(rsa_materials, tmp_path):
    os.chmod(tmp_path, 0o700)
    request = BootstrapConfigureRequest.model_validate(configure_payload(rsa_materials))
    secret_files, runtime, recovery_key = build_installation_payload(
        request,
        host_deploy_root=tmp_path,
    )
    store = InstallationStore(tmp_path / "installation", b"k" * 64)
    store.commit(
        installation_id="install-2",
        secret_files=secret_files,
        runtime=runtime,
        recovery_public_key=recovery_key,
    )
    secret_path = tmp_path / "installation" / "secrets" / "wechat_secret"
    secret_path.write_text("tampered", encoding="utf-8")
    with pytest.raises(InstallationCommitError, match="integrity"):
        store.verify_existing("install-2")

    secret_path.unlink()
    secret_path.symlink_to("/etc/passwd")
    with pytest.raises(InstallationCommitError, match="file set"):
        store.verify_existing("install-2")
