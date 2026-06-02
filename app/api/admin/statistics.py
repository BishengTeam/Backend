from fastapi import APIRouter, Depends

from app.middleware.auth import require_permission
from app.schemas.common import APIResponse, success
from app.services.admin_statistics import AdminStatisticsService

router = APIRouter(prefix="/statistics", tags=["管理后台-数据看板"])


@router.get("/dashboard", response_model=APIResponse)
async def dashboard(
    _admin=Depends(require_permission("dashboard:view")),
):
    result = await AdminStatisticsService().dashboard()
    return success(data=result)
