from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import PlainTextResponse

from app.middleware.auth import require_permission
from app.schemas.admin_activity import AdminActivityCreate, AdminActivityListItem, AdminActivityUpdate
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.activity import ActivityService
from app.services.admin_activity import AdminActivityService

router = APIRouter(prefix="/activities", tags=["管理后台-培训管理"])


@router.get("", response_model=APIResponse[PaginatedData[AdminActivityListItem]])
async def list_activities(
    keyword: str | None = Query(None, description="按标题关键词模糊搜索"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(require_permission("content:list")),
) -> APIResponse[PaginatedData[AdminActivityListItem]]:
    result = await AdminActivityService().list_activities(keyword, page, page_size)
    return success(data=result)


@router.post("", response_model=APIResponse[AdminActivityListItem])
async def create_activity(
    body: AdminActivityCreate,
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[AdminActivityListItem]:
    result = await AdminActivityService().create(body)
    return success(data=result)


@router.put("/{activity_id}", response_model=APIResponse[AdminActivityListItem])
async def update_activity(
    body: AdminActivityUpdate,
    activity_id: int = Path(..., description="培训活动 ID"),
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[AdminActivityListItem]:
    result = await AdminActivityService().update(activity_id, body)
    return success(data=result)


@router.delete("/{activity_id}", response_model=APIResponse)
async def delete_activity(
    activity_id: int = Path(..., description="培训活动 ID"),
    _admin=Depends(require_permission("content:write")),
):
    await AdminActivityService().deactivate(activity_id)
    return success(message="培训活动已下架")


@router.get("/export", response_class=PlainTextResponse)
async def export_registrations(
    _admin=Depends(require_permission("content:read")),
):
    """导出活动报名 CSV"""
    csv_content = await ActivityService().export_csv()
    return PlainTextResponse(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=activity_registrations.csv"},
    )
