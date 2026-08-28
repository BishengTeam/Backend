from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import require_permission
from app.schemas.admin_job import AdminJobCreate, AdminJobListItem, AdminJobUpdate
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.admin_job import AdminJobService

router = APIRouter(prefix="/jobs", tags=["管理后台-就业管理"])


@router.get("",
    response_model=APIResponse[PaginatedData[AdminJobListItem]],
    summary="岗位列表",
    description="""
管理后台 **就业管理** 页面使用。

**页面路径**: `/admin/job`

**使用场景**: 页面加载时获取岗位列表，支持关键词搜索

**查询参数**:
- `keyword`: 按岗位标题模糊搜索
- `page`: 页码，从 1 开始
- `page_size`: 每页条数，默认 20，最大 100

**响应**: 分页岗位数据，包含公司、薪资等信息
    """,
)
async def list_jobs(
    keyword: str | None = Query(None, description="按标题关键词模糊搜索"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(require_permission("job:list")),
):
    """分页查询岗位列表，支持按标题关键词模糊搜索"""
    result = await AdminJobService().list_jobs(keyword, page, page_size)
    return success(data=result)


@router.post("",
    response_model=APIResponse[AdminJobListItem],
    summary="新增岗位",
    description="""
管理后台 **就业管理** 页面使用。

**页面路径**: `/admin/job`

**使用场景**: 新增/编辑弹窗提交，创建新岗位

**请求体**: 岗位标题、公司、薪资等信息

**响应**: 新创建的岗位详情
    """,
)
async def create_job(
    body: AdminJobCreate,
    _admin=Depends(require_permission("job:write")),
):
    """创建新岗位"""
    result = await AdminJobService().create(body)
    return success(data=result)


@router.put("/{job_id}",
    response_model=APIResponse[AdminJobListItem],
    summary="编辑岗位",
    description="""
管理后台 **就业管理** 页面使用。

**页面路径**: `/admin/job`

**使用场景**: 新增/编辑弹窗提交，更新已有岗位信息

**路径参数**:
- `job_id`: 岗位 ID

**请求体**: 要更新的岗位字段

**响应**: 更新后的岗位详情
    """,
)
async def update_job(
    body: AdminJobUpdate,
    job_id: int = Path(..., description="岗位 ID"),
    _admin=Depends(require_permission("job:write")),
):
    """更新指定岗位信息"""
    result = await AdminJobService().update(job_id, body)
    return success(data=result)


@router.delete("/{job_id}",
    response_model=APIResponse,
    summary="下架岗位",
    description="""
管理后台 **就业管理** 页面使用。

**页面路径**: `/admin/job`

**使用场景**: 下架指定岗位（软删除）

**路径参数**:
- `job_id`: 岗位 ID

**响应**: 操作结果消息
    """,
)
async def delete_job(
    job_id: int = Path(..., description="岗位 ID"),
    _admin=Depends(require_permission("job:write")),
):
    """下架指定岗位"""
    await AdminJobService().deactivate(job_id)
    return success(message="岗位已下架")