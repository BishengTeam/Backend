from sqlalchemy import select

from app.core.database import get_db_ctx
from app.models.price_config import PriceConfig
from app.schemas.price_config import PriceFilter, PriceResponse


class PriceConfigService:

    async def list_prices(self, filters: PriceFilter | None = None) -> list[PriceResponse]:
        async with get_db_ctx() as db:
            stmt = select(PriceConfig).where(PriceConfig.is_active == True)
            if filters and filters.cert_type:
                stmt = stmt.where(PriceConfig.cert_type == filters.cert_type)
            if filters and filters.user_type:
                stmt = stmt.where(PriceConfig.user_type == filters.user_type)
            result = await db.execute(stmt.order_by(PriceConfig.id))
            prices = result.scalars().all()
            return [PriceResponse.model_validate(p) for p in prices]
