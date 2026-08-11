from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Path, Query, Request

from app.middleware.auth import require_permission, require_super_admin
from app.middleware.rate_limit import limiter
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.renshe import (
    RensheAdminApplicationDetailResponse,
    RensheAdminApplicationListItem,
    RensheAuditLogResponse,
    RensheApplicationStatus,
    RensheCleanupRunResponse,
    RensheExportJobResponse,
    RensheReviewCorrectionCreate,
    RensheReviewCorrectionResponse,
    RensheReviewCreate,
    RensheReviewResponse,
    RensheRefundDecision,
    RensheRefundResponse,
    RensheSignedUrlResponse,
    RensheMaterialKind,
)
from app.services.renshe_review import RensheReviewService


router = APIRouter(prefix="/renshe", tags=["管理后台-人社报名"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get(
    "/applications",
    response_model=APIResponse[PaginatedData[RensheAdminApplicationListItem]],
    summary="人社报名列表",
)
async def list_applications(
    plan_id: int | None = Query(None, ge=1),
    status: RensheApplicationStatus | None = Query(None),
    payment_status: Literal["pending", "paid", "completed", "refunded", "closed"] | None = Query(None),
    keyword: str | None = Query(None, max_length=64, description="姓名、身份证号或联系电话"),
    submitted_at_start: datetime | None = Query(None),
    submitted_at_end: datetime | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin=Depends(require_permission("user:list")),
) -> APIResponse[PaginatedData[RensheAdminApplicationListItem]]:
    result = await RensheReviewService().list_applications(
        plan_id=plan_id,
        status=status,
        payment_status=payment_status,
        keyword=keyword,
        submitted_at_start=submitted_at_start,
        submitted_at_end=submitted_at_end,
        page=page,
        page_size=page_size,
    )
    return success(data=result)


@router.get(
    "/applications/{application_id}",
    response_model=APIResponse[RensheAdminApplicationDetailResponse],
    summary="人社报名详情与版本历史",
)
async def get_application(
    application_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("user:list")),
) -> APIResponse[RensheAdminApplicationDetailResponse]:
    result = await RensheReviewService().get_application(
        application_id, actor_id=_admin.id
    )
    return success(data=result)


@router.post(
    "/applications/{application_id}/initial-review",
    response_model=APIResponse[RensheReviewResponse],
    summary="人社报名初审",
    description="驳回只要求修改，不触发退款或关单。",
)
async def initial_review(
    body: RensheReviewCreate,
    application_id: int = Path(..., ge=1),
    admin=Depends(require_permission("user:write")),
) -> APIResponse[RensheReviewResponse]:
    result = await RensheReviewService().review(
        reviewer_id=admin.id,
        application_id=application_id,
        stage="initial",
        data=body,
    )
    return success(data=result)


@router.post(
    "/applications/{application_id}/external-review",
    response_model=APIResponse[RensheReviewResponse],
    summary="登记外部复审结果",
)
async def external_review(
    body: RensheReviewCreate,
    application_id: int = Path(..., ge=1),
    admin=Depends(require_permission("user:write")),
) -> APIResponse[RensheReviewResponse]:
    result = await RensheReviewService().review(
        reviewer_id=admin.id,
        application_id=application_id,
        stage="external",
        data=body,
    )
    return success(data=result)


@router.post(
    "/reviews/{review_id}/corrections",
    response_model=APIResponse[RensheReviewCorrectionResponse],
    summary="更正审核结果（仅超级管理员）",
)
async def correct_review(
    body: RensheReviewCorrectionCreate,
    review_id: int = Path(..., ge=1),
    admin=Depends(require_super_admin),
) -> APIResponse[RensheReviewCorrectionResponse]:
    result = await RensheReviewService().correct_review(
        super_admin_id=admin.id,
        review_id=review_id,
        data=body,
    )
    return success(data=result)


@router.get(
    "/refunds",
    response_model=APIResponse[PaginatedData[RensheRefundResponse]],
    summary="退款申请工作台",
)
async def list_refunds(
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin=Depends(require_permission("order:list")),
) -> APIResponse[PaginatedData[RensheRefundResponse]]:
    from app.services.renshe_refund import RensheRefundService

    result = await RensheRefundService().list_refunds(
        status=status,
        page=page,
        page_size=page_size,
    )
    return success(data=result)


@router.post(
    "/refunds/{refund_id}/decision",
    response_model=APIResponse[RensheRefundResponse],
    summary="批准或驳回退款（仅超级管理员）",
    description=(
        "批准时固定商户退款单号并提交微信 V3；接口返回 processing 仅表示"
        "微信已受理，最终成功由退款通知或主动查询确认。"
    ),
)
@limiter.limit("10/minute")
async def decide_refund(
    request: Request,
    body: RensheRefundDecision,
    refund_id: int = Path(..., ge=1),
    admin=Depends(require_super_admin),
) -> APIResponse[RensheRefundResponse]:
    from app.services.renshe_refund import RensheRefundService

    result = await RensheRefundService().decide(
        super_admin_id=admin.id,
        refund_id=refund_id,
        data=body,
    )
    return success(data=result)


@router.post(
    "/plans/{plan_id}/exports",
    response_model=APIResponse[RensheExportJobResponse],
    summary="新建全批次导出任务",
    description="每次重新快照全批次可导出考生，并生成多个不超过 10 GiB 的独立 ZIP。",
)
async def create_export_job(
    plan_id: int = Path(..., ge=1),
    admin=Depends(require_permission("user:write")),
) -> APIResponse[RensheExportJobResponse]:
    from app.services.renshe_export import RensheExportService

    result = await RensheExportService().create_job(plan_id=plan_id, admin_id=admin.id)
    return success(data=result, message="导出任务已创建")


@router.get(
    "/plans/{plan_id}/exports",
    response_model=APIResponse[list[RensheExportJobResponse]],
    summary="查看批次导出历史",
)
async def list_export_jobs(
    plan_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("user:list")),
) -> APIResponse[list[RensheExportJobResponse]]:
    from app.services.renshe_export import RensheExportService

    return success(data=await RensheExportService().list_jobs(plan_id))


