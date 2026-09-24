"""Repository interface for points-mall domain (ISP + DIP)."""

from typing import Protocol

from app.domain.points_mall.src import PointsMallItem, PointsMallRedemption


class PointsMallRepository(Protocol):
    """Persistence interface for coupon templates and redemptions."""

    # Templates
    async def get_item(self, item_id: int) -> PointsMallItem | None: ...

    async def get_item_for_update(self, item_id: int) -> PointsMallItem | None: ...

    async def list_active_items(self) -> list[PointsMallItem]: ...

    async def list_all_items(self, *, offset: int, limit: int) -> tuple[list[PointsMallItem], int]: ...

    async def save_item(self, item: PointsMallItem) -> PointsMallItem: ...

    # Redemptions
    async def get_redemption_by_code(
        self, coupon_code: str, user_id: int
    ) -> PointsMallRedemption | None: ...

    async def get_redemption_for_update(
        self, coupon_code: str, user_id: int
    ) -> PointsMallRedemption | None: ...

    async def list_user_redemptions(self, user_id: int) -> list[PointsMallRedemption]: ...

    async def count_user_redemptions(self, user_id: int, item_id: int) -> int: ...

    async def save_redemption(self, redemption: PointsMallRedemption) -> PointsMallRedemption: ...
