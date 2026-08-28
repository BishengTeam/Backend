from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import require_permission
from app.schemas.admin_competition import (
    AdminCompetitionCreate,
    AdminCompetitionListItem,
    AdminCompetitionRegistrationItem,
    AdminCompetitionUpdate,
)
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.admin_competition import AdminCompetitionService

router = APIRouter(prefix="/competitions", tags=["管理后台-竞赛管理"])


@router.get("",
    response_model=APIResponse[PaginatedData[AdminCompetitionListItem]],
    summary="赛事列表",
    description="""
管理后台 **竞赛管理** 页面使用。

**页面路径**: `/operations/competitions`

**查询参数**: keyword（按名称模糊）/ page / page_size

**响应**: 分页赛事数据（含赛道与报名统计）
    """,
)
async def list_competitions(
    keyword: str | None = Query(None, description="按赛事名称模糊搜索"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(require_permission("competition:list")),
) -> APIResponse[PaginatedData[AdminCompetitionListItem]]:
    result = await AdminCompetitionService().list_competitions(
        keyword, page, page_size
    )
    return success(data=result)


@router.post("",
    response_model=APIResponse[AdminCompetitionListItem],
    summary="创建赛事",
    description="创建赛事（含赛道列表），权限：competition:write",
)
async def create_competition(
    body: AdminCompetitionCreate,
    _admin=Depends(require_permission("competition:write")),
) -> APIResponse[AdminCompetitionListItem]:
    result = await AdminCompetitionService().create(body)
    return success(data=result)


@router.put("/{competition_id}",
    response_model=APIResponse[AdminCompetitionListItem],
    summary="更新赛事",
    description="更新赛事；tracks 传入时全量替换赛道",
)
async def update_competition(
    body: AdminCompetitionUpdate,
    competition_id: int = Path(..., ge=1, description="赛事 ID"),
    _admin=Depends(require_permission("competition:write")),
) -> APIResponse[AdminCompetitionListItem]:
    result = await AdminCompetitionService().update(competition_id, body)
    return success(data=result)


@router.delete("/{competition_id}",
    response_model=APIResponse,
    summary="删除赛事",
    description="删除赛事及其赛道（级联）",
)
async def delete_competition(
    competition_id: int = Path(..., ge=1, description="赛事 ID"),
    _admin=Depends(require_permission("competition:write")),
) -> APIResponse:
    await AdminCompetitionService().delete(competition_id)
    return success(message="赛事已删除")


@router.get("/{competition_id}/registrations",
    response_model=APIResponse[PaginatedData[AdminCompetitionRegistrationItem]],
    summary="报名名单",
    description="按赛事（可按赛道）查看报名名单",
)
async def list_registrations(
    competition_id: int = Path(..., ge=1, description="赛事 ID"),
    track_id: int | None = Query(None, ge=1, description="赛道 ID 筛选"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(require_permission("competition:list")),
) -> APIResponse[PaginatedData[AdminCompetitionRegistrationItem]]:
    result = await AdminCompetitionService().list_registrations(
        competition_id, track_id, page, page_size
    )
    return success(data=result)