@router.get(
    "/exports/{job_id}",
    response_model=APIResponse[RensheExportJobResponse],
    summary="查看导出进度与分卷",
)
async def get_export_job(
    job_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("user:list")),
) -> APIResponse[RensheExportJobResponse]:
    from app.services.renshe_export import RensheExportService

    return success(data=await RensheExportService().get_job(job_id))


@router.post(
    "/exports/{job_id}/retry",
    response_model=APIResponse[RensheExportJobResponse],
    summary="重试失败的导出任务",
)
async def retry_export_job(
    job_id: int = Path(..., ge=1),
    admin=Depends(require_permission("user:write")),
) -> APIResponse[RensheExportJobResponse]:
    from app.services.renshe_export import RensheExportService

    result = await RensheExportService().retry_job(job_id=job_id, admin_id=admin.id)
    return success(data=result, message="导出任务已重新排队")


@router.get(
    "/materials/{material_id}/signed-url",
    response_model=APIResponse[RensheSignedUrlResponse],
    summary="获取材料私有预览或下载短链接",
    description="签名最长 5 分钟；每次请求均记录管理员、材料、版本、IP 和结果。",
)
async def get_material_signed_url(
    request: Request,
    material_id: int = Path(..., ge=1),
    download: bool = Query(False, description="true 为下载，false 为预览"),
    admin=Depends(require_permission("user:list")),
) -> APIResponse[RensheSignedUrlResponse]:
    from app.services.renshe_export import RensheExportService

    result = await RensheExportService().material_signed_url(
        material_id=material_id,
        admin_id=admin.id,
        download=download,
        ip_address=_client_ip(request),
    )
    return success(data=result)


