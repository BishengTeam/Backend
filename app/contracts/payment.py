"""Retired WeChat Pay HTTP contracts.

The platform settles every certification order through bank transfer and the
offline refund ledger.  WeChat Pay is no longer provisioned, so its HTTP
surface is retired: the routes stay documented (``deprecated``) but the
middleware below answers ``410 Gone`` before authentication or any provider
call can happen.
"""

from __future__ import annotations

WECHAT_PAY_RETIRED = True
WECHAT_PAY_RETIRED_CODE = 40205
WECHAT_PAY_RETIRED_MESSAGE = (
    "微信支付已停用，平台仅支持银行转账；历史微信订单请走线下退款登记"
)

RETIRED_WECHAT_PAY_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("POST", "/api/payment/prepay"),
    ("POST", "/api/payment/orders/{order_id}/sync"),
    ("POST", "/api/payment/callback"),
    ("POST", "/api/payment/refund-callback"),
    # Generic order refund submitted a V3 refund directly; superseded by
    # ``POST /admin/orders/{order_id}/offline-payment/refund``.
    ("POST", "/admin/orders/{order_id}/refund"),
    # NISP reconcile only existed to query WeChat for a processing refund.
    ("POST", "/admin/nisp/refunds/{refund_id}/reconcile"),
)


def wechat_pay_retired() -> bool:
    """Single switch consulted by routes, middleware, workers and services."""

    return WECHAT_PAY_RETIRED


__all__ = [
    "RETIRED_WECHAT_PAY_ENDPOINTS",
    "WECHAT_PAY_RETIRED",
    "WECHAT_PAY_RETIRED_CODE",
    "WECHAT_PAY_RETIRED_MESSAGE",
    "wechat_pay_retired",
]
