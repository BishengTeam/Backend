import asyncio
import base64
import json
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from Crypto.Cipher import AES
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15
from starlette.requests import Request

import app.api.payment as payment_api
import app.integrations.wechat_pay as wechat_pay_module
import app.services.admin_order as admin_order_module
import app.services.payment as payment_service_module
import app.services.payment_reconciliation as reconciliation_module
from app.integrations.wechat_pay import (
    WECHAT_PAY_AUTH_SCHEMA,
    WechatPayClient,
    WechatPayRefund,
    WechatPayTransaction,
)
from app.middleware.rate_limit import payment_user_key
from app.port.exceptions import (
    BusinessException,
    ConflictException,
    NotFoundException,
    ThirdPartyException,
)
from app.schemas.payment import PaymentCallbackResponse, PaymentSyncResponse
from app.schemas.payment import PaymentPrepayRequest
from app.services.payment import PaymentService
from app.services.payment_reconciliation import PaymentReconciliationService
from app.services.admin_order import AdminOrderService
from app.utils.payment import generate_out_trade_no


@pytest.fixture(scope="module")
def rsa_keys():
    return RSA.generate(2048), RSA.generate(2048)


def _client(rsa_keys) -> tuple[WechatPayClient, RSA.RsaKey, RSA.RsaKey]:
    merchant_key, platform_key = rsa_keys
    client = WechatPayClient()
    client.enabled = True
    client.appid = "wx-test-appid"
    client.mch_id = "1900000001"
    client.notify_url = "https://pay.example.test/api/payment/callback"
    client.refund_notify_url = "https://pay.example.test/api/payment/refund-callback"
    client.merchant_serial_no = "MERCHANT-SERIAL"
    client.private_key_material = "configured"
    client.api_v3_key = "0123456789abcdef0123456789abcdef"
    client.platform_certificate_material = "configured"
    client.platform_serial_no = "PLATFORM-SERIAL"
    client.notification_tolerance_seconds = 300
    client._merchant_private_key = merchant_key
    client._platform_public_key = platform_key.public_key()
    return client, merchant_key, platform_key


def _platform_signature(platform_key: RSA.RsaKey, message: bytes) -> str:
    signature = pkcs1_15.new(platform_key).sign(SHA256.new(message))
    return base64.b64encode(signature).decode("ascii")


def _transaction_payload(**overrides):
    payload = {
        "appid": "wx-test-appid",
        "mchid": "1900000001",
        "out_trade_no": "order-1001",
        "transaction_id": "420000000120260810000001",
        "trade_state": "SUCCESS",
        "attach": "order:1001",
        "success_time": "2026-08-10T10:00:00+08:00",
        "payer": {"openid": "openid-1001"},
        "amount": {"total": 12800, "currency": "CNY"},
    }
    payload.update(overrides)
    return payload


