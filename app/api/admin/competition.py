from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from app.middleware.auth import require_permission
from app.services.admin_competition import AdminCompetitionService

router = APIRouter(prefix="/competition", tags=["管理后台-竞赛导出"])


@router.get("/export", response_class=PlainTextResponse)
async def export_competition(
    _admin=Depends(require_permission("content:write")),
):
    csv_content = await AdminCompetitionService().export_csv()
    return PlainTextResponse(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=competition_export.csv"},
    )
