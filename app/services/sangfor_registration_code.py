"""Sangfor registration code issued after the payment is confirmed."""
import secrets
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.order.src.index import Order

CODE_FIELD = "sangfor_registration_code"
GENERATED_AT_FIELD = "sangfor_registration_code_generated_at"


def build_code(order_id: int) -> str:
    # 64 bit 随机后缀：仅用于展示/核验时避免可枚举。
    return f"SF-{order_id:06d}-{secrets.token_hex(8).upper()}"


def existing_code(order: Order) -> str | None:
    return (order.extra_data or {}).get(CODE_FIELD)


async def ensure_registration_code(db: AsyncSession, order: Order) -> str:
    """Generate the Sangfor registration code once; later calls are no-ops."""
    extra = order.extra_data or {}
    code = extra.get(CODE_FIELD)
    if code:
        return code

    code = build_code(order.id)
    order.extra_data = {
        **extra,
        CODE_FIELD: code,
        GENERATED_AT_FIELD: datetime.now(timezone.utc).isoformat(),
    }
    await db.flush()
    return code
