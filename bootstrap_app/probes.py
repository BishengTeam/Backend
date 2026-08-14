from __future__ import annotations

import asyncio
import base64
import json
import secrets
import time
from dataclasses import dataclass
from typing import Callable

import httpx
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15

from bootstrap_app.models import BootstrapConfigureRequest


class ExternalProbeError(RuntimeError):
    """A named external integration failed a non-destructive bootstrap probe."""

    def __init__(self, component: str, code: str) -> None:
        self.component = component
        self.code = code
        super().__init__(f"{component}:{code}")


@dataclass(frozen=True, slots=True)
class ProbeReport:
    wechat_login: str
    wechat_pay: str
    renshe_oss: str
    quiz_oss: str
    recovery_oss: str

    def public_dict(self) -> dict[str, str]:
        return {
            "wechat_login": self.wechat_login,
            "wechat_pay": self.wechat_pay,
            "renshe_oss": self.renshe_oss,
            "quiz_oss": self.quiz_oss,
            "recovery_oss": self.recovery_oss,
        }


def _secret(value) -> str:
    return value.get_secret_value()


async def _probe_wechat_login(
    request: BootstrapConfigureRequest,
    client_factory: Callable[[], httpx.AsyncClient],
) -> None:
    try:
        async with client_factory() as client:
            response = await client.get(
                "https://api.weixin.qq.com/cgi-bin/token",
                params={
                    "grant_type": "client_credential",
                    "appid": request.wechat_appid,
                    "secret": _secret(request.wechat_secret),
                },
            )
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        raise ExternalProbeError("wechat_login", "network_unavailable") from exc
    if response.status_code != 200:
        raise ExternalProbeError("wechat_login", "http_rejected")
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise ExternalProbeError("wechat_login", "invalid_response") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("access_token"), str):
        raise ExternalProbeError("wechat_login", "credentials_rejected")


def _authorization(request: BootstrapConfigureRequest) -> tuple[str, str]:
    private_key = RSA.import_key(_secret(request.wechat_pay_private_key_pem))
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    message = f"GET\n/v3/certificates\n{timestamp}\n{nonce}\n\n".encode("utf-8")
    signature = base64.b64encode(
        pkcs1_15.new(private_key).sign(SHA256.new(message))
    ).decode("ascii")
    authorization = (
        'WECHATPAY2-SHA256-RSA2048 '
        f'mchid="{request.wechat_pay_mchid}",'
        f'nonce_str="{nonce}",timestamp="{timestamp}",'
        f'serial_no="{request.wechat_pay_cert_serial_no}",'
        f'signature="{signature}"'
    )
    return authorization, timestamp


def _required_header(headers, name: str) -> str:
    value = headers.get(name)
    if not value:
        raise ExternalProbeError("wechat_pay", "unsigned_response")
    return value


async def _probe_wechat_pay(
    request: BootstrapConfigureRequest,
    client_factory: Callable[[], httpx.AsyncClient],
) -> None:
    authorization, _ = _authorization(request)
    try:
        async with client_factory() as client:
            response = await client.get(
                "https://api.mch.weixin.qq.com/v3/certificates",
                headers={
                    "Accept": "application/json",
                    "Authorization": authorization,
                    "User-Agent": "weMiniApp-bootstrap/1.0",
                    "Wechatpay-Serial": request.wechat_pay_public_key_id,
                },
            )
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        raise ExternalProbeError("wechat_pay", "network_unavailable") from exc

    timestamp = _required_header(response.headers, "Wechatpay-Timestamp")
    nonce = _required_header(response.headers, "Wechatpay-Nonce")
    signature = _required_header(response.headers, "Wechatpay-Signature")
    serial = _required_header(response.headers, "Wechatpay-Serial")
    if not secrets.compare_digest(serial.strip(), request.wechat_pay_public_key_id):
        raise ExternalProbeError("wechat_pay", "public_key_id_mismatch")
    if not timestamp.isascii() or not timestamp.isdecimal():
        raise ExternalProbeError("wechat_pay", "invalid_timestamp")
    if abs(int(time.time()) - int(timestamp)) > 300:
        raise ExternalProbeError("wechat_pay", "clock_skew")
    try:
        decoded_signature = base64.b64decode(signature, validate=True)
        public_key = RSA.import_key(_secret(request.wechat_pay_public_key_pem))
        message = (
            timestamp.encode("ascii")
            + b"\n"
            + nonce.encode("utf-8")
            + b"\n"
            + response.content
            + b"\n"
        )
        pkcs1_15.new(public_key).verify(SHA256.new(message), decoded_signature)
    except (ValueError, TypeError) as exc:
        raise ExternalProbeError("wechat_pay", "signature_invalid") from exc
    if not 200 <= response.status_code < 300:
        raise ExternalProbeError("wechat_pay", "credentials_rejected")


