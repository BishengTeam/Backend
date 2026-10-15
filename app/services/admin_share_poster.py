from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.port.exceptions import NotFoundException
from app.domain.content.src.index import SharePoster
from app.schemas.admin_share_poster import SharePosterCreate, SharePosterListItem, SharePosterUpdate
from app.schemas.common import PaginatedData


class AdminSharePosterService:

    async def list_posters(
        self,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedData[SharePosterListItem]:
        async with get_db_ctx() as db:
            base = select(SharePoster)
            total = (
                await db.execute(
                    select(func.count()).select_from(base.subquery())
                )
            ).scalar() or 0
            rows = (
                await db.execute(
                    base.order_by(SharePoster.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            return PaginatedData(
                items=[SharePosterListItem.model_validate(p) for p in rows],
                total=total,
                page=page,
                page_size=page_size,
            )

    async def create(self, data: SharePosterCreate) -> SharePosterListItem:
        async with get_db_ctx() as db:
            poster = SharePoster(**data.model_dump())
            db.add(poster)
            await db.commit()
            await db.refresh(poster)
            return SharePosterListItem.model_validate(poster)

    async def update(self, poster_id: int, data: SharePosterUpdate) -> SharePosterListItem:
        async with get_db_ctx() as db:
            poster = await db.get(SharePoster, poster_id)
            if poster is None:
                raise NotFoundException("SharePoster")
            update_data = data.model_dump(exclude_unset=True)
            for key, value in update_data.items():
                setattr(poster, key, value)
            await db.commit()
            await db.refresh(poster)
            return SharePosterListItem.model_validate(poster)

    async def delete(self, poster_id: int) -> None:
        async with get_db_ctx() as db:
            poster = await db.get(SharePoster, poster_id)
            if poster is None:
                raise NotFoundException("SharePoster")
            await db.delete(poster)
            await db.commit()

    async def batch_delete(self, poster_ids: list[int]) -> int:
        async with get_db_ctx() as db:
            result = await db.execute(
                select(SharePoster).where(SharePoster.id.in_(poster_ids))
            )
            posters = result.scalars().all()
            for poster in posters:
                await db.delete(poster)
            await db.commit()
            return len(posters)