def _notification(
    client: WechatPayClient,
    platform_key: RSA.RsaKey,
    *,
    timestamp: int,
    transaction: dict | None = None,
    encryption_key: bytes | None = None,
) -> tuple[bytes, dict[str, str]]:
    nonce = b"0123456789ab"
    associated_data = b"transaction"
    plaintext = json.dumps(
        transaction or _transaction_payload(),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    cipher = AES.new(
        encryption_key or client.api_v3_key.encode("utf-8"),
        AES.MODE_GCM,
        nonce=nonce,
    )
    cipher.update(associated_data)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext)
    envelope = {
        "id": "event-1",
        "create_time": "2026-08-10T10:00:01+08:00",
        "event_type": "TRANSACTION.SUCCESS",
        "resource_type": "encrypt-resource",
        "resource": {
            "algorithm": "AEAD_AES_256_GCM",
            "ciphertext": base64.b64encode(ciphertext + tag).decode("ascii"),
            "associated_data": associated_data.decode("ascii"),
            "nonce": nonce.decode("ascii"),
            "original_type": "transaction",
        },
        "summary": "支付成功",
    }
    raw_body = json.dumps(
        envelope, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    header_nonce = "header-nonce"
    signed_message = (
        f"{timestamp}\n{header_nonce}\n".encode("utf-8") + raw_body + b"\n"
    )
    return raw_body, {
        "Wechatpay-Timestamp": str(timestamp),
        "Wechatpay-Nonce": header_nonce,
        "Wechatpay-Signature": _platform_signature(platform_key, signed_message),
        "Wechatpay-Serial": client.platform_serial_no,
    }


def _refund_payload(**overrides):
    payload = {
        "mchid": "1900000001",
        "out_trade_no": "order-1001",
        "transaction_id": "420000000120260810000001",
        "out_refund_no": "RSRF0000000000000000000000000001",
        "refund_id": "503000000120260810000001",
        "refund_status": "SUCCESS",
        "success_time": "2026-08-10T10:05:00+08:00",
        "amount": {"total": 12800, "refund": 12800, "currency": "CNY"},
    }
    payload.update(overrides)
    return payload


def _refund_notification(
    client: WechatPayClient,
    platform_key: RSA.RsaKey,
    *,
    timestamp: int,
    refund: dict | None = None,
    event_type: str = "REFUND.SUCCESS",
) -> tuple[bytes, dict[str, str]]:
    nonce = b"0123456789ab"
    associated_data = b"refund"
    plaintext = json.dumps(
        refund or _refund_payload(),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    cipher = AES.new(
        client.api_v3_key.encode("utf-8"),
        AES.MODE_GCM,
        nonce=nonce,
    )
    cipher.update(associated_data)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext)
    envelope = {
        "id": "refund-event-1",
        "create_time": "2026-08-10T10:05:01+08:00",
        "event_type": event_type,
        "resource_type": "encrypt-resource",
        "resource": {
            "algorithm": "AEAD_AES_256_GCM",
            "ciphertext": base64.b64encode(ciphertext + tag).decode("ascii"),
            "associated_data": associated_data.decode("ascii"),
            "nonce": nonce.decode("ascii"),
            "original_type": "refund",
        },
        "summary": "退款状态变更",
    }
    raw_body = json.dumps(
        envelope, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    header_nonce = "refund-header-nonce"
    signed_message = (
        f"{timestamp}\n{header_nonce}\n".encode("utf-8") + raw_body + b"\n"
    )
    return raw_body, {
        "Wechatpay-Timestamp": str(timestamp),
        "Wechatpay-Nonce": header_nonce,
        "Wechatpay-Signature": _platform_signature(platform_key, signed_message),
        "Wechatpay-Serial": client.platform_serial_no,
    }
def test_v3_authorization_and_jsapi_parameters_use_rsa(rsa_keys) -> None:
    client, merchant_key, _platform_key = _client(rsa_keys)
    authorization = client._build_authorization(
        method="POST",
        request_target="/v3/pay/transactions/jsapi",
        body='{"amount":{"total":12800}}',
        timestamp=1_786_330_800,
        nonce="request-nonce",
    )
    assert authorization.startswith(f"{WECHAT_PAY_AUTH_SCHEMA} ")
    fields = dict(re.findall(r'(\w+)="([^"]+)"', authorization))
    assert fields["mchid"] == client.mch_id
    assert fields["serial_no"] == client.merchant_serial_no
    canonical = (
        "POST\n/v3/pay/transactions/jsapi\n1786330800\nrequest-nonce\n"
        '{"amount":{"total":12800}}\n'
    ).encode("utf-8")
    pkcs1_15.new(merchant_key.public_key()).verify(
        SHA256.new(canonical), base64.b64decode(fields["signature"])
    )

    client._nonce = lambda: "jsapi-nonce"
    original_time = wechat_pay_module.time.time
    wechat_pay_module.time.time = lambda: 1_786_330_801
    try:
        params = client.build_jsapi_params("prepay-id-1")
    finally:
        wechat_pay_module.time.time = original_time
    assert params["sign_type"] == "RSA"
    assert params["package"] == "prepay_id=prepay-id-1"
    jsapi_message = (
        "wx-test-appid\n1786330801\njsapi-nonce\nprepay_id=prepay-id-1\n"
    ).encode("utf-8")
    pkcs1_15.new(merchant_key.public_key()).verify(
        SHA256.new(jsapi_message), base64.b64decode(params["pay_sign"])
    )


def test_all_business_order_numbers_fit_wechat_v3_limit() -> None:
    values = {generate_out_trade_no("RS-ZY") for _ in range(100)}
    assert len(values) == 100
    assert all(6 <= len(value) <= 32 for value in values)
    assert all(re.fullmatch(r"[0-9A-Za-z_-]+", value) for value in values)


@pytest.mark.asyncio
async def test_v3_jsapi_prepay_sends_frozen_server_values(
    rsa_keys, monkeypatch
) -> None:
    client, _merchant_key, platform_key = _client(rsa_keys)
    captured = {}

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method, url, *, content, headers):
            captured.update(
                method=method,
                url=url,
                content=content,
                headers=headers,
            )
            response_body = b'{"prepay_id":"prepay-id-1"}'
            response_timestamp = str(int(time.time()))
            response_nonce = "response-nonce"
            signature = _platform_signature(
                platform_key,
                response_timestamp.encode()
                + b"\n"
                + response_nonce.encode()
                + b"\n"
                + response_body
                + b"\n",
            )
            return httpx.Response(
                200,
                content=response_body,
                headers={
                    "Wechatpay-Timestamp": response_timestamp,
                    "Wechatpay-Nonce": response_nonce,
                    "Wechatpay-Signature": signature,
                    "Wechatpay-Serial": client.platform_serial_no,
                },
                request=httpx.Request(method, url),
            )

    monkeypatch.setattr(wechat_pay_module.httpx, "AsyncClient", FakeAsyncClient)
    expires_at = datetime(2026, 8, 10, 11, 0, tzinfo=timezone.utc)
    result = await client.create_jsapi_prepay(
        openid="openid-1001",
        out_trade_no="order-1001",
        description="RS-ZY 订单服务费",
        amount_total=12800,
        attach="order:1001",
        time_expire=expires_at,
    )

    sent = json.loads(captured["content"])
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/v3/pay/transactions/jsapi")
    assert captured["headers"]["Authorization"].startswith(
        WECHAT_PAY_AUTH_SCHEMA
    )
    assert sent == {
        "appid": client.appid,
        "mchid": client.mch_id,
        "description": "RS-ZY 订单服务费",
        "out_trade_no": "order-1001",
        "notify_url": client.notify_url,
        "amount": {"total": 12800, "currency": "CNY"},
        "payer": {"openid": "openid-1001"},
        "attach": "order:1001",
        "time_expire": "2026-08-10T11:00:00+00:00",
    }
    assert result["prepay_id"] == "prepay-id-1"
    assert result["sign_type"] == "RSA"


def test_v3_notification_verifies_signature_and_decrypts_aes_gcm(rsa_keys) -> None:
    client, _merchant_key, platform_key = _client(rsa_keys)
    now = int(time.time())
    raw_body, headers = _notification(client, platform_key, timestamp=now)
    transaction = client.parse_payment_notification(
        headers=headers, raw_body=raw_body, now=now
    )
    assert transaction.out_trade_no == "order-1001"
    assert transaction.transaction_id == "420000000120260810000001"
    assert transaction.amount_total == 12800
    assert transaction.currency == "CNY"
    assert transaction.payer_openid == "openid-1001"


def test_v3_refund_notification_verifies_decrypts_and_normalizes(rsa_keys) -> None:
    client, _merchant_key, platform_key = _client(rsa_keys)
    now = int(time.time())
    raw_body, headers = _refund_notification(
        client,
        platform_key,
        timestamp=now,
    )

    refund = client.parse_refund_notification(
        headers=headers,
        raw_body=raw_body,
        now=now,
    )

    assert isinstance(refund, WechatPayRefund)
    assert refund.status == "SUCCESS"
    assert refund.out_refund_no == "RSRF0000000000000000000000000001"
    assert refund.amount_total == refund.amount_refund == 12800
    assert refund.mchid == "1900000001"


def test_v3_refund_notification_rejects_event_status_mismatch(rsa_keys) -> None:
    client, _merchant_key, platform_key = _client(rsa_keys)
    now = int(time.time())
    raw_body, headers = _refund_notification(
        client,
        platform_key,
        timestamp=now,
        event_type="REFUND.CLOSED",
    )

    with pytest.raises(ThirdPartyException, match="事件类型与退款状态"):
        client.parse_refund_notification(
            headers=headers,
            raw_body=raw_body,
            now=now,
        )


def test_v3_refund_notification_rejects_forged_signature(rsa_keys) -> None:
    client, _merchant_key, platform_key = _client(rsa_keys)
    now = int(time.time())
    raw_body, headers = _refund_notification(
        client,
        platform_key,
        timestamp=now,
    )
    headers["Wechatpay-Signature"] = base64.b64encode(b"forged").decode()

    with pytest.raises(ThirdPartyException, match="签名验证失败"):
        client.parse_refund_notification(
            headers=headers,
            raw_body=raw_body,
            now=now,
        )


@pytest.mark.parametrize("failure", ["serial", "signature", "timestamp", "ciphertext"])
def test_v3_notification_rejects_forged_or_stale_input(rsa_keys, failure) -> None:
    client, _merchant_key, platform_key = _client(rsa_keys)
    now = int(time.time())
    encryption_key = b"x" * 32 if failure == "ciphertext" else None
    raw_body, headers = _notification(
        client,
        platform_key,
        timestamp=now - 301 if failure == "timestamp" else now,
        encryption_key=encryption_key,
    )
    if failure == "serial":
        headers["Wechatpay-Serial"] = "WRONG-SERIAL"
    elif failure == "signature":
        headers["Wechatpay-Signature"] = base64.b64encode(b"forged").decode()

    with pytest.raises(ThirdPartyException):
        client.parse_payment_notification(headers=headers, raw_body=raw_body, now=now)


@pytest.mark.asyncio
async def test_v3_client_exposes_query_close_refund_and_refund_query(rsa_keys) -> None:
    client, _merchant_key, _platform_key = _client(rsa_keys)
    request = AsyncMock(
        side_effect=[
            _transaction_payload(),
            {},
            {"refund_id": "refund-1", "status": "PROCESSING"},
            {"refund_id": "refund-1", "status": "SUCCESS"},
        ]
    )
    client._request_json = request

    transaction = await client.query_order(out_trade_no="order/1001")
    await client.close_order(out_trade_no="order/1001")
    refund = await client.refund(
        out_trade_no="order/1001",
        out_refund_no="refund/1001",
        amount_total=12800,
        refund_amount=12800,
        reason="审核退款",
    )
    refund_query = await client.query_refund(out_refund_no="refund/1001")

    assert transaction.trade_state == "SUCCESS"
    assert refund["status"] == "PROCESSING"
    assert refund_query["status"] == "SUCCESS"
    assert request.await_args_list[0].args == (
        "GET",
        "/v3/pay/transactions/out-trade-no/order%2F1001?mchid=1900000001",
    )
    assert request.await_args_list[1].args == (
        "POST",
        "/v3/pay/transactions/out-trade-no/order%2F1001/close",
    )
    assert request.await_args_list[2].args == (
        "POST",
        "/v3/refund/domestic/refunds",
    )
    assert request.await_args_list[3].args == (
        "GET",
        "/v3/refund/domestic/refunds/refund%2F1001",
    )


@pytest.mark.asyncio
async def test_v3_timeout_reports_unknown_result_for_safe_same_order_retry(
    rsa_keys, monkeypatch
) -> None:
    client, _merchant_key, _platform_key = _client(rsa_keys)

    class TimeoutAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method, url, **_kwargs):
            raise httpx.ReadTimeout("timeout", request=httpx.Request(method, url))

    monkeypatch.setattr(wechat_pay_module.httpx, "AsyncClient", TimeoutAsyncClient)
    with pytest.raises(ThirdPartyException, match="结果未知，请主动查单"):
        await client.create_jsapi_prepay(
            openid="openid-1001",
            out_trade_no="stable-order-number",
            description="RS-ZY 订单服务费",
            amount_total=12800,
            attach="order:1001",
        )


