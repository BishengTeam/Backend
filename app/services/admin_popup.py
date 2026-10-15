from sqlalchemy import func, or_, select

from app.adapter.database import get_db_ctx
from app.port.exceptions import NotFoundException
from app.domain.content.src.index import Popup
from app.schemas.admin_popup import PopupCreate, PopupListItem, PopupUpdate
from app.schemas.common import PaginatedData


class AdminPopupService:

    async def list_popups(
        self,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedData[PopupListItem]:
        async with get_db_ctx() as db:
            base = select(Popup)
            if keyword:
                pattern = f"%{keyword}%"
                base = base.where(
                    or_(
                        Popup.image_url.ilike(pattern),
                        Popup.jump_link.ilike(pattern),
                    )
                )
            total = (
                await db.execute(
                    select(func.count()).select_from(base.subquery())
                )
            ).scalar() or 0
            rows = (
                await db.execute(
                    base.order_by(Popup.sort, Popup.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            return PaginatedData(
                items=[PopupListItem.model_validate(p) for p in rows],
                total=total,
                page=page,
                page_size=page_size,
            )

    async def create(self, data: PopupCreate) -> PopupListItem:
        async with get_db_ctx() as db:
            popup = Popup(**data.model_dump())
            db.add(popup)
            await db.commit()
            await db.refresh(popup)
            return PopupListItem.model_validate(popup)

    async def update(self, popup_id: int, data: PopupUpdate) -> PopupListItem:
        async with get_db_ctx() as db:
            popup = await db.get(Popup, popup_id)
            if popup is None:
                raise NotFoundException("Popup")
            update_data = data.model_dump(exclude_unset=True)
            for key, value in update_data.items():
                setattr(popup, key, value)
            await db.commit()
            await db.refresh(popup)
            return PopupListItem.model_validate(popup)

    async def delete(self, popup_id: int) -> None:
        async with get_db_ctx() as db:
            popup = await db.get(Popup, popup_id)
            if popup is None:
                raise NotFoundException("Popup")
            await db.delete(popup)
            await db.commit()

    async def batch_delete(self, popup_ids: list[int]) -> int:
        async with get_db_ctx() as db:
            result = await db.execute(
                select(Popup).where(Popup.id.in_(popup_ids))
            )
            popups = result.scalars().all()
            for popup in popups:
                await db.delete(popup)
            await db.commit()
            return len(popups)
