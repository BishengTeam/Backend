from app.core.database import get_db_ctx
from app.core.exceptions import NotFoundException
from app.models.price_config import PriceConfig
from app.schemas.admin_price import AdminPriceCreate, AdminPriceUpdate
from app.schemas.price_config import PriceResponse


class AdminPriceService:

    async def create(self, data: AdminPriceCreate) -> PriceResponse:
        async with get_db_ctx() as db:
            price = PriceConfig(**data.model_dump())
            db.add(price)
            await db.commit()
            await db.refresh(price)
            return PriceResponse.model_validate(price)

    async def update(self, price_id: int, data: AdminPriceUpdate) -> PriceResponse:
        async with get_db_ctx() as db:
            price = await db.get(PriceConfig, price_id)
            if price is None:
                raise NotFoundException("价格配置")
            update_data = data.model_dump(exclude_unset=True)
            for key, value in update_data.items():
                setattr(price, key, value)
            await db.commit()
            await db.refresh(price)
            return PriceResponse.model_validate(price)

    async def deactivate(self, price_id: int) -> None:
        async with get_db_ctx() as db:
            price = await db.get(PriceConfig, price_id)
            if price is None:
                raise NotFoundException("价格配置")
            price.is_active = False
            await db.commit()