@pytest.mark.asyncio
async def test_generic_admin_refund_never_treats_processing_as_success(
    monkeypatch,
) -> None:
    refund = AsyncMock(
        return_value={
            "refund_id": "refund-1",
            "out_refund_no": "RF1001",
            "status": "PROCESSING",
            "amount": {"total": 12800, "refund": 12800},
        }
    )
    query_refund = AsyncMock(
        return_value={
            "refund_id": "refund-1",
            "out_refund_no": "RF1001",
            "status": "SUCCESS",
            "amount": {"total": 12800, "refund": 12800},
        }
    )
    monkeypatch.setattr(
        admin_order_module,
        "WechatPayClient",
        lambda: SimpleNamespace(refund=refund, query_refund=query_refund),
    )
    order = _order(status="paid", transaction_id="transaction-1001")
    succeeded = await AdminOrderService._submit_wechat_v3_refund(order)
    assert succeeded is False
    assert order.status == "paid"
    assert order.extra_data["_wechat_refund_v3"] == {
        "out_refund_no": "RF1001",
        "status": "PROCESSING",
        "refund_id": "refund-1",
    }
    refund.assert_awaited_once_with(
        out_trade_no="order-1001",
        out_refund_no="RF1001",
        amount_total=12800,
        refund_amount=12800,
        reason=None,
    )
    succeeded = await AdminOrderService._submit_wechat_v3_refund(order)
    assert succeeded is True
    query_refund.assert_awaited_once_with(out_refund_no="RF1001")
    assert order.extra_data["_wechat_refund_v3"]["status"] == "SUCCESS"


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _SharedOrderStore:
    def __init__(self, order, *, duplicate=None):
        self.order = order
        self.duplicate = duplicate
        self.lock = asyncio.Lock()
        self.commits = 0
        self.rollbacks = 0


