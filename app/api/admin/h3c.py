from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query

from app.api.admin.error_contracts import admin_error_contract
from app.middleware.auth import (
    require_permission,
    require_reauthenticated_admin,
)
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.h3c_registration import (
    H3cCloseRequest,
    H3cExamBatchCreate,
    H3cExamBatchResponse,
    H3cExamBatchUpdate,
    H3cExportCreate,
    H3cExportJobResponse,
    H3cRefundConfirmRequest,
    H3cRefundResponse,
    H3cRegistrationResponse,
    H3cReviewDecision,
    H3cSignedUrlResponse,
)
from app.services.h3c_admin import H3cAdminBatchService
from app.services.h3c_export import H3cExportService
from app.services.h3c_refund import H3cRefundService
from app.services.h3c_registration import H3cRegistrationService


router = APIRouter(prefix="/cert-products/h3c", tags=["管理后台-H3C 认证"])


@router.post(
    "/batches",
    response_model=APIResponse[H3cExamBatchResponse],
    summary="创建 H3C 考试批次",
    **admin_error_contract("40001", "40100", "40101", "40200", "40201", "50000"),
)
async def create_batch(
    body: H3cExamBatchCreate,
    admin=Depends(require_permission("h3c:batch_manage")),
) -> APIResponse[H3cExamBatchResponse]:
    return success(
        data=await H3cAdminBatchService().create_batch(admin_id=admin.id, data=body)
    )


@router.get(
    "/batches",
    response_model=APIResponse[PaginatedData[H3cExamBatchResponse]],
    summary="H3C 考试批次列表",
    **admin_error_contract("40001", "40100", "40101", "50000"),
)
async def list_batches(
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin=Depends(require_permission("h3c:batch_manage")),
) -> APIResponse[PaginatedData[H3cExamBatchResponse]]:
    return success(
        data=await H3cAdminBatchService().list_batches(
            status=status,
            page=page,
            page_size=page_size,
        )
    )


@router.get(
    "/batches/{batch_id}",
    response_model=APIResponse[H3cExamBatchResponse],
    summary="H3C 考试批次详情",
    **admin_error_contract("40001", "40100", "40101", "40300", "50000"),
)
async def get_batch(
    batch_id: int = Path(..., gt=0),
    _admin=Depends(require_permission("h3c:batch_manage")),
) -> APIResponse[H3cExamBatchResponse]:
    return success(data=await H3cAdminBatchService().get_batch(batch_id))


@router.put(
    "/batches/{batch_id}",
    response_model=APIResponse[H3cExamBatchResponse],
    summary="编辑草稿 H3C 考试批次",
    **admin_error_contract("40001", "40100", "40101", "40200", "40201", "40300", "50000"),
)
async def update_batch(
    body: H3cExamBatchUpdate,
    batch_id: int = Path(..., gt=0),
    admin=Depends(require_permission("h3c:batch_manage")),
) -> APIResponse[H3cExamBatchResponse]:
    return success(
        data=await H3cAdminBatchService().update_batch(
            admin_id=admin.id,
            batch_id=batch_id,
            data=body,
        )
    )


@router.post(
    "/batches/{batch_id}/publish",
    response_model=APIResponse[H3cExamBatchResponse],
    summary="发布 H3C 考试批次",
    **admin_error_contract("40100", "40101", "40200", "40201", "40300", "50000"),
)
async def publish_batch(
    batch_id: int = Path(..., gt=0),
    admin=Depends(require_permission("h3c:batch_manage")),
) -> APIResponse[H3cExamBatchResponse]:
    return success(
        data=await H3cAdminBatchService().publish_batch(
            admin_id=admin.id,
            batch_id=batch_id,
        ),
        message="H3C 考试批次已发布",
    )


@router.post(
    "/batches/{batch_id}/close-registration",
    response_model=APIResponse[H3cExamBatchResponse],
    summary="关闭 H3C 批次报名",
    **admin_error_contract("40100", "40101", "40200", "40201", "40300", "50000"),
)
async def close_registration(
    batch_id: int = Path(..., gt=0),
    admin=Depends(require_permission("h3c:batch_manage")),
) -> APIResponse[H3cExamBatchResponse]:
    return success(
        data=await H3cAdminBatchService().close_registration(
            admin_id=admin.id,
            batch_id=batch_id,
        ),
        message="H3C 批次报名已关闭",
    )


@router.post(
    "/batches/{batch_id}/finalize",
    response_model=APIResponse[H3cExamBatchResponse],
    summary="结束 H3C 考试批次",
    **admin_error_contract("40100", "40101", "40200", "40201", "40300", "50000"),
)
async def finalize_batch(
    batch_id: int = Path(..., gt=0),
    admin=Depends(require_permission("h3c:batch_manage")),
) -> APIResponse[H3cExamBatchResponse]:
    return success(
        data=await H3cAdminBatchService().finalize_batch(
            admin_id=admin.id,
            batch_id=batch_id,
        ),
        message="H3C 考试批次已结束",
    )


@router.post(
    "/batches/{batch_id}/cancel",
    response_model=APIResponse[H3cExamBatchResponse],
    summary="取消 H3C 考试批次",
    **admin_error_contract("40100", "40101", "40200", "40201", "40300", "50000"),
)
async def cancel_batch(
    batch_id: int = Path(..., gt=0),
    _authorized=Depends(require_permission("h3c:batch_manage")),
    admin=Depends(require_reauthenticated_admin),
) -> APIResponse[H3cExamBatchResponse]:
    # Reauthentication is role-independent but does not replace the preset
    # permission check required by the fixed-role model.
    return success(
        data=await H3cAdminBatchService().cancel_batch(
            admin_id=admin.id,
            batch_id=batch_id,
        ),
        message="H3C 考试批次已取消",
    )


