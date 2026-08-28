from sqlalchemy import func, or_, select

from app.adapter.database import get_db_ctx
from app.port.exceptions import NotFoundException
from app.domain.content.src.index import Banner
from app.schemas.admin_banner import BannerCreate, BannerListItem, BannerUpdate
from app.schemas.common import PaginatedData


class AdminBannerService:

    async def list_banners(
        self,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedData[BannerListItem]:
        async with get_db_ctx() as db:
            base = select(Banner)
            if keyword:
                pattern = f"%{keyword}%"
                base = base.where(
                    or_(
                        Banner.image_url.ilike(pattern),
                        Banner.jump_link.ilike(pattern),
                    )
                )
            total = (
                await db.execute(
                    select(func.count()).select_from(base.subquery())
                )
            ).scalar() or 0
            rows = (
                await db.execute(
                    base.order_by(Banner.sort, Banner.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            return PaginatedData(
                items=[BannerListItem.model_validate(b) for b in rows],
                total=total,
                page=page,
                page_size=page_size,
            )

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
