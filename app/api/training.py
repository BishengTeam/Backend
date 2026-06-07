from fastapi import APIRouter, Query

from app.adapter.database import get_db_ctx
from app.domain.content.src.index import Training
from app.schemas.admin_training import AdminTrainingListItem
from app.schemas.common import APIResponse, PaginatedData, success
from sqlalchemy import func, select

router = APIRouter(prefix="/training", tags=["培训"])


@router.get("", response_model=APIResponse[PaginatedData[AdminTrainingListItem]])
async def list_trainings(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(10, ge=1, le=100, description="每页数量"),
) -> APIResponse[PaginatedData[AdminTrainingListItem]]:
    """公开培训列表（仅展示已上架的培训）"""
    async with get_db_ctx() as db:
        base = select(Training).where(Training.is_active == True)
        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await db.execute(count_stmt)).scalar() or 0
        stmt = base.order_by(Training.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        result = await db.execute(stmt)
        items = result.scalars().all()
        return success(data=PaginatedData[AdminTrainingListItem](
            items=[AdminTrainingListItem.model_validate(t) for t in items],
            total=total,
            page=page,
            page_size=page_size,
        ))