@router.get(
    "/users/{user_id}/verification-materials/{kind}/signed-url",
    response_model=APIResponse[RensheSignedUrlResponse],
    summary="获取待审核实名/学生材料短链接",
    description="供管理员在报名建立前人工审核认证资料；预览和下载均留痕。",
)
async def get_verification_material_signed_url(
    request: Request,
    user_id: int = Path(..., ge=1),
    kind: RensheMaterialKind = Path(...),
    download: bool = Query(False),
    admin=Depends(require_permission("user:list")),
) -> APIResponse[RensheSignedUrlResponse]:
    from app.services.renshe_export import RensheExportService

    result = await RensheExportService().verification_material_signed_url(
        user_id=user_id,
        kind=kind,
        admin_id=admin.id,
        download=download,
        ip_address=_client_ip(request),
    )
    return success(data=result)


@router.get(
    "/export-volumes/{volume_id}/signed-url",
    response_model=APIResponse[RensheSignedUrlResponse],
    summary="获取导出分卷下载短链接",
    description="只允许下载生成成功且尚未清理的分卷，并记录下载审计。",
)
async def get_export_volume_signed_url(
    request: Request,
    volume_id: int = Path(..., ge=1),
    admin=Depends(require_permission("user:write")),
) -> APIResponse[RensheSignedUrlResponse]:
    from app.services.renshe_export import RensheExportService

    result = await RensheExportService().volume_signed_url(
        volume_id=volume_id,
        admin_id=admin.id,
        ip_address=_client_ip(request),
    )
    return success(data=result)


@router.get(
    "/plans/{plan_id}/cleanup-runs",
    response_model=APIResponse[list[RensheCleanupRunResponse]],
    summary="查看批次级清理任务",
)
async def list_cleanup_runs(
    plan_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("user:list")),
) -> APIResponse[list[RensheCleanupRunResponse]]:
    from app.services.renshe_cleanup import RensheCleanupService

    return success(data=await RensheCleanupService().list_runs(plan_id))


@router.get(
    "/audit-logs",
    response_model=APIResponse[PaginatedData[RensheAuditLogResponse]],
    summary="查询人社审计日志",
    description=(
        "按批次、报名、管理员、动作、结果和时间分页查询脱敏审计；"
        "该资源只读，没有更新、删除或敏感原文导出接口。"
    ),
)
async def list_audit_logs(
    request: Request,
    plan_id: int | None = Query(None, ge=1),
    application_id: int | None = Query(None, ge=1),
    admin_id: int | None = Query(None, ge=1, description="按操作管理员筛选"),
    action: str | None = Query(None, min_length=1, max_length=64),
    result: Literal["succeeded", "failed"] | None = Query(None),
    started_at: datetime | None = Query(None),
    ended_at: datetime | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin=Depends(require_permission("user:list")),
) -> APIResponse[PaginatedData[RensheAuditLogResponse]]:
    from app.services.renshe_audit import RensheAuditQueryService

    rows = await RensheAuditQueryService().list_logs(
        admin_id=admin.id,
        ip_address=_client_ip(request),
        plan_id=plan_id,
        application_id=application_id,
        actor_admin_id=admin_id,
        action=action,
        result=result,
        started_at=started_at,
        ended_at=ended_at,
        page=page,
        page_size=page_size,
    )
    return success(data=rows)


@router.post(
    "/cleanup-runs/{run_id}/retry",
    response_model=APIResponse[RensheCleanupRunResponse],
    summary="重试批次清理任务（仅超级管理员）",
)
async def retry_cleanup_run(
    run_id: int = Path(..., ge=1),
    admin=Depends(require_super_admin),
) -> APIResponse[RensheCleanupRunResponse]:
    from app.services.renshe_cleanup import RensheCleanupService

    result = await RensheCleanupService().retry_run(
        run_id=run_id, super_admin_id=admin.id
    )
    return success(data=result, message="清理任务已重新排队")
