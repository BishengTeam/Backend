from __future__ import annotations

import base64
import time

import httpx
import pytest
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15

from bootstrap_app.models import BootstrapConfigureRequest
from bootstrap_app.probes import ExternalProbeError, validate_external_dependencies


def _request():
    merchant = RSA.generate(2048)
    payment = RSA.generate(2048)
    recovery = RSA.generate(3072)
    request = BootstrapConfigureRequest.model_validate(
        {
            "deployment_mode": "internal",
            "api_origin": "https://api.example.com",
            "admin_origin": "https://admin.example.com",
            "postgres_host": "db",
            "postgres_port": 5432,
            "postgres_user": "wemini",
            "postgres_database": "wemini_app",
            "wechat_appid": "wx08157fb5562f4ebe",
            "wechat_secret": "wechat-secret",
            "wechat_pay_mchid": "1113740961",
            "wechat_pay_appid": "wx08157fb5562f4ebe",
            "wechat_pay_cert_serial_no": "1234567890ABCDEF1234567890ABCDEF12345678",
            "wechat_pay_private_key_pem": merchant.export_key().decode(),
            "wechat_pay_api_v3_key": "v" * 32,
            "wechat_pay_public_key_pem": payment.public_key().export_key().decode(),
            "wechat_pay_public_key_id": "PUB_KEY_ID_TEST",
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
    )
    return request, payment


class _FakeAsyncClient:
    def __init__(self, response):
        self.response = response
        self.request = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, url, **kwargs):
        self.request = (url, kwargs)
        return self.response


class _ACL:
    acl = "private"


class _Meta:
    def __init__(self, installation_id):
        self.headers = {"x-oss-meta-bootstrap": installation_id}


class _Bucket:
    def __init__(self, installation_id, *, acl="private"):
        self.installation_id = installation_id
        self.acl = acl
        self.deleted = []

    def get_bucket_acl(self):
        value = _ACL()
        value.acl = self.acl
        return value

    def put_object(self, key, body, headers):
        assert body == b"bootstrap-probe"
        assert headers["x-oss-meta-bootstrap"] == self.installation_id

    def get_object_meta(self, _key):
        return _Meta(self.installation_id)

    def delete_object(self, key):
        self.deleted.append(key)


@pytest.mark.asyncio
async def test_external_probe_validates_signed_payment_and_private_oss():
    request, payment = _request()
    body = b'{"data":[]}'
    timestamp = str(int(time.time()))
    nonce = "response-nonce"
    message = timestamp.encode() + b"\n" + nonce.encode() + b"\n" + body + b"\n"
    signature = base64.b64encode(
        pkcs1_15.new(payment).sign(SHA256.new(message))
    ).decode()
    responses = iter(
        [
            httpx.Response(200, json={"access_token": "discarded", "expires_in": 7200}),
            httpx.Response(
                200,
                content=body,
                headers={
                    "Wechatpay-Timestamp": timestamp,
                    "Wechatpay-Nonce": nonce,
                    "Wechatpay-Signature": signature,
                    "Wechatpay-Serial": request.wechat_pay_public_key_id,
                },
            ),
        ]
    )
    clients = []

    def client_factory():
        client = _FakeAsyncClient(next(responses))
        clients.append(client)
        return client

    buckets = []

    def bucket_factory(*_args):
        bucket = _Bucket("install-1")
        buckets.append(bucket)
        return bucket

    report = await validate_external_dependencies(
        request,
        installation_id="install-1",
        client_factory=client_factory,
        bucket_factory=bucket_factory,
    )
    assert report.public_dict()["wechat_pay"] == "ok"
    assert len(buckets) == 3
    assert len(buckets[0].deleted) == 1
    assert len(buckets[1].deleted) == 1
    assert buckets[2].deleted == []
    payment_headers = clients[1].request[1]["headers"]
    assert payment_headers["Authorization"].startswith("WECHATPAY2-SHA256-RSA2048")


@pytest.mark.asyncio
async def test_external_probe_rejects_public_bucket_without_exposing_credentials():
    request, payment = _request()
    body = b"{}"
    timestamp = str(int(time.time()))
    nonce = "response-nonce"
    signature = base64.b64encode(
        pkcs1_15.new(payment).sign(
            SHA256.new(timestamp.encode() + b"\n" + nonce.encode() + b"\n" + body + b"\n")
        )
    ).decode()
    responses = iter(
        [
            httpx.Response(200, json={"access_token": "discarded"}),
            httpx.Response(
                200,
                content=body,
                headers={
                    "Wechatpay-Timestamp": timestamp,
                    "Wechatpay-Nonce": nonce,
                    "Wechatpay-Signature": signature,
                    "Wechatpay-Serial": request.wechat_pay_public_key_id,
                },
            ),
        ]
    )
    calls = 0

    def bucket_factory(*_args):
        nonlocal calls
        calls += 1
        return _Bucket("install-2", acl="public-read" if calls == 1 else "private")

    with pytest.raises(ExternalProbeError) as error:
        await validate_external_dependencies(
            request,
            installation_id="install-2",
            client_factory=lambda: _FakeAsyncClient(next(responses)),
            bucket_factory=bucket_factory,
        )
    assert error.value.component == "renshe_oss"
    assert error.value.code == "bucket_not_private"
    assert "renshe-secret" not in str(error.value)