class _PaymentSession:
    def __init__(self, store: _SharedOrderStore):
        self.store = store
        self.execute_count = 0

    async def execute(self, _statement):
        self.execute_count += 1
        if self.execute_count == 1:
            return _ScalarResult(self.store.order)
        return _ScalarResult(self.store.duplicate)

    async def get(self, model, _identifier):
        if model.__name__ == "User":
            return SimpleNamespace(openid="openid-1001", is_active=True)
        return self.store.order

    async def commit(self):
        self.store.commits += 1

    async def rollback(self):
        self.store.rollbacks += 1

    async def refresh(self, _value):
        return None


def _order(**overrides):
    values = {
        "id": 1001,
        "user_id": 51,
        "order_kind": "certification",
        "product_type": "RS-ZY",
        "application_id": None,
        "out_trade_no": "order-1001",
        "transaction_id": None,
        "price": 12800,
        "status": "pending",
        "paid_at": None,
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=30),
        "closed_at": None,
        "close_reason": None,
        "extra_data": {},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _transaction(**overrides):
    values = {
        "appid": "wx-test-appid",
        "mchid": "1900000001",
        "out_trade_no": "order-1001",
        "transaction_id": "transaction-1001",
        "trade_state": "SUCCESS",
        "amount_total": 12800,
        "currency": "CNY",
        "attach": "order:1001",
        "success_time": datetime.now(timezone.utc),
        "payer_openid": "openid-1001",
    }
    values.update(overrides)
    return WechatPayTransaction(**values)


