from datetime import datetime

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import Response

from app.middleware.auth import require_permission
from app.middleware.rate_limit import limiter
from app.schemas.admin import (
    AdminBatchDeleteRequest,
    AdminIdentityReview,
    AdminProfileUpdate,
    AdminUserConversationBrief,
    AdminUserFilter,
    AdminUserListItem,
    AdminUserOrderBrief,
    AdminUserStatusToggle,
)
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.user import (
    EnterpriseResponse,
    RealnameAdminResponse,
    StudentResponse,
    UserProfileDetail,
)
from app.services.admin_user import AdminUserService

router = APIRouter(prefix="/users", tags=["管理后台-用户管理"])


@router.get("",
    response_model=APIResponse[PaginatedData[AdminUserListItem]],
    summary="用户列表",
    description="""
管理后台 **用户管理** 页面使用。

**页面路径**: `/admin/users`

**使用场景**: 页面加载时获取用户列表，支持按 openid/手机号/注册时间筛选

**查询参数**:
- `openid`: 按 openid 精确筛选
- `phone`: 按手机号精确筛选
- `created_at_start` / `created_at_end`: 注册时间范围
- `page`: 页码
- `page_size`: 每页条数

**响应**: 分页用户数据
    """,
)
async def list_users(
    openid: str | None = Query(None, description="按 openid 精确筛选"),
    phone: str | None = Query(None, description="按手机号精确筛选"),
    created_at_start: datetime | None = Query(None, description="创建时间起，ISO 8601"),
    created_at_end: datetime | None = Query(None, description="创建时间止，ISO 8601"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(require_permission("user:list")),
) -> APIResponse[PaginatedData[AdminUserListItem]]:
    has_filters = openid or phone or created_at_start or created_at_end
    filters = AdminUserFilter(openid=openid, phone=phone, created_at_start=created_at_start, created_at_end=created_at_end) if has_filters else None
    result = await AdminUserService().list_users(filters, page, page_size)
    return success(data=result)


@router.get("/export",
    response_class=Response,
    summary="导出用户数据",
    description="""
管理后台 **用户管理** 页面使用。

**页面路径**: `/admin/users`

**使用场景**: 管理员点击导出按钮，按当前筛选条件导出用户数据为 CSV 文件

**查询参数**: 同用户列表筛选参数

**响应**: CSV 文件下载
    """,
)
async def export_users(
    openid: str | None = Query(None, description="按 openid 精确筛选"),
    phone: str | None = Query(None, description="按手机号精确筛选"),
    created_at_start: datetime | None = Query(None, description="创建时间起，ISO 8601"),
    created_at_end: datetime | None = Query(None, description="创建时间止，ISO 8601"),
    _admin=Depends(require_permission("user:list")),
):
    has_filters = openid or phone or created_at_start or created_at_end
    filters = AdminUserFilter(openid=openid, phone=phone, created_at_start=created_at_start, created_at_end=created_at_end) if has_filters else None
    csv_content = await AdminUserService().import_users_csv(filters)
    return Response(content=csv_content, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=users.csv"})


@router.get("/{user_id}",
    response_model=APIResponse[AdminUserListItem],
    summary="用户详情",
    description="""
管理后台 **用户管理** 页面使用。

**页面路径**: `/admin/users`

**使用场景**: 管理员点击用户行，查看该用户的详细信息
    """,
)
async def get_user(
    user_id: int = Path(..., description="用户 ID"),
    _admin=Depends(require_permission("user:list")),
) -> APIResponse[AdminUserListItem]:
    result = await AdminUserService().get_user(user_id)
    return success(data=result)


@router.patch("/{user_id}/status",
    response_model=APIResponse[AdminUserListItem],
    summary="切换用户状态",
    description="""
管理后台 **用户管理** 页面使用。

**页面路径**: `/admin/users`

**使用场景**: 管理员点击启用/禁用开关，切换用户的可用状态
    """,
)
async def toggle_user_status(
    body: AdminUserStatusToggle,
    user_id: int = Path(..., description="用户 ID"),
    _admin=Depends(require_permission("user:list")),
) -> APIResponse[AdminUserListItem]:
    result = await AdminUserService().toggle_user_status(user_id, body.is_active)
    return success(data=result, message="用户状态已更新")


@router.post("/batch-delete",
    response_model=APIResponse[int],
    summary="批量删除用户",
    description="""
管理后台 **用户管理** 页面使用。

**页面路径**: `/admin/users`

**使用场景**: 管理员勾选多个用户后点击批量删除按钮
    """,
)
@limiter.limit("3/minute")
async def batch_delete_users(
    request: Request,
    body: AdminBatchDeleteRequest,
    _admin=Depends(require_permission("user:delete")),
):
    count = await AdminUserService().batch_delete(body.ids)
    return success(data=count, message=f"已删除 {count} 个用户")


@router.get("/{user_id}/orders",
    response_model=APIResponse[list[AdminUserOrderBrief]],
    summary="用户订单列表",
    description="""
管理后台 **用户管理** 页面使用。

**页面路径**: `/admin/users`

**使用场景**: 管理员在用户详情中查看该用户的订单记录
    """,
)
async def get_user_orders(
    user_id: int = Path(..., description="用户 ID"),
    _admin=Depends(require_permission("user:list")),
):
    result = await AdminUserService().get_user_orders(user_id)
    return success(data=result)


@router.get("/{user_id}/conversations",
    response_model=APIResponse[list[AdminUserConversationBrief]],
    summary="用户对话记录",
    description="""
管理后台 **用户管理** 页面使用。

**页面路径**: `/admin/users`

**使用场景**: 管理员在用户详情中查看该用户的 AI 对话记录
    """,
)
async def get_user_conversations(
    user_id: int = Path(..., description="用户 ID"),
    _admin=Depends(require_permission("user:list")),
):
    result = await AdminUserService().get_user_conversations(user_id)
    return success(data=result)


@router.put("/{user_id}/identity/review",
    response_model=APIResponse[dict],
    summary="审核实名认证",
    description="""
管理后台 **用户管理** 页面使用。

**页面路径**: `/admin/users`

**使用场景**: 管理员审核用户的实名认证信息，通过或驳回。

**路径参数**:
- `user_id`: 用户 ID

**请求体**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| status | str | ✅ | `verified` 通过 / `rejected` 驳回 |
| comment | str | 否 | 审核备注 |

**认证**: 需 `user:write` 权限
    """,
)
async def review_identity(
    body: AdminIdentityReview,
    user_id: int = Path(..., description="用户 ID"),
    _admin=Depends(require_permission("user:write")),
):
    result = await AdminUserService().review_identity(user_id, body)
    return success(data=result)


@router.put("/{user_id}/profile",
    response_model=APIResponse[UserProfileDetail],
    summary="编辑用户资料（管理端可直接修改任意数据）",
)
async def update_user_profile(
    body: AdminProfileUpdate,
    user_id: int = Path(..., description="用户 ID"),
    _admin=Depends(require_permission("user:write")),
) -> APIResponse[UserProfileDetail]:
    result = await AdminUserService().update_user_profile(user_id, body)
    return success(data=result)


@router.get("/{user_id}/profile",
    response_model=APIResponse[UserProfileDetail],
    summary="用户完整个人资料",
    description="""
管理后台 **用户管理** 页面使用。

**页面路径**: `/admin/users`

**使用场景**: 管理员在用户详情抽屉中查看该用户的完整个人资料，包括性别、学历、学校、专业、单位、邮箱以及脱敏后的手机号/身份证号。

**路径参数**:
- `user_id`: 用户 ID

**认证**: 需 `user:list` 权限
    """,
)
async def get_user_profile(
    user_id: int = Path(..., description="用户 ID"),
    _admin=Depends(require_permission("user:list")),
) -> APIResponse[UserProfileDetail]:
    result = await AdminUserService().get_user_profile(user_id)
    return success(data=result)

@router.get("/{user_id}/identity",
    response_model=APIResponse[RealnameAdminResponse],
    summary="用户实名认证详情",
    description="""
管理后台 **用户管理** 页面使用。

**页面路径**: `/admin/users`

**使用场景**: 管理员在用户详情抽屉中查看该用户的实名认证信息，包括认证类型、真实姓名、明文身份证号、证件照以及审核状态。

**路径参数**:
- `user_id`: 用户 ID

**认证**: 需 `user:list` 权限
    """,
)
async def get_user_identity(
    user_id: int = Path(..., description="用户 ID"),
    _admin=Depends(require_permission("user:list")),
) -> APIResponse[RealnameAdminResponse]:
    result = await AdminUserService().get_user_identity(user_id)
    return success(data=result)


# ── Level 2: 学生信息 ──

@router.get("/{user_id}/student",
    response_model=APIResponse[StudentResponse],
    summary="用户学生信息",
)
async def get_user_student(
    user_id: int = Path(..., description="用户 ID"),
    _admin=Depends(require_permission("user:list")),
) -> APIResponse[StudentResponse]:
    result = await AdminUserService().get_user_student(user_id)
    return success(data=result)


@router.put("/{user_id}/student/review",
    response_model=APIResponse[dict],
    summary="审核学生信息",
)
async def review_student(
    body: AdminIdentityReview,
    user_id: int = Path(..., description="用户 ID"),
    _admin=Depends(require_permission("user:write")),
):
    result = await AdminUserService().review_student(user_id, body)
    return success(data=result)


# ── Level 2: 企业信息 ──

@router.get("/{user_id}/enterprise",
    response_model=APIResponse[EnterpriseResponse],
    summary="用户企业信息",
)
async def get_user_enterprise(
    user_id: int = Path(..., description="用户 ID"),
    _admin=Depends(require_permission("user:list")),
) -> APIResponse[EnterpriseResponse]:
    result = await AdminUserService().get_user_enterprise(user_id)
    return success(data=result)


@router.put("/{user_id}/enterprise/review",
    response_model=APIResponse[dict],
    summary="审核企业信息",
)
async def review_enterprise(
    body: AdminIdentityReview,
    user_id: int = Path(..., description="用户 ID"),
    _admin=Depends(require_permission("user:write")),
):
    result = await AdminUserService().review_enterprise(user_id, body)
    return success(data=result)