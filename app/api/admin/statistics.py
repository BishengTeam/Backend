from fastapi import APIRouter, Depends

from app.middleware.auth import require_permission
from app.schemas.admin import DashboardResponse
from app.schemas.common import APIResponse, success
from app.services.admin_statistics import AdminStatisticsService

router = APIRouter(prefix="/statistics", tags=["管理后台-数据看板"])


@router.get("/dashboard",
    response_model=APIResponse[DashboardResponse],
    summary="数据看板",
    description="""
管理后台 **首页** 使用。

**页面路径**: `/admin/dashboard`

**使用场景**: 管理员登录后进入后台首页，展示核心数据概览（用户数、订单数、收入等统计指标）
    """,
)
async def dashboard(
    _admin=Depends(require_permission("dashboard:view")),
):
    result = await AdminStatisticsService().dashboard()
    return success(data=result)
