"""管理后台 - 审核管理"""

from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import require_permission
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.review import ReviewCreate, ReviewFilter, ReviewResponse
from app.services.review import ReviewService

router = APIRouter(prefix="/reviews", tags=["管理后台-审核管理"])


@router.post("",
    response_model=APIResponse[ReviewResponse],
    summary="提交审核",
    description="""
管理后台 **审核** 功能使用。

**使用场景**: 对实名认证、学生信息和旧版认证订单进行审核

**请求体**:
- `target_type`: 审核对象类型（identity / student / order）
- `target_id`: 审核对象 ID（user_id 或 order_id）
- `action`: 审核动作（approve / reject）
- `comment`: 审核备注（驳回时必填）

**认证**: 需 `user:write` 权限
    """,
)
async def create_review(
    body: ReviewCreate,
    _admin=Depends(require_permission("user:write")),
) -> APIResponse[ReviewResponse]:
    result = await ReviewService().create_review(_admin.id, body)
    return success(data=result)


@router.get("",
    response_model=APIResponse[PaginatedData[ReviewResponse]],
    summary="审核记录",
    description="""
管理后台 **审核** 功能使用。

**使用场景**: 查询审核历史记录

**查询参数**:
- `target_type`: 按审核对象类型筛选
- `target_id`: 按审核对象 ID 筛选
- `page`: 页码，默认 1
- `page_size`: 每页数量，默认 20

**认证**: 需 `user:list` 权限
    """,
)
async def list_reviews(
    target_type: str | None = Query(None, description="按审核对象类型筛选"),
    target_id: int | None = Query(None, ge=1, description="按审核对象 ID 筛选"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(require_permission("user:list")),
) -> APIResponse[PaginatedData[ReviewResponse]]:
    filters = ReviewFilter(target_type=target_type, target_id=target_id) if (target_type or target_id) else None
    result = await ReviewService().list_reviews(filters, page, page_size)
    return success(data=result)