def _payment_service_with_store(monkeypatch, store):
    @asynccontextmanager
    async def fake_db_ctx():
        async with store.lock:
            yield _PaymentSession(store)

    monkeypatch.setattr(payment_service_module, "get_db_ctx", fake_db_ctx)
    service = PaymentService()
    service.wechat_pay = SimpleNamespace(
        appid="wx-test-appid",
        mch_id="1900000001",
    )
    service._confirm_inventory_sale = AsyncMock()
    service._release_inventory_lock = AsyncMock()
    service._refund_inventory_sale = AsyncMock(return_value=False)
    service.fulfillment = SimpleNamespace(
        on_paid=AsyncMock(return_value=False),
        on_closed=AsyncMock(return_value=False),
        on_refunded=AsyncMock(return_value=False),
    )
    return service


@pytest.mark.asyncio
async def test_prepay_retry_reuses_existing_business_order(monkeypatch) -> None:
    order = _order(out_trade_no="stable-order-1001")
    store = _SharedOrderStore(order)

    @asynccontextmanager
    async def fake_db_ctx():
        async with store.lock:
            yield _PaymentSession(store)

    monkeypatch.setattr(payment_service_module, "get_db_ctx", fake_db_ctx)
    remote_prepay = AsyncMock(
        return_value={
            "prepay_id": "prepay-id-1",
            "time_stamp": "1786330801",
            "nonce_str": "nonce",
            "package": "prepay_id=prepay-id-1",
            "sign_type": "RSA",
            "pay_sign": "signed",
        }
    )
    service = PaymentService()
    service.wechat_pay = SimpleNamespace(create_jsapi_prepay=remote_prepay)

    first = await service.create_prepay(51, PaymentPrepayRequest(order_id=1001))
    second = await service.create_prepay(51, PaymentPrepayRequest(order_id=1001))

    assert first.order_id == second.order_id == 1001
    assert first.out_trade_no == second.out_trade_no == "stable-order-1001"
    assert remote_prepay.await_count == 2
    assert {
        call.kwargs["out_trade_no"] for call in remote_prepay.await_args_list
    } == {"stable-order-1001"}


