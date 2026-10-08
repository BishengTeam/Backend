from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.middleware.auth import require_permission
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.nisp import (
    NispExportCreate,
    NispExportJobResponse,
    NispRefundResponse,
    NispExamBatchCreate,
    NispExamBatchResponse,
    NispExamBatchUpdate,
    NispRegistrationResponse,
    NispReviewDecisionRequest,
)
from app.services.nisp_admin import NispAdminService
from app.services.nisp_export import NispExportService
from app.services.nisp_refund import NispRefundService
from app.services.nisp_registration import NispRegistrationService

router = APIRouter(prefix="/nisp", tags=["管理后台-NISP认证"])
_service = NispAdminService()


@router.get("/batches", response_model=APIResponse[PaginatedData[NispExamBatchResponse]])
async def list_batches(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin=Depends(require_permission("nisp:batch_manage")),
) -> APIResponse[PaginatedData[NispExamBatchResponse]]:
    items, total = await _service.list_batches(page=page, page_size=page_size)
    return success(data=PaginatedData(items=items, total=total, page=page, page_size=page_size))


@router.post("/batches", response_model=APIResponse[NispExamBatchResponse])
async def create_batch(
    body: NispExamBatchCreate,
    _admin=Depends(require_permission("nisp:batch_manage")),
) -> APIResponse[NispExamBatchResponse]:
    return success(data=await _service.create_batch(body))


@router.get("/batches/{batch_id}", response_model=APIResponse[NispExamBatchResponse])
async def get_batch(
    batch_id: int,
    _admin=Depends(require_permission("nisp:batch_manage")),
) -> APIResponse[NispExamBatchResponse]:
    return success(data=await _service.get_batch(batch_id))


@router.put("/batches/{batch_id}", response_model=APIResponse[NispExamBatchResponse])
async def update_batch(
    batch_id: int,
    body: NispExamBatchUpdate,
    _admin=Depends(require_permission("nisp:batch_manage")),
) -> APIResponse[NispExamBatchResponse]:
    return success(data=await _service.update_batch(batch_id, body))


@router.post("/batches/{batch_id}/publish", response_model=APIResponse[NispExamBatchResponse])
async def publish_batch(
    batch_id: int,
    _admin=Depends(require_permission("nisp:batch_manage")),
) -> APIResponse[NispExamBatchResponse]:
    return success(data=await _service.publish_batch(batch_id))


@router.post("/batches/{batch_id}/close-registration", response_model=APIResponse[NispExamBatchResponse])
async def close_registration(
    batch_id: int,
    _admin=Depends(require_permission("nisp:batch_manage")),
) -> APIResponse[NispExamBatchResponse]:
    return success(data=await _service.close_registration(batch_id))


@router.post("/batches/{batch_id}/cancel", response_model=APIResponse[NispExamBatchResponse])
async def cancel_batch(
    batch_id: int,
    _admin=Depends(require_permission("nisp:batch_manage")),
) -> APIResponse[NispExamBatchResponse]:
    return success(data=await _service.cancel_batch(batch_id))


@router.get("/registrations", response_model=APIResponse[PaginatedData[NispRegistrationResponse]])
async def list_registrations(
    batch_id: int | None = Query(None),
    level: str | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin=Depends(require_permission("nisp:review")),
) -> APIResponse[PaginatedData[NispRegistrationResponse]]:
    items, total = await _service.list_registrations(
        batch_id=batch_id,
        level=level,
        status=status,
        page=page,
        page_size=page_size,
    )
    return success(data=PaginatedData(items=items, total=total, page=page, page_size=page_size))


@router.get(
    "/registrations/{registration_id}",
    response_model=APIResponse[NispRegistrationResponse],
)
async def get_registration(
    registration_id: int,
    _admin=Depends(require_permission("nisp:review")),
) -> APIResponse[NispRegistrationResponse]:
    """Get one registration with current and historical review materials."""
    return success(
        data=await NispRegistrationService().get_admin_registration(registration_id)
    )


@router.post("/registrations/{registration_id}/review", response_model=APIResponse[NispRegistrationResponse])
async def review_registration(
    registration_id: int,
    body: NispReviewDecisionRequest,
    _admin=Depends(require_permission("nisp:review")),
) -> APIResponse[NispRegistrationResponse]:
    return success(data=await _service.review(admin_id=_admin.id, registration_id=registration_id, data=body))


@router.post("/export", response_model=APIResponse[NispExportJobResponse])
async def create_export_job(
    body: NispExportCreate,
    _admin=Depends(require_permission("nisp:export")),
):
    """Create an async NISP export job."""
    result = await NispExportService().create_job(
        admin_id=_admin.id,
        batch_id=body.batch_id,
        level=body.level,
        include_statuses=body.include_statuses,
    )
    return success(data=result)


@router.get("/export/jobs", response_model=APIResponse)
async def list_export_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin=Depends(require_permission("nisp:export")),
):
    """List NISP export jobs."""
    from app.schemas.common import PaginatedData
    items, total = await NispExportService().list_jobs(page=page, page_size=page_size)
    return success(data=PaginatedData(items=items, total=total, page=page, page_size=page_size))


@router.get("/export/jobs/{job_id}/signed-url", response_model=APIResponse)
async def get_export_signed_url(
    job_id: int,
    _admin=Depends(require_permission("nisp:export")),
):
    """Get a signed download URL for a completed export."""
    from app.schemas.nisp import NispSignedUrlResponse
    url = await NispExportService().signed_url(job_id)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    return success(data=NispSignedUrlResponse(url=url, expires_at=expires_at))


@router.get("/refunds", response_model=APIResponse)
async def list_refunds(
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin=Depends(require_permission("nisp:refund")),
):
    """List NISP refund requests."""
    items, total = await NispRefundService().list_refunds(
        status=status, page=page, page_size=page_size
    )
    from app.schemas.common import PaginatedData
    return success(data=PaginatedData(
        items=items, total=total, page=page, page_size=page_size
    ))


@router.post("/refunds/{refund_id}/confirm", response_model=APIResponse[NispRefundResponse])
async def confirm_refund(
    refund_id: int,
    _admin=Depends(require_permission("nisp:refund")),
):
    """Confirm and process a NISP refund."""
    result = await NispRefundService().confirm(
        admin_id=_admin.id, refund_id=refund_id
    )
    return success(data=result)


@router.post("/refunds/{refund_id}/reconcile", response_model=APIResponse[NispRefundResponse])
async def reconcile_refund(
    refund_id: int,
    _admin=Depends(require_permission("nisp:refund")),
):
    """Reconcile a processing NISP refund with WeChat Pay."""
    result = await NispRefundService().reconcile(refund_id)
    return success(data=result)
