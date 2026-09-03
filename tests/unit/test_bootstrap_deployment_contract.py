from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="部署契约脚本内部执行 bash -n，需要 bash（部署目标为 Linux）",
)
def test_bootstrap_deployment_static_contract():
    subprocess.run(
        [sys.executable, "scripts/check_bootstrap_deployment.py"],
        cwd=ROOT,
        check=True,
    )


def test_bootstrap_and_runtime_compose_parse_when_required_values_are_supplied(tmp_path):
    if shutil.which("docker") is None or subprocess.run(
        ["docker", "compose", "version"],
        capture_output=True,
        check=False,
    ).returncode != 0:
        return
    bootstrap_env = os.environ | {
        "BOOTSTRAP_UID": str(os.getuid()),
        "BOOTSTRAP_GID": str(os.getgid()),
        "BOOTSTRAP_HOST_DEPLOY_ROOT": str(tmp_path),
    }
    subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "docker-compose.bootstrap.yml",
            "config",
            "--quiet",
        ],
        cwd=ROOT,
        env=bootstrap_env,
        check=True,
    )
    subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "docker-compose.bootstrap.release.yml",
            "config",
            "--quiet",
        ],
        cwd=ROOT,
        env=bootstrap_env
        | {"BOOTSTRAP_IMAGE": "wemini-backend:" + "a" * 40},
        check=True,
    )

    runtime_env = os.environ | {
        "BOOTSTRAP_UID": str(os.getuid()),
        "BOOTSTRAP_GID": str(os.getgid()),
        "BACKEND_IMAGE": "wemini-backend:test",
        "ADMIN_IMAGE": "wemini-admin:test",
        "DB_HOST": "db",
        "DB_PORT": "5432",
        "DB_USER": "wemini",
        "DB_NAME": "wemini_app",
        "POSTGRES_USER": "wemini",
        "POSTGRES_DB": "wemini_app",
        "CORS_ORIGINS": '["https://admin.example.com"]',
        "WECHAT_APPID": "wx08157fb5562f4ebe",
        "WECHAT_PAY_MCHID": "1113740961",
        "WECHAT_PAY_APPID": "wx08157fb5562f4ebe",
        "WECHAT_PAY_NOTIFY_URL": "https://api.example.com/api/payment/callback",
        "WECHAT_PAY_REFUND_NOTIFY_URL": "https://api.example.com/api/payment/refund-callback",
        "WECHAT_PAY_CERT_SERIAL_NO": "1234567890ABCDEF",
        "WECHAT_PAY_PUBLIC_KEY_ID": "PUB_KEY_ID_TEST",
        "ALIYUN_OSS_ENDPOINT": "https://oss.example.com",
        "ALIYUN_OSS_BUCKET": "renshe-private",
        "QUIZ_OSS_ENDPOINT": "https://oss.example.com",
        "QUIZ_OSS_BUCKET": "quiz-private",
        "SECRETS_DIR": str(tmp_path / "secrets"),
        "RENSHE_TEMPLATE_HOST_DIR": str(tmp_path / "templates"),
    }
    subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "docker-compose.deploy.yml",
            "config",
            "--quiet",
        ],
        cwd=ROOT,
        env=runtime_env,
        check=True,
    )

    runtime_without_oss = dict(runtime_env)
    for name in (
        "ALIYUN_OSS_ENDPOINT",
        "ALIYUN_OSS_BUCKET",
        "QUIZ_OSS_ENDPOINT",
        "QUIZ_OSS_BUCKET",
    ):
        runtime_without_oss.pop(name, None)
    runtime_without_oss.update(
        {
            "RENSHE_STORAGE_TYPE": "disabled",
            "QUIZ_IMPORT_STORAGE_TYPE": "disabled",
        }
    )
    subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "docker-compose.deploy.yml",
            "config",
            "--quiet",
        ],
        cwd=ROOT,
        env=runtime_without_oss,
        check=True,
    )