@router.get(
    "/registrations",
    response_model=APIResponse[PaginatedData[H3cRegistrationResponse]],
    summary="H3C 报名列表",
    **admin_error_contract("40001", "40100", "40101", "50000"),
)
async def list_registrations(
    batch_id: int | None = Query(None, gt=0),
    registration_type: str | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin=Depends(require_permission("h3c:review")),
) -> APIResponse[PaginatedData[H3cRegistrationResponse]]:
    return success(
        data=await H3cRegistrationService().list_admin_registrations(
            batch_id=batch_id,
            registration_type=registration_type,
            status=status,
            page=page,
            page_size=page_size,
        )
    )


@router.get(
    "/registrations/{registration_id}",
    response_model=APIResponse[H3cRegistrationResponse],
    summary="H3C 报名详情",
    **admin_error_contract("40001", "40100", "40101", "40300", "50000"),
)
async def get_registration(
    registration_id: int = Path(..., gt=0),
    _admin=Depends(require_permission("h3c:review")),
) -> APIResponse[H3cRegistrationResponse]:
    service = H3cRegistrationService()
    return success(data=await service._admin_registration(registration_id))


@router.post(
    "/registrations/{registration_id}/review",
    response_model=APIResponse[H3cRegistrationResponse],
    summary="审核 H3C 报名",
    **admin_error_contract("40001", "40100", "40101", "40200", "40201", "40300", "50000"),
)
async def review_registration(
    body: H3cReviewDecision,
    registration_id: int = Path(..., gt=0),
    admin=Depends(require_permission("h3c:review")),
) -> APIResponse[H3cRegistrationResponse]:
    return success(
        data=await H3cRegistrationService().review(
            admin_id=admin.id,
            registration_id=registration_id,
            decision_data=body,
        )
    )


@router.post(
    "/registrations/{registration_id}/close",
    response_model=APIResponse[H3cRegistrationResponse],
    summary="例外关闭 H3C 报名并发起退款确认",
    **admin_error_contract("40001", "40100", "40101", "40200", "40201", "40300", "50000"),
)
async def close_registration_record(
    body: H3cCloseRequest,
    registration_id: int = Path(..., gt=0),
    _authorized=Depends(require_permission("h3c:order_close")),
    admin=Depends(require_reauthenticated_admin),
) -> APIResponse[H3cRegistrationResponse]:
    return success(
        data=await H3cRegistrationService().close_registration(
            admin_id=admin.id,
            registration_id=registration_id,
            reason=body.reason_detail,
        )
    )


@router.get(
    "/refunds",
    response_model=APIResponse[PaginatedData[H3cRefundResponse]],
    summary="H3C 退款任务列表",
    **admin_error_contract("40001", "40100", "40101", "50000"),
)
async def list_refunds(
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin=Depends(require_permission("h3c:refund")),
) -> APIResponse[PaginatedData[H3cRefundResponse]]:
    return success(
        data=await H3cRefundService().list_refunds(
            status=status,
            page=page,
            page_size=page_size,
        )
    )


@router.post(
    "/refunds/{refund_id}/confirm",
    response_model=APIResponse[H3cRefundResponse],
    summary="确认并发起 H3C 全额退款",
    **admin_error_contract("40001", "40100", "40101", "40200", "40201", "40300", "50000"),
)
async def confirm_refund(
    body: H3cRefundConfirmRequest,
    refund_id: int = Path(..., gt=0),
    _authorized=Depends(require_permission("h3c:refund")),
    admin=Depends(require_reauthenticated_admin),
) -> APIResponse[H3cRefundResponse]:
    return success(
        data=await H3cRefundService().confirm(
            admin_id=admin.id,
            refund_id=refund_id,
        ),
        message="H3C 退款已确认提交",
    )


@router.post(
    "/exports",
    response_model=APIResponse[H3cExportJobResponse],
    summary="创建 H3C 异步导出任务",
    **admin_error_contract("40001", "40100", "40101", "40200", "40201", "40300", "50000"),
)
async def create_export(
    body: H3cExportCreate,
    admin=Depends(require_permission("h3c:export")),
) -> APIResponse[H3cExportJobResponse]:
    return success(
        data=await H3cExportService().create_job(admin_id=admin.id, data=body)
    )


@router.get(
    "/exports",
    response_model=APIResponse[PaginatedData[H3cExportJobResponse]],
    summary="H3C 导出任务列表",
    **admin_error_contract("40001", "40100", "40101", "50000"),
)
async def list_exports(
    batch_id: int | None = Query(None, gt=0),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin=Depends(require_permission("h3c:export")),
) -> APIResponse[PaginatedData[H3cExportJobResponse]]:
    return success(
        data=await H3cExportService().list_jobs(
            batch_id=batch_id,
            status=status,
            page=page,
            page_size=page_size,
        )
    )


@router.get(
    "/exports/{job_id}/download-url",
    response_model=APIResponse[H3cSignedUrlResponse],
    summary="获取 H3C 导出产物短时下载地址",
    **admin_error_contract("40100", "40101", "40200", "40201", "40300", "50000"),
)
async def download_export(
    job_id: int = Path(..., gt=0),
    _authorized=Depends(require_permission("h3c:export")),
    admin=Depends(require_reauthenticated_admin),
) -> APIResponse[H3cSignedUrlResponse]:
    return success(
        data=await H3cExportService().signed_url(
            admin_id=admin.id,
            job_id=job_id,
        )
    )
