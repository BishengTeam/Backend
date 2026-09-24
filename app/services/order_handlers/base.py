"""Protocol for order type handlers — Open/Closed Principle."""

from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.order import OrderCreate


class OrderHandler(Protocol):
    """Each order type implements this interface.

    Adding a new product type means adding a new handler class,
    not modifying existing conditional logic.
    """

    order_kind: str

    async def validate(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        data: OrderCreate,
    ) -> int:
        """Validate the request and return the price in cents."""
        ...

    async def lock_inventory(
        self,
        db: AsyncSession,
        *,
        product_type: str,
    ) -> tuple[int | None, object]:
        """Lock inventory if applicable. Returns (inventory_id, change)."""
        ...
