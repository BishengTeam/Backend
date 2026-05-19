from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessException, ConflictException
from app.models.inventory import InventoryRecord
from app.models.order import Order

INVENTORY_TYPE_CERTIFICATION = "certification"
INVENTORY_LOCK_ACTION = "lock"
INVENTORY_CONFIRM_ACTION = "confirm"
INVENTORY_RELEASE_ACTION = "release"


@dataclass(frozen=True, slots=True)
class InventoryChange:
    inventory_id: int
    before_total_quota: int
    before_available_quota: int
    before_locked_quota: int
    before_sold_quota: int
    after_total_quota: int
    after_available_quota: int
    after_locked_quota: int
    after_sold_quota: int


def _row_int(row: dict, key: str) -> int:
    return int(row[key])


def _change_from_after(
    row: dict,
    *,
    before_available_delta: int = 0,
    before_locked_delta: int = 0,
    before_sold_delta: int = 0,
) -> InventoryChange:
    after_total = _row_int(row, "total_quota")
    after_available = _row_int(row, "available_quota")
    after_locked = _row_int(row, "locked_quota")
    after_sold = _row_int(row, "sold_quota")
    return InventoryChange(
        inventory_id=_row_int(row, "id"),
        before_total_quota=after_total,
        before_available_quota=after_available + before_available_delta,
        before_locked_quota=after_locked + before_locked_delta,
        before_sold_quota=after_sold + before_sold_delta,
        after_total_quota=after_total,
        after_available_quota=after_available,
        after_locked_quota=after_locked,
        after_sold_quota=after_sold,
    )


def add_inventory_record(
    db: AsyncSession,
    *,
    change: InventoryChange,
    order_id: int | None,
    action: str,
    reason: str,
) -> None:
    db.add(
        InventoryRecord(
            inventory_id=change.inventory_id,
            order_id=order_id,
            action=action,
            quantity=1,
            before_total_quota=change.before_total_quota,
            before_available_quota=change.before_available_quota,
            before_locked_quota=change.before_locked_quota,
            before_sold_quota=change.before_sold_quota,
            after_total_quota=change.after_total_quota,
            after_available_quota=change.after_available_quota,
            after_locked_quota=change.after_locked_quota,
            after_sold_quota=change.after_sold_quota,
            reason=reason,
        )
    )


async def lock_certification_inventory(db: AsyncSession, cert_type: str) -> InventoryChange:
    result = await db.execute(
        text(
            """
            UPDATE inventory
            SET available_quota = available_quota - 1,
                locked_quota = locked_quota + 1,
                updated_at = now()
            WHERE inventory_type = :inventory_type
              AND ref_code = :ref_code
              AND is_active = true
              AND available_quota >= 1
            RETURNING id, total_quota, available_quota, locked_quota, sold_quota
            """
        ),
        {
            "inventory_type": INVENTORY_TYPE_CERTIFICATION,
            "ref_code": cert_type,
        },
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise BusinessException("认证报名名额不足")
    return _change_from_after(
        dict(row),
        before_available_delta=1,
        before_locked_delta=-1,
    )


async def confirm_inventory_sale(
    db: AsyncSession,
    order: Order,
    *,
    reason: str = "payment_success",
) -> bool:
    if order.inventory_id is None:
        return False
    result = await db.execute(
        text(
            """
            UPDATE inventory
            SET locked_quota = locked_quota - 1,
                sold_quota = sold_quota + 1,
                updated_at = now()
            WHERE id = :inventory_id
              AND locked_quota >= 1
            RETURNING id, total_quota, available_quota, locked_quota, sold_quota
            """
        ),
        {"inventory_id": order.inventory_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise ConflictException("订单库存锁定状态不允许确认成交")
    change = _change_from_after(
        dict(row),
        before_locked_delta=1,
        before_sold_delta=-1,
    )
    add_inventory_record(
        db,
        change=change,
        order_id=order.id,
        action=INVENTORY_CONFIRM_ACTION,
        reason=reason,
    )
    return True


async def release_inventory_lock(
    db: AsyncSession,
    order: Order,
    *,
    reason: str = "payment_timeout",
) -> bool:
    if order.inventory_id is None:
        return False
    result = await db.execute(
        text(
            """
            UPDATE inventory
            SET available_quota = available_quota + 1,
                locked_quota = locked_quota - 1,
                updated_at = now()
            WHERE id = :inventory_id
              AND locked_quota >= 1
            RETURNING id, total_quota, available_quota, locked_quota, sold_quota
            """
        ),
        {"inventory_id": order.inventory_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise ConflictException("订单库存锁定状态不允许释放")
    change = _change_from_after(
        dict(row),
        before_available_delta=-1,
        before_locked_delta=1,
    )
    add_inventory_record(
        db,
        change=change,
        order_id=order.id,
        action=INVENTORY_RELEASE_ACTION,
        reason=reason,
    )
    return True
