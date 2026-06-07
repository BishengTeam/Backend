from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import require_permission
from app.schemas.admin_training import AdminTrainingCreate, AdminTrainingListItem, AdminTrainingUpdate
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.admin_training import AdminTrainingService

router = APIRouter(prefix="/training", tags=["管理后台-培训管理"])


@router.get("",
    response_model=APIResponse[PaginatedData[AdminTrainingListItem]],
    summary="培训列表",
    description="""
管理后台 **培训管理** 页面使用。

**页面路径**: `/admin/training`

**使用场景**: 页面加载时获取培训列表，支持关键词搜索

**查询参数**:
- `keyword`: 按培训标题模糊搜索
- `page`: 页码，从 1 开始
- `page_size`: 每页条数，默认 20，最大 100

**响应**: 分页培训数据，包含封面、价格、讲师等信息
    """,
)
async def list_trainings(
    keyword: str | None = Query(None, description="按标题关键词模糊搜索"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(require_permission("content:list")),
) -> APIResponse[PaginatedData[AdminTrainingListItem]]:
    """分页查询培训列表，支持按标题关键词模糊搜索"""
    result = await AdminTrainingService().list_trainings(keyword, page, page_size)
    return success(data=result)


@router.post("",
    response_model=APIResponse[AdminTrainingListItem],
    summary="新增培训",
    description="""
管理后台 **培训管理** 页面使用。

**页面路径**: `/admin/training`

**使用场景**: 新增/编辑弹窗提交，创建新培训

**请求体**: 培训名称、封面、价格、讲师等信息

**响应**: 新创建的培训详情
    """,
)
async def create_training(
    body: AdminTrainingCreate,
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[AdminTrainingListItem]:
    """创建新培训"""
    result = await AdminTrainingService().create(body)
    return success(data=result)


@router.put("/{training_id}",
    response_model=APIResponse[AdminTrainingListItem],
    summary="编辑培训",
    description="""
管理后台 **培训管理** 页面使用。

**页面路径**: `/admin/training`

**使用场景**: 新增/编辑弹窗提交，更新已有培训信息

**路径参数**:
- `training_id`: 培训 ID

**请求体**: 要更新的培训字段

**响应**: 更新后的培训详情
    """,
)
async def update_training(
    body: AdminTrainingUpdate,
    training_id: int = Path(..., description="培训 ID"),
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[AdminTrainingListItem]:
    """更新指定培训信息"""
    result = await AdminTrainingService().update(training_id, body)
    return success(data=result)


@router.delete("/{training_id}",
    response_model=APIResponse,
    summary="下架培训",
    description="""
管理后台 **培训管理** 页面使用。

**页面路径**: `/admin/training`

**使用场景**: 下架指定培训（软删除）

**路径参数**:
- `training_id`: 培训 ID

**响应**: 操作结果消息
    """,
)
async def delete_training(
    training_id: int = Path(..., description="培训 ID"),
    _admin=Depends(require_permission("content:write")),
):
    """下架指定培训"""
    await AdminTrainingService().deactivate(training_id)
    return success(message="培训已下架")