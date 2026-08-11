"""WeChat Pay API V3 client.

Only the JSON/RSA/AES-GCM V3 protocol is implemented here.  Keeping the
provider protocol in one adapter prevents business services from accidentally
falling back to retired payment endpoints.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import logging
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import httpx
from Crypto.Cipher import AES
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15

from app.port.config import settings
from app.port.exceptions import ThirdPartyException

logger = logging.getLogger(__name__)

WECHAT_PAY_API_BASE = "https://api.mch.weixin.qq.com"
WECHAT_PAY_AUTH_SCHEMA = "WECHATPAY2-SHA256-RSA2048"
WECHAT_PAY_SIGN_TYPE = "RSA"
WECHAT_PAY_CURRENCY = "CNY"
WECHAT_PAY_NOTIFICATION_ALGORITHM = "AEAD_AES_256_GCM"
WECHAT_PAY_NOTIFICATION_TYPE = "transaction"
WECHAT_PAY_REFUND_NOTIFICATION_TYPE = "refund"
WECHAT_PAY_REFUND_STATUSES = frozenset(
    {"PROCESSING", "SUCCESS", "CLOSED", "ABNORMAL"}
)


class WechatPayAPIError(ThirdPartyException):
    """A signed V3 response reported a provider-side error."""

    def __init__(self, api_code: str, status_code: int) -> None:
        self.api_code = api_code or "UNKNOWN"
        self.status_code = status_code
        super().__init__(f"微信支付 API V3 请求失败: {self.api_code}")


class WechatPayResultUnknownError(ThirdPartyException):
    """A transport failure happened after a V3 request may have been sent.

    Callers must retain their stable merchant number and reconcile it with a
    provider query instead of creating a second business request.
    """


@dataclass(frozen=True, slots=True)
class WechatPayTransaction:
    """Normalized, validated transaction returned by WeChat Pay."""

    appid: str
    mchid: str
    out_trade_no: str
    trade_state: str
    amount_total: int
    currency: str
    attach: str
    transaction_id: str | None = None
    success_time: datetime | None = None
    payer_openid: str | None = None

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "WechatPayTransaction":
        def required_text(name: str) -> str:
            value = payload.get(name)
            if not isinstance(value, str) or not value.strip():
                raise ThirdPartyException(f"微信支付交易数据缺少字段: {name}")
            return value.strip()

        amount = payload.get("amount")
        if not isinstance(amount, Mapping):
            raise ThirdPartyException("微信支付交易数据缺少金额")
        total = amount.get("total")
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            raise ThirdPartyException("微信支付交易金额格式无效")
        currency = amount.get("currency")
        if not isinstance(currency, str) or not currency.strip():
            raise ThirdPartyException("微信支付交易币种格式无效")

        transaction_id = payload.get("transaction_id")
        if transaction_id is not None and (
            not isinstance(transaction_id, str) or not transaction_id.strip()
        ):
            raise ThirdPartyException("微信支付交易号格式无效")

        success_time = None
        raw_success_time = payload.get("success_time")
        if raw_success_time is not None:
            if not isinstance(raw_success_time, str):
                raise ThirdPartyException("微信支付成功时间格式无效")
            try:
                success_time = datetime.fromisoformat(
                    raw_success_time.replace("Z", "+00:00")
                )
            except ValueError as exc:
                raise ThirdPartyException("微信支付成功时间格式无效") from exc
            if success_time.tzinfo is None:
                raise ThirdPartyException("微信支付成功时间缺少时区")
            success_time = success_time.astimezone(timezone.utc)

        payer_openid = None
        payer = payload.get("payer")
        if payer is not None:
            if not isinstance(payer, Mapping):
                raise ThirdPartyException("微信支付付款人格式无效")
            raw_openid = payer.get("openid")
            if raw_openid is not None:
                if not isinstance(raw_openid, str) or not raw_openid.strip():
                    raise ThirdPartyException("微信支付付款人 openid 格式无效")
                payer_openid = raw_openid.strip()

        attach = payload.get("attach")
        if not isinstance(attach, str):
            raise ThirdPartyException("微信支付交易数据缺少 attach")

        trade_state = required_text("trade_state")
        if trade_state == "SUCCESS":
            if not transaction_id:
                raise ThirdPartyException("微信支付成功结果缺少交易号")
            if success_time is None:
                raise ThirdPartyException("微信支付成功结果缺少成功时间")
            if payer_openid is None:
                raise ThirdPartyException("微信支付成功结果缺少付款人 openid")

        return cls(
            appid=required_text("appid"),
            mchid=required_text("mchid"),
            out_trade_no=required_text("out_trade_no"),
            transaction_id=transaction_id.strip() if transaction_id else None,
            trade_state=trade_state,
            amount_total=total,
            currency=currency.strip().upper(),
            success_time=success_time,
            attach=attach,
            payer_openid=payer_openid,
        )


@dataclass(frozen=True, slots=True)
class WechatPayRefund:
    """Normalized V3 refund result from submit, query, or notification."""

    out_trade_no: str
    transaction_id: str
    out_refund_no: str
    refund_id: str
    status: str
    amount_total: int
    amount_refund: int
    currency: str
    mchid: str | None = None
    success_time: datetime | None = None

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        require_mchid: bool = False,
    ) -> "WechatPayRefund":
        def required_text(name: str) -> str:
            value = payload.get(name)
            if not isinstance(value, str) or not value.strip():
                raise ThirdPartyException(f"微信退款数据缺少字段: {name}")
            return value.strip()

        raw_status = payload.get("status", payload.get("refund_status"))
        if not isinstance(raw_status, str) or not raw_status.strip():
            raise ThirdPartyException("微信退款数据缺少状态")
        status = raw_status.strip().upper()
        if status not in WECHAT_PAY_REFUND_STATUSES:
            raise ThirdPartyException("微信退款数据状态不受支持")

        amount = payload.get("amount")
        if not isinstance(amount, Mapping):
            raise ThirdPartyException("微信退款数据缺少金额")

        def required_amount(name: str) -> int:
            value = amount.get(name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ThirdPartyException(f"微信退款金额字段无效: {name}")
            return value

        currency = amount.get("currency")
        if not isinstance(currency, str) or not currency.strip():
            raise ThirdPartyException("微信退款币种格式无效")

        mchid = payload.get("mchid")
        if mchid is not None and (
            not isinstance(mchid, str) or not mchid.strip()
        ):
            raise ThirdPartyException("微信退款商户号格式无效")
        if require_mchid and not mchid:
            raise ThirdPartyException("微信退款通知缺少商户号")

        success_time = None
        raw_success_time = payload.get("success_time")
        if raw_success_time is not None:
            if not isinstance(raw_success_time, str):
                raise ThirdPartyException("微信退款成功时间格式无效")
            try:
                success_time = datetime.fromisoformat(
                    raw_success_time.replace("Z", "+00:00")
                )
            except ValueError as exc:
                raise ThirdPartyException("微信退款成功时间格式无效") from exc
            if success_time.tzinfo is None:
                raise ThirdPartyException("微信退款成功时间缺少时区")
            success_time = success_time.astimezone(timezone.utc)
        if status == "SUCCESS" and success_time is None:
            raise ThirdPartyException("微信退款成功结果缺少成功时间")

        return cls(
            out_trade_no=required_text("out_trade_no"),
            transaction_id=required_text("transaction_id"),
            out_refund_no=required_text("out_refund_no"),
            refund_id=required_text("refund_id"),
            status=status,
            amount_total=required_amount("total"),
            amount_refund=required_amount("refund"),
            currency=currency.strip().upper(),
            mchid=mchid.strip() if isinstance(mchid, str) else None,
            success_time=success_time,
        )


class WechatPayClient:
    """Sign V3 requests, verify responses/notifications, and decrypt events."""

    def __init__(self) -> None:
        self.enabled = bool(settings.WECHAT_PAY_ENABLED)
        self.appid = settings.WECHAT_PAY_APPID or settings.WECHAT_APPID
        self.mch_id = settings.WECHAT_PAY_MCHID
        self.notify_url = settings.WECHAT_PAY_NOTIFY_URL
        self.refund_notify_url = settings.WECHAT_PAY_REFUND_NOTIFY_URL
        self.merchant_serial_no = settings.WECHAT_PAY_CERT_SERIAL_NO
        self.private_key_material = settings.WECHAT_PAY_PRIVATE_KEY
        self.api_v3_key = settings.WECHAT_PAY_API_V3_KEY
        self.platform_certificate_material = settings.WECHAT_PAY_PLATFORM_CERTIFICATE
        self.platform_serial_no = settings.WECHAT_PAY_PLATFORM_CERT_SERIAL_NO
        self.notification_tolerance_seconds = (
            settings.WECHAT_PAY_NOTIFICATION_TOLERANCE_SECONDS
        )
        self.api_base = WECHAT_PAY_API_BASE
        self._merchant_private_key: RSA.RsaKey | None = None
        self._platform_public_key: RSA.RsaKey | None = None

    def _missing_configuration(self) -> list[str]:
        values = {
            "WECHAT_PAY_ENABLED": self.enabled,
            "WECHAT_PAY_APPID/WECHAT_APPID": self.appid,
            "WECHAT_PAY_MCHID": self.mch_id,
            "WECHAT_PAY_NOTIFY_URL": self.notify_url,
            "WECHAT_PAY_CERT_SERIAL_NO": self.merchant_serial_no,
            "WECHAT_PAY_PRIVATE_KEY": self.private_key_material,
            "WECHAT_PAY_API_V3_KEY": self.api_v3_key,
            "WECHAT_PAY_PLATFORM_CERTIFICATE": self.platform_certificate_material,
            "WECHAT_PAY_PLATFORM_CERT_SERIAL_NO": self.platform_serial_no,
        }
        return [name for name, value in values.items() if not value]

    def _is_configured(self) -> bool:
        return not self._missing_configuration()

    def _ensure_configured(self) -> None:
        missing = self._missing_configuration()
        if not missing:
            return
        logger.error("Wechat Pay V3 configuration is incomplete: %s", missing)
        raise ThirdPartyException(
            f"微信支付 V3 配置不完整，缺少: {', '.join(missing)}"
        )

    def ensure_configured(self) -> None:
        """Public fail-fast guard used before a business state transition."""

        self._ensure_configured()

    def ensure_refund_configured(self) -> None:
        """Fail before approval if the dedicated refund callback is missing."""

        self._ensure_configured()
        if not self.refund_notify_url:
            raise ThirdPartyException(
                "微信支付 V3 配置不完整，缺少: WECHAT_PAY_REFUND_NOTIFY_URL"
            )

    @staticmethod
    def _read_key_material(value: str, setting_name: str) -> bytes:
        normalized = value.strip().replace("\\n", "\n")
        if normalized.startswith("-----BEGIN"):
            return normalized.encode("utf-8")
        path = Path(normalized)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise ThirdPartyException(f"{setting_name} 无法读取") from exc

    def _private_key(self) -> RSA.RsaKey:
        if self._merchant_private_key is None:
            try:
                self._merchant_private_key = RSA.import_key(
                    self._read_key_material(
                        self.private_key_material, "WECHAT_PAY_PRIVATE_KEY"
                    )
                )
            except (ValueError, IndexError, TypeError) as exc:
                raise ThirdPartyException("微信支付商户私钥格式无效") from exc
            if not self._merchant_private_key.has_private():
                raise ThirdPartyException("微信支付商户私钥不包含私钥")
            if self._merchant_private_key.size_in_bits() != 2048:
                raise ThirdPartyException("微信支付商户私钥必须为 RSA 2048 位")
        return self._merchant_private_key

    def _platform_key(self) -> RSA.RsaKey:
        if self._platform_public_key is None:
            try:
                self._platform_public_key = RSA.import_key(
                    self._read_key_material(
                        self.platform_certificate_material,
                        "WECHAT_PAY_PLATFORM_CERTIFICATE",
                    )
                ).public_key()
            except (ValueError, IndexError, TypeError) as exc:
                raise ThirdPartyException("微信支付平台证书格式无效") from exc
            if self._platform_public_key.size_in_bits() != 2048:
                raise ThirdPartyException("微信支付平台证书必须为 RSA 2048 位")
        return self._platform_public_key

    @staticmethod
    def _nonce() -> str:
        return secrets.token_hex(16)

    @staticmethod
    def _json_body(payload: Mapping[str, Any] | None) -> str:
        if payload is None:
            return ""
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=False,
        )

    def _rsa_sign(self, message: bytes) -> str:
        digest = SHA256.new(message)
        signature = pkcs1_15.new(self._private_key()).sign(digest)
        return base64.b64encode(signature).decode("ascii")

    def _build_authorization(
        self,
        *,
        method: str,
        request_target: str,
        body: str,
        timestamp: int | None = None,
        nonce: str | None = None,
    ) -> str:
        self._ensure_configured()
        request_timestamp = int(time.time()) if timestamp is None else timestamp
        request_nonce = nonce or self._nonce()
        message = (
            f"{method.upper()}\n{request_target}\n{request_timestamp}\n"
            f"{request_nonce}\n{body}\n"
        ).encode("utf-8")
        signature = self._rsa_sign(message)
        return (
            f'{WECHAT_PAY_AUTH_SCHEMA} mchid="{self.mch_id}",'
            f'nonce_str="{request_nonce}",timestamp="{request_timestamp}",'
            f'serial_no="{self.merchant_serial_no}",signature="{signature}"'
        )

    @staticmethod
    def _header(headers: Mapping[str, str], name: str) -> str:
        value = headers.get(name) or headers.get(name.lower())
        if not value:
            raise ThirdPartyException(f"微信支付签名缺少请求头: {name}")
        return value

    def verify_signature(
        self,
        *,
        timestamp: str,
        nonce: str,
        body: bytes,
        signature: str,
        serial: str,
        now: int | None = None,
    ) -> None:
        """Verify a V3 platform signature and reject stale/future messages."""

        self._ensure_configured()
        expected_serial = self.platform_serial_no.strip().upper()
        supplied_serial = serial.strip().upper()
        if not hmac.compare_digest(expected_serial, supplied_serial):
            raise ThirdPartyException("微信支付平台证书序列号不匹配")
        if not timestamp.isascii() or not timestamp.isdecimal():
            raise ThirdPartyException("微信支付签名时间戳无效")
        try:
            signed_at = int(timestamp)
        except (TypeError, ValueError) as exc:
            raise ThirdPartyException("微信支付签名时间戳无效") from exc
        current = int(time.time()) if now is None else now
        if abs(current - signed_at) > self.notification_tolerance_seconds:
            raise ThirdPartyException("微信支付签名已过期或本机时钟异常")
        if not nonce:
            raise ThirdPartyException("微信支付签名随机串为空")
        try:
            decoded_signature = base64.b64decode(signature, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ThirdPartyException("微信支付平台签名格式无效") from exc
        message = timestamp.encode("ascii") + b"\n" + nonce.encode("utf-8") + b"\n" + body + b"\n"
        try:
            pkcs1_15.new(self._platform_key()).verify(
                SHA256.new(message), decoded_signature
            )
        except (ValueError, TypeError) as exc:
            raise ThirdPartyException("微信支付平台签名验证失败") from exc

    def _verify_signed_body(
        self, headers: Mapping[str, str], body: bytes, *, now: int | None = None
    ) -> None:
        self.verify_signature(
            timestamp=self._header(headers, "Wechatpay-Timestamp"),
            nonce=self._header(headers, "Wechatpay-Nonce"),
            body=body,
            signature=self._header(headers, "Wechatpay-Signature"),
            serial=self._header(headers, "Wechatpay-Serial"),
            now=now,
        )

    async def _request_json(
        self,
        method: str,
        request_target: str,
        *,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = self._json_body(payload)
        authorization = self._build_authorization(
            method=method,
            request_target=request_target,
            body=body,
        )
        headers = {
            "Accept": "application/json",
            "Authorization": authorization,
            "Content-Type": "application/json",
            "User-Agent": "weMiniApp-wechat-pay-v3/1.0",
        }
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)
            ) as client:
                response = await client.request(
                    method.upper(),
                    f"{self.api_base}{request_target}",
                    content=body.encode("utf-8") if body else None,
                    headers=headers,
                )
        except httpx.TimeoutException as exc:
            raise WechatPayResultUnknownError(
                "微信支付 API V3 请求超时，结果未知，请主动查单"
            ) from exc
        except httpx.RequestError as exc:
            raise WechatPayResultUnknownError(
                "微信支付 API V3 网络请求失败，结果未知，请主动查单"
            ) from exc

        # V3 API responses are signed too.  Verify the exact bytes before
        # trusting either a success document or a provider error code.
        self._verify_signed_body(response.headers, response.content)
        response_data: dict[str, Any] = {}
        if response.content:
            try:
                parsed = response.json()
            except (ValueError, json.JSONDecodeError) as exc:
                raise ThirdPartyException("微信支付 API V3 返回无效 JSON") from exc
            if not isinstance(parsed, dict):
                raise ThirdPartyException("微信支付 API V3 返回格式无效")
            response_data = parsed
        if not 200 <= response.status_code < 300:
            api_code = response_data.get("code")
            raise WechatPayAPIError(
                api_code if isinstance(api_code, str) else "UNKNOWN",
                response.status_code,
            )
        return response_data

    async def create_jsapi_prepay(
        self,
        *,
        openid: str,
        out_trade_no: str,
        description: str,
        amount_total: int,
        attach: str,
        time_expire: datetime | None = None,
    ) -> dict[str, str]:
        payload: dict[str, Any] = {
            "appid": self.appid,
            "mchid": self.mch_id,
            "description": description,
            "out_trade_no": out_trade_no,
            "notify_url": self.notify_url,
            "amount": {"total": amount_total, "currency": WECHAT_PAY_CURRENCY},
            "payer": {"openid": openid},
            "attach": attach,
        }
        if time_expire is not None:
            expires_at = time_expire
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            payload["time_expire"] = expires_at.isoformat(timespec="seconds")
        data = await self._request_json(
            "POST", "/v3/pay/transactions/jsapi", payload=payload
        )
        prepay_id = data.get("prepay_id")
        if not isinstance(prepay_id, str) or not prepay_id:
            raise ThirdPartyException("微信支付预下单未返回 prepay_id")
        return self.build_jsapi_params(prepay_id)

    def build_jsapi_params(self, prepay_id: str) -> dict[str, str]:
        timestamp = str(int(time.time()))
        nonce = self._nonce()
        package = f"prepay_id={prepay_id}"
        message = f"{self.appid}\n{timestamp}\n{nonce}\n{package}\n".encode(
            "utf-8"
        )
        return {
            "prepay_id": prepay_id,
            "time_stamp": timestamp,
            "nonce_str": nonce,
            "package": package,
            "sign_type": WECHAT_PAY_SIGN_TYPE,
            "pay_sign": self._rsa_sign(message),
        }

    async def query_order(self, *, out_trade_no: str) -> WechatPayTransaction:
        encoded_trade_no = quote(out_trade_no, safe="")
        query = urlencode({"mchid": self.mch_id})
        data = await self._request_json(
            "GET",
            f"/v3/pay/transactions/out-trade-no/{encoded_trade_no}?{query}",
        )
        return WechatPayTransaction.from_payload(data)

    async def close_order(self, *, out_trade_no: str) -> None:
        encoded_trade_no = quote(out_trade_no, safe="")
        await self._request_json(
            "POST",
            f"/v3/pay/transactions/out-trade-no/{encoded_trade_no}/close",
            payload={"mchid": self.mch_id},
        )

    async def refund(
        self,
        *,
        out_trade_no: str,
        out_refund_no: str,
        amount_total: int,
        refund_amount: int,
        reason: str | None = None,
        notify_url: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "out_trade_no": out_trade_no,
            "out_refund_no": out_refund_no,
            "amount": {
                "refund": refund_amount,
                "total": amount_total,
                "currency": WECHAT_PAY_CURRENCY,
            },
        }
        if reason:
            payload["reason"] = reason[:80]
        if notify_url:
            payload["notify_url"] = notify_url
        return await self._request_json(
            "POST", "/v3/refund/domestic/refunds", payload=payload
        )

    async def query_refund(self, *, out_refund_no: str) -> dict[str, Any]:
        encoded_refund_no = quote(out_refund_no, safe="")
        return await self._request_json(
            "GET", f"/v3/refund/domestic/refunds/{encoded_refund_no}"
        )

    def parse_payment_notification(
        self,
        *,
        headers: Mapping[str, str],
        raw_body: bytes,
        now: int | None = None,
    ) -> WechatPayTransaction:
        """Authenticate and decrypt a V3 transaction notification."""

        self._verify_signed_body(headers, raw_body, now=now)
        try:
            envelope = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise ThirdPartyException("微信支付通知 JSON 无效") from exc
        if not isinstance(envelope, dict):
            raise ThirdPartyException("微信支付通知格式无效")
        if envelope.get("event_type") != "TRANSACTION.SUCCESS":
            raise ThirdPartyException("微信支付通知事件类型不受支持")
        resource = envelope.get("resource")
        if not isinstance(resource, Mapping):
            raise ThirdPartyException("微信支付通知缺少加密资源")
        if resource.get("algorithm") != WECHAT_PAY_NOTIFICATION_ALGORITHM:
            raise ThirdPartyException("微信支付通知加密算法不受支持")
        if resource.get("original_type") != WECHAT_PAY_NOTIFICATION_TYPE:
            raise ThirdPartyException("微信支付通知资源类型不受支持")

        nonce = resource.get("nonce")
        ciphertext = resource.get("ciphertext")
        associated_data = resource.get("associated_data", "")
        if not all(isinstance(value, str) for value in (nonce, ciphertext, associated_data)):
            raise ThirdPartyException("微信支付通知加密参数格式无效")
        api_key = self.api_v3_key.encode("utf-8")
        if len(api_key) != 32:
            raise ThirdPartyException("WECHAT_PAY_API_V3_KEY 必须为 32 字节")
        try:
            encrypted = base64.b64decode(ciphertext, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ThirdPartyException("微信支付通知密文格式无效") from exc
        if len(encrypted) <= 16:
            raise ThirdPartyException("微信支付通知密文长度无效")
        try:
            cipher = AES.new(api_key, AES.MODE_GCM, nonce=nonce.encode("utf-8"))
            cipher.update(associated_data.encode("utf-8"))
            plaintext = cipher.decrypt_and_verify(encrypted[:-16], encrypted[-16:])
            payload = json.loads(plaintext.decode("utf-8"))
        except (ValueError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ThirdPartyException("微信支付通知解密失败") from exc
        if not isinstance(payload, dict):
            raise ThirdPartyException("微信支付通知交易格式无效")
        return WechatPayTransaction.from_payload(payload)

    def parse_refund_notification(
        self,
        *,
        headers: Mapping[str, str],
        raw_body: bytes,
        now: int | None = None,
    ) -> WechatPayRefund:
        """Authenticate and decrypt a V3 domestic-refund notification."""

        envelope, payload = self._decrypt_notification(
            headers=headers,
            raw_body=raw_body,
            expected_event_types={
                "REFUND.SUCCESS",
                "REFUND.ABNORMAL",
                "REFUND.CLOSED",
            },
            expected_original_type=WECHAT_PAY_REFUND_NOTIFICATION_TYPE,
            now=now,
        )
        result = WechatPayRefund.from_payload(payload, require_mchid=True)
        expected_status = {
            "REFUND.SUCCESS": "SUCCESS",
            "REFUND.ABNORMAL": "ABNORMAL",
            "REFUND.CLOSED": "CLOSED",
        }[envelope["event_type"]]
        if result.status != expected_status:
            raise ThirdPartyException("微信退款通知事件类型与退款状态不一致")
        return result

    def _decrypt_notification(
        self,
        *,
        headers: Mapping[str, str],
        raw_body: bytes,
        expected_event_types: set[str],
        expected_original_type: str,
        now: int | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Verify a V3 notification and return its envelope and plaintext."""

        self._verify_signed_body(headers, raw_body, now=now)
        try:
            envelope = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise ThirdPartyException("微信支付通知 JSON 无效") from exc
        if not isinstance(envelope, dict):
            raise ThirdPartyException("微信支付通知格式无效")
        if envelope.get("event_type") not in expected_event_types:
            raise ThirdPartyException("微信支付通知事件类型不受支持")
        resource = envelope.get("resource")
        if not isinstance(resource, Mapping):
            raise ThirdPartyException("微信支付通知缺少加密资源")
        if resource.get("algorithm") != WECHAT_PAY_NOTIFICATION_ALGORITHM:
            raise ThirdPartyException("微信支付通知加密算法不受支持")
        if resource.get("original_type") != expected_original_type:
            raise ThirdPartyException("微信支付通知资源类型不受支持")

        nonce = resource.get("nonce")
        ciphertext = resource.get("ciphertext")
        associated_data = resource.get("associated_data", "")
        if not all(
            isinstance(value, str)
            for value in (nonce, ciphertext, associated_data)
        ):
            raise ThirdPartyException("微信支付通知加密参数格式无效")
        api_key = self.api_v3_key.encode("utf-8")
        if len(api_key) != 32:
            raise ThirdPartyException("WECHAT_PAY_API_V3_KEY 必须为 32 字节")
        try:
            encrypted = base64.b64decode(ciphertext, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ThirdPartyException("微信支付通知密文格式无效") from exc
        if len(encrypted) <= 16:
            raise ThirdPartyException("微信支付通知密文长度无效")
        try:
            cipher = AES.new(api_key, AES.MODE_GCM, nonce=nonce.encode("utf-8"))
            cipher.update(associated_data.encode("utf-8"))
            plaintext = cipher.decrypt_and_verify(encrypted[:-16], encrypted[-16:])
            payload = json.loads(plaintext.decode("utf-8"))
        except (ValueError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ThirdPartyException("微信支付通知解密失败") from exc
        if not isinstance(payload, dict):
            raise ThirdPartyException("微信支付通知资源格式无效")
        return envelope, payload


__all__ = [
    "WECHAT_PAY_API_BASE",
    "WECHAT_PAY_AUTH_SCHEMA",
    "WECHAT_PAY_CURRENCY",
    "WECHAT_PAY_SIGN_TYPE",
    "WechatPayAPIError",
    "WechatPayClient",
    "WechatPayRefund",
    "WechatPayResultUnknownError",
    "WechatPayTransaction",
]
