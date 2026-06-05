from sqlalchemy import select

from app.adapter.database import get_db_ctx
from app.port.exceptions import NotFoundException
from app.domain.content.src.index import Banner
from app.schemas.admin_banner import BannerCreate, BannerListItem, BannerUpdate


class AdminBannerService:

    async def list_banners(self) -> list[BannerListItem]:
        async with get_db_ctx() as db:
            stmt = select(Banner).order_by(Banner.sort, Banner.id.desc())
            result = await db.execute(stmt)
            banners = result.scalars().all()
            return [BannerListItem.model_validate(b) for b in banners]

    async def create(self, data: BannerCreate) -> BannerListItem:
        async with get_db_ctx() as db:
            banner = Banner(**data.model_dump())
            db.add(banner)
            await db.commit()
            await db.refresh(banner)
            return BannerListItem.model_validate(banner)

    async def update(self, banner_id: int, data: BannerUpdate) -> BannerListItem:
        async with get_db_ctx() as db:
            banner = await db.get(Banner, banner_id)
            if banner is None:
                raise NotFoundException("Banner")
            update_data = data.model_dump(exclude_unset=True)
            for key, value in update_data.items():
                setattr(banner, key, value)
            await db.commit()
            await db.refresh(banner)
            return BannerListItem.model_validate(banner)

    async def delete(self, banner_id: int) -> None:
        async with get_db_ctx() as db:
            banner = await db.get(Banner, banner_id)
            if banner is None:
                raise NotFoundException("Banner")
            await db.delete(banner)
            await db.commit()

    async def batch_delete(self, banner_ids: list[int]) -> int:
        async with get_db_ctx() as db:
            result = await db.execute(
                select(Banner).where(Banner.id.in_(banner_ids))
            )
            banners = result.scalars().all()
            for banner in banners:
                await db.delete(banner)
            await db.commit()
            return len(banners)
