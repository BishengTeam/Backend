from sqlalchemy import and_, func, select

from app.adapter.database import get_db_ctx
from app.port.exceptions import NotFoundException
from app.models.collection import Collection
from app.schemas.collection import CollectionCreate, CollectionResponse
from app.schemas.common import PaginatedData


class CollectionService:
    async def list_collections(
        self,
        user_id: int,
        *,
        target_type: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedData[CollectionResponse]:
        async with get_db_ctx() as db:
            base = select(Collection).where(Collection.user_id == user_id)
            if target_type is not None:
                base = base.where(Collection.target_type == target_type)
            count_stmt = select(func.count()).select_from(base.subquery())
            total = (await db.execute(count_stmt)).scalar() or 0
            stmt = (
                base.order_by(Collection.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            result = await db.execute(stmt)
            collections = result.scalars().all()
            return PaginatedData[CollectionResponse](
                items=[CollectionResponse.model_validate(c) for c in collections],
                total=total,
                page=page,
                page_size=page_size,
            )

    async def add_collection(
        self, user_id: int, data: CollectionCreate
    ) -> CollectionResponse:
        async with get_db_ctx() as db:
            existing = (
                await db.execute(
                    select(Collection).where(
                        and_(
                            Collection.user_id == user_id,
                            Collection.target_type == data.target_type,
                            Collection.target_id == data.target_id,
                        )
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return CollectionResponse.model_validate(existing)
            collection = Collection(
                user_id=user_id,
                target_type=data.target_type,
                target_id=data.target_id,
            )
            db.add(collection)
            await db.commit()
            await db.refresh(collection)
            return CollectionResponse.model_validate(collection)

    async def remove_collection(self, user_id: int, collection_id: int) -> None:
        async with get_db_ctx() as db:
            collection = await db.get(Collection, collection_id)
            if collection is None:
                raise NotFoundException("收藏记录")
            if collection.user_id != user_id:
                raise NotFoundException("收藏记录")
            await db.delete(collection)
            await db.commit()