@pytest.mark.asyncio
async def test_notification_and_query_race_is_idempotent(monkeypatch) -> None:
    order = _order()
    store = _SharedOrderStore(order)
    service = _payment_service_with_store(monkeypatch, store)
    transaction = _transaction()

    first, second = await asyncio.gather(
        service._apply_transaction(
            transaction,
            source="notification",
            verify_provider_fields=True,
        ),
        service._apply_transaction(
            transaction,
            source="user_sync",
            verify_provider_fields=True,
        ),
    )

    assert sorted([first.processed, second.processed]) == [False, True]
    assert order.status == "paid"
    assert order.transaction_id == "transaction-1001"
    service._confirm_inventory_sale.assert_awaited_once()
    assert store.commits == 1


@pytest.mark.asyncio
async def test_reconciliation_rejects_amount_and_transaction_reuse(monkeypatch) -> None:
    order = _order()
    store = _SharedOrderStore(order)
    service = _payment_service_with_store(monkeypatch, store)
    with pytest.raises(BusinessException, match="支付金额"):
        await service._apply_transaction(
            _transaction(amount_total=1),
            source="notification",
            verify_provider_fields=True,
        )
    assert order.status == "pending"
    service._confirm_inventory_sale.assert_not_awaited()

    store.duplicate = _order(id=2002, out_trade_no="other-order")
    with pytest.raises(ConflictException, match="已绑定其他订单"):
        await service._apply_transaction(
            _transaction(),
            source="notification",
            verify_provider_fields=True,
        )
    assert order.status == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override",
    [
        {"appid": "wrong-appid"},
        {"mchid": "wrong-mchid"},
        {"currency": "USD"},
        {"attach": "order:9999"},
        {"payer_openid": "another-openid"},
    ],
)
async def test_reconciliation_rejects_provider_identity_mismatches(
    monkeypatch, override
) -> None:
    order = _order()
    store = _SharedOrderStore(order)
    service = _payment_service_with_store(monkeypatch, store)
    with pytest.raises(BusinessException):
        await service._apply_transaction(
            _transaction(**override),
            source="notification",
            verify_provider_fields=True,
        )
    assert order.status == "pending"
    assert store.commits == 0
    service._confirm_inventory_sale.assert_not_awaited()


@pytest.mark.asyncio
async def test_payment_after_expiration_closes_without_fulfilling(monkeypatch) -> None:
    expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    order = _order(expires_at=expires_at)
    store = _SharedOrderStore(order)
    service = _payment_service_with_store(monkeypatch, store)
    result = await service._apply_transaction(
        _transaction(success_time=expires_at + timedelta(seconds=1)),
        source="reconciliation_worker",
        verify_provider_fields=True,
    )

    assert result.processed is True
    assert order.status == "closed"
    assert order.close_reason == "payment_after_expiration"
    assert order.extra_data["_wechat_pay_v3"]["late_payment"][
        "requires_refund_review"
    ] is True
    service._confirm_inventory_sale.assert_not_awaited()
    service.fulfillment.on_paid.assert_not_awaited()
    service._release_inventory_lock.assert_awaited_once()


