from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import require_permission
from app.schemas.admin_training import AdminTrainingCreate, AdminTrainingListItem, AdminTrainingUpdate
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.admin_training import AdminTrainingService

router = APIRouter(prefix="/training", tags=["管理后台-培训管理"])


@router.get("", response_model=APIResponse[PaginatedData[AdminTrainingListItem]])
async def list_trainings(
    keyword: str | None = Query(None, description="按标题关键词模糊搜索"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(require_permission("content:list")),
) -> APIResponse[PaginatedData[AdminTrainingListItem]]:
    result = await AdminTrainingService().list_trainings(keyword, page, page_size)
    return success(data=result)


@router.post("", response_model=APIResponse[AdminTrainingListItem])
async def create_training(
    body: AdminTrainingCreate,
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[AdminTrainingListItem]:
    result = await AdminTrainingService().create(body)
    return success(data=result)


@router.put("/{training_id}", response_model=APIResponse[AdminTrainingListItem])
async def update_training(
    body: AdminTrainingUpdate,
    training_id: int = Path(..., description="培训 ID"),
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[AdminTrainingListItem]:
    result = await AdminTrainingService().update(training_id, body)
    return success(data=result)


@router.delete("/{training_id}", response_model=APIResponse)
async def delete_training(
    training_id: int = Path(..., description="培训 ID"),
    _admin=Depends(require_permission("content:write")),
):
    await AdminTrainingService().deactivate(training_id)
    return success(message="培训已下架")
