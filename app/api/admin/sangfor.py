from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app.middleware.auth import require_permission, require_reauthenticated_admin
from app.schemas.common import APIResponse, PaginatedDataWithCounts, success
from app.schemas.plan import PlanCreate, PlanUpdate
from app.schemas.sangfor import SangforBatchCreate, SangforDecision, SangforExport, SangforRefundResponse
from app.services.plan import PlanService
from app.services.sangfor import SangforService

router = APIRouter(prefix="/cert-products/sangfor", tags=["管理后台-深信服认证"])
service = SangforService()


@router.get("/batches")
async def batches(admin=Depends(require_permission("content:read"))):
    return success(data=await service.batches())


@router.get("/batches/{batch_id}")
async def batch(batch_id: int, admin=Depends(require_permission("content:read"))):
    return success(data=await service.get_batch(batch_id))


@router.post("/batches")
async def create_batch(body: SangforBatchCreate, admin=Depends(require_permission("content:write"))):
    await service.require_product(body.product_code)
    plan = await PlanService().create_plan(body.product_code, PlanCreate.model_validate(body.model_dump(exclude={"product_code"})), admin_id=admin.id)
    return success(data={"id": plan.id}, message="草稿批次已创建")


@router.put("/batches/{batch_id}")
async def update_batch(batch_id: int, body: PlanUpdate, admin=Depends(require_permission("content:write"))):
    batch = await service.get_batch(batch_id)
    return success(data=await PlanService().update_plan(batch_id, body, batch["product_code"], admin_id=admin.id))


@router.post("/batches/{batch_id}/publish")
async def publish_batch(batch_id: int, admin=Depends(require_permission("content:write"))):
    batch = await service.get_batch(batch_id)
    return success(data=await PlanService().publish_plan(batch_id, batch["product_code"], admin_id=admin.id))


@router.post("/batches/{batch_id}/close")
async def close_batch(batch_id: int, admin=Depends(require_permission("content:write"))):
    batch = await service.get_batch(batch_id)
    return success(data=await PlanService().close_registration(batch_id, batch["product_code"], admin_id=admin.id))


@router.get("/registrations")
async def registrations(batch_id: int | None = None, state: str | None = None, keyword: str | None = Query(None, max_length=64),
                        payment_state: str | None = None, refund_only: bool = False, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                        admin=Depends(require_permission("order:list"))):
    return success(data=await service.registrations(batch_id=batch_id, state=state, keyword=keyword, payment_state=payment_state, refund_only=refund_only, page=page, page_size=page_size))


@router.get("/registrations/{registration_id}")
async def detail(registration_id: int, admin=Depends(require_permission("order:list"))):
    return success(data=await service.detail(registration_id))


@router.get("/refund-batches")
async def refund_batches(admin=Depends(require_permission("order:list"))):
    return success(data=await service.refund_batches())


@router.get("/refunds", response_model=APIResponse[PaginatedDataWithCounts[SangforRefundResponse]])
async def refunds(
    batch_id: int | None = Query(None, ge=1),
    registration_id: int | None = Query(None, ge=1),
    keyword: str | None = Query(None, max_length=64),
    request_kind: Literal["normal", "exception"] | None = None,
    requested_at_start: datetime | None = None,
    requested_at_end: datetime | None = None,
    group: Literal["pending", "processing", "abnormal"] | None = None,
    status: Literal["approved", "exception", "succeeded"] | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin=Depends(require_permission("order:list")),
):
    return success(data=await service.refunds(batch_id=batch_id, registration_id=registration_id, keyword=keyword,
        request_kind=request_kind, requested_at_start=requested_at_start, requested_at_end=requested_at_end,
        group=group, status=status, page=page, page_size=page_size))


@router.post("/registrations/{registration_id}/review")
async def review(registration_id: int, body: SangforDecision, admin=Depends(require_permission("sangfor:review"))):
    return success(data=await service.review(registration_id, admin.id, body))


@router.post("/batches/{batch_id}/export")
async def export(batch_id: int, body: SangforExport, admin=Depends(require_permission("sangfor:export")), reauth=Depends(require_reauthenticated_admin)):
    content = await service.export(batch_id, body.registration_ids, admin.id, body.expected_versions)
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="sangfor_batch_{batch_id}.xlsx"'})


@router.get("/batches/{batch_id}/exports")
async def export_records(batch_id: int, admin=Depends(require_permission("sangfor:export"))):
    return success(data=await service.export_records(batch_id))