@pytest.mark.asyncio
async def test_out_of_order_closed_result_cannot_roll_back_paid_order(monkeypatch) -> None:
    order = _order(status="paid", transaction_id="transaction-1001")
    store = _SharedOrderStore(order)
    service = _payment_service_with_store(monkeypatch, store)
    result = await service._apply_transaction(
        _transaction(
            trade_state="CLOSED",
            transaction_id=None,
            success_time=None,
            payer_openid=None,
        ),
        source="user_sync",
        verify_provider_fields=True,
    )
    assert result.processed is False
    assert order.status == "paid"
    assert store.commits == 0


@pytest.mark.asyncio
async def test_sync_checks_owner_before_calling_wechat(monkeypatch) -> None:
    @asynccontextmanager
    async def missing_db_ctx():
        yield SimpleNamespace(execute=AsyncMock(return_value=_ScalarResult(None)))

    monkeypatch.setattr(payment_service_module, "get_db_ctx", missing_db_ctx)
    service = PaymentService()
    service.wechat_pay = SimpleNamespace(query_order=AsyncMock())
    with pytest.raises(NotFoundException):
        await service.sync_order(user_id=99, order_id=1001)
    service.wechat_pay.query_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_isolates_item_failures_and_can_retry(monkeypatch) -> None:
    class _IdsResult:
        def all(self):
            return [1, 2, 3]

    class _IdsSession:
        async def scalars(self, _statement):
            return _IdsResult()

    @asynccontextmanager
    async def ids_db_ctx():
        yield _IdsSession()

    monkeypatch.setattr(reconciliation_module, "get_db_ctx", ids_db_ctx)
    fake_payment = SimpleNamespace(sync_pending_order=AsyncMock())
    fake_payment.sync_pending_order.side_effect = [
        PaymentSyncResponse(
            order_id=1,
            status="paid",
            processed=True,
            trade_state="SUCCESS",
            synchronized_at=datetime.now(timezone.utc),
        ),
        ThirdPartyException("provider timeout"),
        None,
        PaymentSyncResponse(
            order_id=1,
            status="paid",
            processed=False,
            trade_state="SUCCESS",
            synchronized_at=datetime.now(timezone.utc),
        ),
        None,
        None,
    ]
    service = PaymentReconciliationService(fake_payment)

    first = await service.reconcile_batch(limit=3)
    second = await service.reconcile_batch(limit=3)
    assert (first.scanned, first.synchronized, first.changed, first.failed) == (
        3,
        1,
        1,
        1,
    )
    assert (second.scanned, second.synchronized, second.changed, second.failed) == (
        3,
        1,
        0,
        0,
    )


def _request_with_body(body: bytes) -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/payment/callback",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "scheme": "https",
            "server": ("testserver", 443),
        },
        receive,
    )


@pytest.mark.asyncio
async def test_callback_returns_wechat_v3_ack_without_api_envelope(monkeypatch) -> None:
    fake_service = SimpleNamespace(
        handle_callback_raw=AsyncMock(
            return_value=PaymentCallbackResponse(
                order_id=1001,
                status="paid",
                processed=True,
            )
        )
    )
    monkeypatch.setattr(payment_api, "PaymentService", lambda: fake_service)
    response = await payment_api.payment_callback(_request_with_body(b"{}"))
    assert response.status_code == 200
    assert json.loads(response.body) == {"code": "SUCCESS", "message": "成功"}
    assert "data" not in json.loads(response.body)

    fake_service.handle_callback_raw.side_effect = ThirdPartyException("bad signature")
    response = await payment_api.payment_callback(_request_with_body(b"{}"))
    assert response.status_code == 400
    assert json.loads(response.body) == {"code": "FAIL", "message": "失败"}


def test_payment_rate_limit_key_is_per_authenticated_user(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.adapter.security.decode_access_token",
        lambda _token: {"type": "access", "user_id": 51},
    )
    request = SimpleNamespace(
        headers={"authorization": "Bearer valid"},
        client=SimpleNamespace(host="192.0.2.10"),
    )
    assert payment_user_key(request) == "payment:user:51"