def _default_bucket_factory(access_id, access_secret, endpoint, bucket_name):
    import oss2

    return oss2.Bucket(oss2.Auth(access_id, access_secret), endpoint, bucket_name)


def _probe_oss_bucket_sync(
    *,
    component: str,
    endpoint: str,
    bucket_name: str,
    access_id: str,
    access_secret: str,
    installation_id: str,
    object_prefix: str,
    require_write: bool,
    bucket_factory: Callable,
) -> None:
    try:
        bucket = bucket_factory(access_id, access_secret, endpoint, bucket_name)
        acl = str(bucket.get_bucket_acl().acl).strip().lower()
        if acl != "private":
            raise ExternalProbeError(component, "bucket_not_private")
        if not require_write:
            return
        object_key = (
            f"{object_prefix.strip('/')}/bootstrap-probes/{installation_id}/"
            f"{secrets.token_hex(12)}"
        )
        uploaded = False
        try:
            bucket.put_object(
                object_key,
                b"bootstrap-probe",
                headers={"x-oss-meta-bootstrap": installation_id},
            )
            uploaded = True
            metadata = bucket.get_object_meta(object_key)
            headers = {str(k).lower(): str(v) for k, v in metadata.headers.items()}
            if headers.get("x-oss-meta-bootstrap") != installation_id:
                raise ExternalProbeError(component, "metadata_mismatch")
        finally:
            if uploaded:
                bucket.delete_object(object_key)
    except ExternalProbeError:
        raise
    except Exception as exc:
        raise ExternalProbeError(component, "bucket_probe_failed") from exc


async def validate_external_dependencies(
    request: BootstrapConfigureRequest,
    *,
    installation_id: str,
    client_factory: Callable[[], httpx.AsyncClient] | None = None,
    bucket_factory: Callable | None = None,
) -> ProbeReport:
    async_client_factory = client_factory or (
        lambda: httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5, read=15, write=10, pool=5),
            follow_redirects=False,
        )
    )
    oss_bucket_factory = bucket_factory or _default_bucket_factory

    await _probe_wechat_login(request, async_client_factory)
    await _probe_wechat_pay(request, async_client_factory)
    probes = (
        {
            "component": "renshe_oss",
            "endpoint": request.renshe_oss_endpoint,
            "bucket_name": request.renshe_oss_bucket,
            "access_id": _secret(request.renshe_oss_access_key_id),
            "access_secret": _secret(request.renshe_oss_access_key_secret),
            "object_prefix": "renshe",
            "require_write": True,
        },
        {
            "component": "quiz_oss",
            "endpoint": request.quiz_oss_endpoint,
            "bucket_name": request.quiz_oss_bucket,
            "access_id": _secret(request.quiz_oss_access_key_id),
            "access_secret": _secret(request.quiz_oss_access_key_secret),
            "object_prefix": "quiz-imports",
            "require_write": True,
        },
        {
            "component": "recovery_oss",
            "endpoint": request.recovery_oss_endpoint,
            "bucket_name": request.recovery_oss_bucket,
            "access_id": _secret(request.recovery_oss_access_key_id),
            "access_secret": _secret(request.recovery_oss_access_key_secret),
            "object_prefix": request.recovery_oss_prefix,
            # The encrypted bundle upload later is the authoritative write
            # check.  Do not require delete permission for the recovery key.
            "require_write": False,
        },
    )
    for probe in probes:
        try:
            await asyncio.wait_for(
                asyncio.to_thread(
                    _probe_oss_bucket_sync,
                    installation_id=installation_id,
                    bucket_factory=oss_bucket_factory,
                    **probe,
                ),
                timeout=20,
            )
        except TimeoutError as exc:
            raise ExternalProbeError(str(probe["component"]), "probe_timeout") from exc
    return ProbeReport(
        wechat_login="ok",
        wechat_pay="ok",
        renshe_oss="ok",
        quiz_oss="ok",
        recovery_oss="private_and_reachable",
    )
