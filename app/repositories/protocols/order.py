"""Repository interface for order domain (ISP + DIP)."""

from datetime import datetime
from typing import Protocol

from app.domain.order.src.index import Order


class OrderRepository(Protocol):
    """Persistence interface for orders.

    Services depend on this abstraction, not on SQLAlchemy directly.
    """

    async def get_by_id(self, order_id: int) -> Order | None: ...

    async def get_by_id_for_update(self, order_id: int, user_id: int) -> Order | None: ...

    async def list_by_user(
        self,
        user_id: int,
        *,
        status: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Order], int]: ...

    async def save(self, order: Order) -> Order: ...
