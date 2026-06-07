from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from app.middleware.auth import require_permission
from app.services.activity import ActivityService

router = APIRouter(prefix="/activities", tags=["管理后台-培训管理"])


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
