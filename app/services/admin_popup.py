from sqlalchemy import func, or_, select

from app.adapter.database import get_db_ctx
from app.port.exceptions import NotFoundException
from app.domain.content.src.index import Popup
from app.schemas.admin_popup import PopupCreate, PopupListItem, PopupUpdate
from app.schemas.common import PaginatedData
from app.services.recycle_bin import restore_row, soft_delete_row


class AdminPopupService:

    async def list_popups(
        self,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedData[PopupListItem]:
        async with get_db_ctx() as db:
            base = select(Popup).where(Popup.deleted_at.is_(None))
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
            if popup is None or popup.deleted_at is not None:
                raise NotFoundException("Popup")
            update_data = data.model_dump(exclude_unset=True)
            for key, value in update_data.items():
                setattr(popup, key, value)
            await db.commit()
            await db.refresh(popup)
            return PopupListItem.model_validate(popup)

    async def delete(self, popup_id: int) -> None:
        """软删除进回收站，30 自然日内可恢复。"""
        async with get_db_ctx() as db:
            popup = await db.get(Popup, popup_id)
            if popup is None or popup.deleted_at is not None:
                raise NotFoundException("Popup")
            soft_delete_row(popup)
            await db.commit()

    async def batch_delete(self, popup_ids: list[int]) -> int:
        async with get_db_ctx() as db:
            result = await db.execute(
                select(Popup).where(Popup.id.in_(popup_ids), Popup.deleted_at.is_(None))
            )
            popups = result.scalars().all()
            for popup in popups:
                soft_delete_row(popup)
            await db.commit()
            return len(popups)

    async def restore(self, popup_id: int) -> None:
        """从回收站恢复。"""
        async with get_db_ctx() as db:
            popup = await db.get(Popup, popup_id)
            if popup is None or popup.deleted_at is None:
                raise NotFoundException("Popup")
            restore_row(popup)
            await db.commit()
