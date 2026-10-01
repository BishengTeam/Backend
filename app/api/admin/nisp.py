from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.middleware.auth import require_permission
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.nisp import (
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


@router.post("/registrations/{registration_id}/review", response_model=APIResponse[NispRegistrationResponse])
async def review_registration(
    registration_id: int,
    body: NispReviewDecisionRequest,
    _admin=Depends(require_permission("nisp:review")),
) -> APIResponse[NispRegistrationResponse]:
    return success(data=await _service.review(admin_id=_admin.id, registration_id=registration_id, data=body))


@router.get("/export")
async def export_registrations(
    batch_id: int = Query(...),
    level: str = Query(...),
    status: str = Query("approved"),
    _admin=Depends(require_permission("nisp:export")),
):
    """Export NISP registrations as Excel matching the official template."""
    import hashlib
    from fastapi.responses import StreamingResponse
    import io

    from app.adapter.database import get_db_ctx
    from app.domain.nisp.src import NispRegistration

    async with get_db_ctx() as db:
        registrations = (
            await db.execute(
                select(NispRegistration)
                .where(
                    NispRegistration.batch_id == batch_id,
                    NispRegistration.level == level,
                    NispRegistration.status == status,
                )
                .order_by(NispRegistration.id.asc())
            )
        ).scalars().all()

    if not registrations:
        from app.port.exceptions import BusinessException
        raise BusinessException("没有符合条件的报名记录")

    excel_bytes = NispExportService().build_excel(
        list(registrations), level=level
    )

    level_label = "一级" if level == "1" else "二级"
    filename = f"NISP{level_label}报名表-{datetime.now().strftime('%Y%m%d')}.xlsx"

    return StreamingResponse(
        io.BytesIO(excel_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
