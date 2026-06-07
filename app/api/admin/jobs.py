from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import require_permission
from app.schemas.admin_job import AdminJobCreate, AdminJobListItem, AdminJobUpdate
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.admin_job import AdminJobService

router = APIRouter(prefix="/jobs", tags=["管理后台-就业管理"])


@router.get("", response_model=APIResponse[PaginatedData[AdminJobListItem]])
async def list_jobs(
    keyword: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin=Depends(require_permission("content:list")),
):
    result = await AdminJobService().list_jobs(keyword, page, page_size)
    return success(data=result)


@router.post("", response_model=APIResponse[AdminJobListItem])
async def create_job(
    body: AdminJobCreate,
    _admin=Depends(require_permission("content:write")),
):
    result = await AdminJobService().create(body)
    return success(data=result)


@router.put("/{job_id}", response_model=APIResponse[AdminJobListItem])
async def update_job(
    body: AdminJobUpdate,
    job_id: int = Path(...),
    _admin=Depends(require_permission("content:write")),
):
    result = await AdminJobService().update(job_id, body)
    return success(data=result)


@router.delete("/{job_id}", response_model=APIResponse)
async def delete_job(
    job_id: int = Path(...),
    _admin=Depends(require_permission("content:write")),
):
    await AdminJobService().deactivate(job_id)
    return success(message="岗位已下架")
