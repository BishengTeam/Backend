"""Course order handler — preserves the specific error message."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.port.exceptions import BusinessException
from app.schemas.order import OrderCreate
from app.services.order_handlers.registry import order_handler_registry


@order_handler_registry.register("course")
class CourseOrderHandler:
    """Course purchases must use the dedicated course purchase API."""

    order_kind = "course"

    async def validate(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        data: OrderCreate,
    ) -> int:
        raise BusinessException("课程订单请使用课程购买接口创建")

    async def lock_inventory(
        self,
        db: AsyncSession,
        *,
        product_type: str,
    ) -> tuple[int | None, object]:
        return None, None
