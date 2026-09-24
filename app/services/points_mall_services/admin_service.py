"""Admin-side coupon template management."""

from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.domain.points_mall.src import PointsMallItem
from app.port.exceptions import BusinessException, NotFoundException
from app.schemas.points_mall import (
    PointsMallItemCreate,
    PointsMallItemResponse,
    PointsMallItemUpdate,
)


def _remaining_stock(item: PointsMallItem) -> int:
    if item.total_stock == 0:
        return -1
    return max(0, item.total_stock - item.total_redeemed)


def _item_response(item: PointsMallItem) -> PointsMallItemResponse:
    return PointsMallItemResponse(
        id=item.id,
        name=item.name,
        description=item.description,
        discount_type=item.discount_type,
        discount_value=item.discount_value,
        scope_type=item.scope_type,
        scope_value=item.scope_value,
        min_order_amount_cents=item.min_order_amount_cents,
        points_cost=item.points_cost,
        total_stock=item.total_stock,
        total_redeemed=item.total_redeemed,
        remaining_stock=_remaining_stock(item),
        per_user_limit=item.per_user_limit,
        validity_type=item.validity_type,
        validity_days=item.validity_days,
        valid_until=item.valid_until,
        is_active=item.is_active,
        sort_order=item.sort_order,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


class AdminPointsMallService:
    """CRUD and lifecycle management for coupon templates."""

    async def create(self, data: PointsMallItemCreate) -> PointsMallItemResponse:
        self._validate(data)
        async with get_db_ctx() as db:
            item = PointsMallItem(**data.model_dump())
            db.add(item)
            await db.commit()
            await db.refresh(item)
            return _item_response(item)

    async def update(self, item_id: int, data: PointsMallItemUpdate) -> PointsMallItemResponse:
        async with get_db_ctx() as db:
            item = await db.get(PointsMallItem, item_id)
            if item is None:
                raise NotFoundException("积分商城商品")
            updates = data.model_dump(exclude_unset=True)
            self._validate_updates(updates)
            for key, value in updates.items():
                setattr(item, key, value)
            await db.commit()
            await db.refresh(item)
            return _item_response(item)

    async def list_items(
        self, *, page: int = 1, page_size: int = 20
    ) -> tuple[list[PointsMallItemResponse], int]:
        async with get_db_ctx() as db:
            total = await db.scalar(
                select(func.count()).select_from(PointsMallItem)
            ) or 0
            rows = (
                await db.execute(
                    select(PointsMallItem)
                    .order_by(PointsMallItem.sort_order.desc(), PointsMallItem.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            return [_item_response(row) for row in rows], total

    async def deactivate(self, item_id: int) -> None:
        async with get_db_ctx() as db:
            item = await db.get(PointsMallItem, item_id)
            if item is None:
                raise NotFoundException("积分商城商品")
            item.is_active = False
            await db.commit()

    @staticmethod
    def _validate(data: PointsMallItemCreate) -> None:
        if data.validity_type == "days" and not data.validity_days:
            raise BusinessException("固定天数模式必须指定天数")
        if data.validity_type == "fixed_date" and not data.valid_until:
            raise BusinessException("固定日期模式必须指定截止时间")
        if data.scope_type != "global" and not data.scope_value:
            raise BusinessException("指定范围必须填写范围值")
        if data.discount_type == "percent" and data.discount_value >= 100:
            raise BusinessException("百分比折扣不能大于或等于100")

    @staticmethod
    def _validate_updates(updates: dict) -> None:
        if updates.get("validity_type") == "days" and not updates.get("validity_days"):
            raise BusinessException("固定天数模式必须指定天数")
        if updates.get("validity_type") == "fixed_date" and not updates.get("valid_until"):
            raise BusinessException("固定日期模式必须指定截止时间")
