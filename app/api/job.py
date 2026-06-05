from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.job import JobApplicationResponse, JobResponse
from app.services.job import JobService

router = APIRouter(prefix="/jobs", tags=["岗位"])


@router.get("", response_model=APIResponse[PaginatedData[JobResponse]])
async def list_jobs(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
) -> APIResponse[PaginatedData[JobResponse]]:
    """招聘岗位列表"""
    result = await JobService().list_jobs(page, page_size)
    return success(data=result)


@router.post("/{job_id}/apply", response_model=APIResponse[JobApplicationResponse])
async def apply_job(
    job_id: int = Path(..., ge=1, description="岗位 ID"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[JobApplicationResponse]:
    """投递岗位"""
    result = await JobService().apply(current_user.id, job_id)
    return success(data=JobApplicationResponse.model_validate(result))