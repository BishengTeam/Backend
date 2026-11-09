from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.port.exceptions import NotFoundException
from app.domain.content.src.index import Training
from app.schemas.admin_training import AdminTrainingCreate, AdminTrainingListItem, AdminTrainingUpdate
from app.schemas.common import PaginatedData
from app.services.recycle_bin import restore_row, soft_delete_row


class AdminTrainingService:

    _list_columns = (
        Training.id,
        Training.title,
        Training.cover_url,
        Training.location,
        Training.start_time,
        Training.end_time,
        Training.max_participants,
        Training.cert_type,
        Training.price,
        Training.is_active,
        Training.created_at,
    )

    async def list_trainings(
        self, keyword: str | None, page: int, page_size: int
    ) -> PaginatedData[AdminTrainingListItem]:
        async with get_db_ctx() as db:
            base = select(*self._list_columns).where(Training.deleted_at.is_(None))
            if keyword:
                base = base.where(Training.title.ilike(f"%{keyword}%"))
            count_stmt = select(func.count()).select_from(base.subquery())
            total = (await db.execute(count_stmt)).scalar() or 0
            stmt = base.order_by(Training.id.desc()).offset((page - 1) * page_size).limit(page_size)
            result = await db.execute(stmt)
            items = result.all()
            return PaginatedData[AdminTrainingListItem](
                items=[AdminTrainingListItem.model_validate(a) for a in items],
                total=total,
                page=page,
                page_size=page_size,
            )

    async def create(self, data: AdminTrainingCreate) -> AdminTrainingListItem:
        async with get_db_ctx() as db:
            training = Training(**data.model_dump())
            db.add(training)
            await db.commit()
            await db.refresh(training)
            return AdminTrainingListItem.model_validate(training)

    async def update(self, training_id: int, data: AdminTrainingUpdate) -> AdminTrainingListItem:
        async with get_db_ctx() as db:
            training = await db.get(Training, training_id)
            if training is None or training.deleted_at is not None:
                raise NotFoundException("培训")
            update_data = data.model_dump(exclude_unset=True)
            for key, value in update_data.items():
                setattr(training, key, value)
            await db.commit()
            await db.refresh(training)
            return AdminTrainingListItem.model_validate(training)

    async def delete(self, training_id: int) -> None:
        """软删除进回收站，30 自然日内可恢复。"""
        async with get_db_ctx() as db:
            training = await db.get(Training, training_id)
            if training is None or training.deleted_at is not None:
                raise NotFoundException("培训")
            soft_delete_row(training)
            await db.commit()

    async def restore(self, training_id: int) -> None:
        """从回收站恢复。"""
        async with get_db_ctx() as db:
            training = await db.get(Training, training_id)
            if training is None or training.deleted_at is None:
                raise NotFoundException("培训")
            restore_row(training)
            await db.commit()
