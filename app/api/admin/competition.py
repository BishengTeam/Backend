from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from app.middleware.auth import require_permission
from app.services.admin_competition import AdminCompetitionService

router = APIRouter(prefix="/competition", tags=["管理后台-竞赛导出"])


@router.get("/export",
    response_class=PlainTextResponse,
    summary="导出竞赛数据",
    description="""
管理后台 **竞赛管理** 页面使用。

**页面路径**: `/admin/competition`

**使用场景**: 管理员导出竞赛报名数据为 CSV 文件

**响应**: CSV 文件下载

**认证**: 需 `competition:list` 权限
    """,
)
async def export_competition(
    _admin=Depends(require_permission("competition:list")),
):
    csv_content = await AdminCompetitionService().export_csv()
    return PlainTextResponse(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=competition_export.csv"},
    )