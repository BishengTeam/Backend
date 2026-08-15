from __future__ import annotations

import os
import re

import pytest
from Crypto.PublicKey import RSA
from fastapi.testclient import TestClient

from bootstrap_app.config import BootstrapSettings
from bootstrap_app.main import create_app
from bootstrap_app.probes import ProbeReport


@pytest.fixture(scope="module")
def web_payload():
    merchant = RSA.generate(2048)
    payment = RSA.generate(2048)
    recovery = RSA.generate(3072)
    return {
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
        "wechat_secret": "wechat-secret-never-echo",
        "wechat_pay_mchid": "1113740961",
        "wechat_pay_appid": "wx08157fb5562f4ebe",
        "wechat_pay_cert_serial_no": "1234567890ABCDEF1234567890ABCDEF12345678",
        "wechat_pay_private_key_pem": merchant.export_key().decode(),
        "wechat_pay_api_v3_key": "v" * 32,
        "wechat_pay_public_key_pem": payment.public_key().export_key().decode(),
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
        "recovery_public_key_pem": recovery.public_key().export_key().decode(),
    }


def _client(tmp_path, *, admin_creator=None):
    os.chmod(tmp_path, 0o700)
    control = tmp_path / "control"
    control.mkdir(mode=0o700)
    token = b"bootstrap-token-abcdefghijklmnopqrstuvwxyz"
    token_file = control / "bootstrap_token"
    token_file.write_bytes(token)
    token_file.chmod(0o600)
    settings = BootstrapSettings(
        deploy_root=tmp_path,
        host_deploy_root=tmp_path,
        token_file=token_file,
        control_dir=control,
        installation_dir=tmp_path / "installation",
        token=token,
    )
    async def successful_probes(*_args, **_kwargs):
        return ProbeReport("ok", "ok", "ok", "ok", "ok")

    kwargs = {"probe_runner": successful_probes}
    if admin_creator is not None:
        kwargs["admin_creator"] = admin_creator
    return TestClient(create_app(settings, **kwargs)), token.decode()


def test_health_and_static_page_have_security_headers(tmp_path):
    client, _ = _client(tmp_path)
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "component": "bootstrap", "version": 1}
    redirect = client.get("/", follow_redirects=False)
    assert redirect.status_code == 307
    assert redirect.headers["location"] == "/setup"
    page = client.get("/setup")
    assert page.status_code == 200
    assert "一次性初始化" in page.text
    assert page.headers["x-frame-options"] == "DENY"
    assert page.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in page.headers["content-security-policy"]

    for name in (
        "renshe_oss_endpoint",
        "renshe_oss_bucket",
        "renshe_oss_access_key_id",
        "renshe_oss_access_key_secret",
        "quiz_oss_endpoint",
        "quiz_oss_bucket",
        "quiz_oss_access_key_id",
        "quiz_oss_access_key_secret",
        "recovery_oss_endpoint",
        "recovery_oss_bucket",
        "recovery_oss_access_key_id",
        "recovery_oss_access_key_secret",
    ):
        tag = re.search(rf'<input name="{name}"[^>]*>', page.text)
        assert tag is not None
        assert "required" not in tag.group(0)
    assert 'data-optional-group="renshe"' in page.text
    assert 'data-optional-group="quiz"' in page.text
    assert 'data-optional-group="recovery"' in page.text


def test_api_requires_exact_bearer_token(tmp_path):
    client, token = _client(tmp_path)
    assert client.get("/api/bootstrap/status").status_code == 401
    assert (
        client.get(
            "/api/bootstrap/status",
            headers={"Authorization": "Bearer wrong"},
        ).status_code
        == 401
    )
    response = client.get(
        "/api/bootstrap/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["phase"] == "NEW"


def test_configuration_commits_once_without_echoing_secrets(tmp_path, web_payload):
    client, token = _client(tmp_path)
    headers = {"Authorization": f"Bearer {token}"}
    response = client.post(
        "/api/bootstrap/configure",
        headers=headers,
        json=web_payload,
    )
    assert response.status_code == 200
    assert response.json()["phase"] == "CONFIGURED"
    assert "wechat-secret-never-echo" not in response.text
    assert (tmp_path / "installation" / "secrets" / "wechat_secret").exists()

    repeated = client.post(
        "/api/bootstrap/configure",
        headers=headers,
        json=web_payload,
    )
    assert repeated.status_code == 200
    assert repeated.json()["config_fingerprint"] == response.json()[
        "config_fingerprint"
    ]


def test_configuration_accepts_all_three_oss_groups_as_unconfigured(
    tmp_path,
    web_payload,
):
    client, token = _client(tmp_path)
    payload = dict(web_payload)
    for name in (
        "renshe_oss_endpoint",
        "renshe_oss_bucket",
        "renshe_oss_access_key_id",
        "renshe_oss_access_key_secret",
        "quiz_oss_endpoint",
        "quiz_oss_bucket",
        "quiz_oss_access_key_id",
        "quiz_oss_access_key_secret",
        "recovery_oss_endpoint",
        "recovery_oss_bucket",
        "recovery_oss_access_key_id",
        "recovery_oss_access_key_secret",
    ):
        payload[name] = None

    response = client.post(
        "/api/bootstrap/configure",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )

    assert response.status_code == 200
    runtime = (tmp_path / "installation" / "runtime.env").read_text("utf-8")
    assert "RENSHE_STORAGE_TYPE=disabled" in runtime
    assert "QUIZ_IMPORT_STORAGE_TYPE=disabled" in runtime
    assert (
        tmp_path / "installation" / "secrets" / "aliyun_oss_access_key_id"
    ).read_bytes() == b""


def test_validation_response_never_echoes_rejected_secret(tmp_path, web_payload):
    client, token = _client(tmp_path)
    payload = dict(web_payload)
    leaked = "do-not-echo-this-private-value"
    payload["wechat_pay_api_v3_key"] = leaked
    response = client.post(
        "/api/bootstrap/configure",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    assert response.status_code == 422
    assert leaked not in response.text
    assert payload["wechat_pay_private_key_pem"] not in response.text


def test_request_size_limit_fails_before_parsing(tmp_path):
    client, token = _client(tmp_path)
    response = client.post(
        "/api/bootstrap/configure",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Length": str(3 * 1024 * 1024),
            "Content-Type": "application/json",
        },
        content=b"{}",
    )
    assert response.status_code == 413


def test_admin_password_is_used_once_and_never_echoed(tmp_path):
    captured = {}

    async def admin_creator(_installation_dir, request):
        captured["username"] = request.username
        captured["password"] = request.password.get_secret_value()
        return 7

    client, token = _client(tmp_path, admin_creator=admin_creator)
    store = client.app.state.bootstrap_state_store
    state = store.load()
    phases = list(store.load().phase.__class__)
    awaiting_index = phases.index(state.phase.__class__.AWAITING_ADMIN)
    for target in phases[1 : awaiting_index + 1]:
        state = store.transition(state.phase, target)

    password = "a-final-admin-password-2026"
    response = client.post(
        "/api/bootstrap/admin",
        headers={"Authorization": f"Bearer {token}"},
        json={"username": "root.admin", "password": password},
    )
    assert response.status_code == 200
    assert response.json()["phase"] == "ADMIN_CREATED"
    assert password not in response.text
    assert captured == {"username": "root.admin", "password": password}


def test_production_seed_has_no_test_or_force_path():
    source = (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "scripts"
        / "seed_production.py"
    ).read_text(encoding="utf-8")
    assert "seed_testdata" not in source
    assert "--force" not in source
    assert "PRODUCTION_SEED_VERSION" in source
