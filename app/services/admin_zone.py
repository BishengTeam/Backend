from sqlalchemy import func, select

from app.core.database import get_db_ctx
from app.core.exceptions import NotFoundException
from app.models.zone import Zone
from app.schemas.admin_zone import AdminZoneCreate, AdminZoneListItem, AdminZoneUpdate
from app.schemas.common import PaginatedData


class AdminZoneService:

    _list_columns = (
        Zone.id,
        Zone.zone_type,
        Zone.title,
        Zone.cover_url,
        Zone.description,
        Zone.link_url,
        Zone.sort_order,
        Zone.is_active,
        Zone.created_at,
    )

    async def list_zones(
        self, page: int, page_size: int
    ) -> PaginatedData[AdminZoneListItem]:
        async with get_db_ctx() as db:
            base = select(*self._list_columns)
            count_stmt = select(func.count()).select_from(Zone)
            total = (await db.execute(count_stmt)).scalar() or 0
            stmt = base.order_by(Zone.zone_type, Zone.sort_order, Zone.id.desc()).offset(
                (page - 1) * page_size
            ).limit(page_size)
            result = await db.execute(stmt)
            zones = result.all()
            return PaginatedData[AdminZoneListItem](
                items=[AdminZoneListItem.model_validate(z) for z in zones],
                total=total,
                page=page,
                page_size=page_size,
            )

    async def create(self, data: AdminZoneCreate) -> AdminZoneListItem:
        async with get_db_ctx() as db:
            zone = Zone(**data.model_dump())
            db.add(zone)
            await db.commit()
            await db.refresh(zone)
            return AdminZoneListItem.model_validate(zone)

    async def update(self, zone_id: int, data: AdminZoneUpdate) -> AdminZoneListItem:
        async with get_db_ctx() as db:
            zone = await db.get(Zone, zone_id)
            if zone is None:
                raise NotFoundException("专区内容")
            update_data = data.model_dump(exclude_unset=True)
            for key, value in update_data.items():
                setattr(zone, key, value)
            await db.commit()
            await db.refresh(zone)
            return AdminZoneListItem.model_validate(zone)

    async def deactivate(self, zone_id: int) -> None:
        async with get_db_ctx() as db:
            zone = await db.get(Zone, zone_id)
            if zone is None:
                raise NotFoundException("专区内容")
            zone.is_active = False
            await db.commit()
