"""审核回调注册表"""

from collections.abc import Awaitable, Callable

ReviewCallback = Callable[[int, str | None], Awaitable[None]]

REVIEW_CALLBACKS: dict[tuple[str, str], ReviewCallback] = {}


def _register():
    from app.services.review_callbacks import (
        identity_callbacks,
        order_callbacks,
        student_callbacks,
    )
    REVIEW_CALLBACKS[("identity",   "approve")] = identity_callbacks.approve
    REVIEW_CALLBACKS[("identity",   "reject")]  = identity_callbacks.reject
    REVIEW_CALLBACKS[("student",    "approve")] = student_callbacks.approve
    REVIEW_CALLBACKS[("student",    "reject")]  = student_callbacks.reject
    REVIEW_CALLBACKS[("order",      "approve")] = order_callbacks.approve
    REVIEW_CALLBACKS[("order",      "reject")]  = order_callbacks.reject


_register()
