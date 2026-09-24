"""Repository interfaces for points-mall domain (ISP + DIP).

Split into two narrow interfaces:
- PointsMallItemRepository: template CRUD (used by admin service)
- PointsMallRedemptionRepository: user redemption records (used by user/checkout service)
"""

from typing import Protocol

from app.domain.points_mall.src import PointsMallItem, PointsMallRedemption


class PointsMallItemRepository(Protocol):
    """Persistence interface for coupon templates only."""

    async def get_item(self, item_id: int) -> PointsMallItem | None: ...

    async def get_item_for_update(self, item_id: int) -> PointsMallItem | None: ...

    async def list_active_items(self) -> list[PointsMallItem]: ...

    async def list_all_items(
        self, *, offset: int, limit: int
    ) -> tuple[list[PointsMallItem], int]: ...

    async def save_item(self, item: PointsMallItem) -> PointsMallItem: ...


class PointsMallRedemptionRepository(Protocol):
    """Persistence interface for user coupon redemptions only."""

    async def get_by_code(
        self, coupon_code: str, user_id: int
    ) -> PointsMallRedemption | None: ...

    async def get_for_update(
        self, coupon_code: str, user_id: int
    ) -> PointsMallRedemption | None: ...

    async def list_by_user(self, user_id: int) -> list[PointsMallRedemption]: ...

    async def count_by_user_and_item(self, user_id: int, item_id: int) -> int: ...

    async def save(self, redemption: PointsMallRedemption) -> PointsMallRedemption: ...
