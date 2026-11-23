from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy import select

from app.adapter.database import get_db_ctx
from app.domain.order.src.index import Order
from app.middleware.auth import get_current_user, require_normal_admin
from app.policy.permissions import admin_has_permission
from app.port.exceptions import ForbiddenException, NotFoundException
from app.schemas.certification_journey import CertificationVendor, JourneyContentUpdate
from app.schemas.common import success
from app.services.certification_journey import CertificationJourneyService, user_journey

router = APIRouter(prefix="/cert/enrollment", tags=["认证统一报名"])
admin_router = APIRouter(prefix="/certification-journeys", tags=["管理后台-认证报名须知"])
service = CertificationJourneyService()
WRITE_PERMISSION = {"h3c": "cert:h3c", "nisp": "cert:nisp", "renshe": "cert:renshe", "sangfor": "cert:sangfor"}


async def journey_admin(request: Request, vendor: CertificationVendor, admin=Depends(require_normal_admin)):
    permission = WRITE_PERMISSION[vendor] if request.method == "PUT" else "content:read"
    if not admin_has_permission(admin, permission):
        raise ForbiddenException(f"缺少权限: {permission}")
    return admin


@router.get("/orders/{order_id}")
async def order_registration(order_id: int = Path(ge=1), user=Depends(get_current_user)):
    from app.domain.h3c.src.index import H3cRegistration
    from app.domain.nisp.src.index import NispRegistration
    async with get_db_ctx() as db:
        order = await db.scalar(select(Order).where(Order.id == order_id, Order.user_id == user.id))
        if not order:
            raise NotFoundException("订单")
        if order.application_id:
            return success(data={"vendor": "renshe", "id": order.application_id})
        if (order.extra_data or {}).get("business_type") == "sangfor":
            return success(data={"vendor": "sangfor", "id": order.id})
        for vendor, model in (("h3c", H3cRegistration), ("nisp", NispRegistration)):
            record_id = await db.scalar(select(model.id).where(model.order_id == order.id, model.user_id == user.id))
            if record_id:
                return success(data={"vendor": vendor, "id": record_id})
        return success(data=None)


@router.get("/{vendor}/catalog")
async def catalog(vendor: CertificationVendor):
    return success(data=await service.catalog(vendor))


async def _plan_attachments_response(vendor: str, plan_id: int):
    from app.domain.plan.src.index import Plan
    from app.services.cert_attachment import (
        list_plan_attachments,
        plan_belongs_to_vendor,
    )

    async with get_db_ctx() as db:
        plan = await db.get(Plan, plan_id)
        if plan is None or plan.status not in {"published", "registration_closed"}:
            raise NotFoundException("批次")
        if not await plan_belongs_to_vendor(db, plan, vendor):
            raise NotFoundException("批次")
        return success(data=await list_plan_attachments(db, vendor, plan_id))


async def _resolve_plan_id(db, vendor: str, batch_id: int) -> int | None:
    """旧客户端传厂商批次 ID；H3C/NISP 需映射为 Plan ID，Sangfor 本身就是 Plan ID。"""
    if vendor == "h3c":
        from app.domain.h3c.src.index import H3cExamBatch

        batch = await db.get(H3cExamBatch, batch_id)
        if batch is not None:
            return batch.plan_id
        return None
    elif vendor == "nisp":
        from app.domain.nisp.src.index import NispExamBatch

        batch = await db.get(NispExamBatch, batch_id)
        if batch is not None:
            return batch.plan_id
        return None
    return batch_id


@router.get("/{vendor}/batches/{batch_id}/attachments")
async def batch_attachments(
    vendor: CertificationVendor,
    batch_id: int = Path(ge=1),
):
    """兼容旧路径：厂商批次 ID → Plan ID 后返回批次级 + 厂商级附件。"""
    async with get_db_ctx() as db:
        plan_id = await _resolve_plan_id(db, vendor, batch_id)
    if plan_id is None:
        raise NotFoundException("批次")
    return await _plan_attachments_response(vendor, plan_id)


@router.get("/{vendor}/plans/{plan_id}/attachments")
async def plan_attachments(
    vendor: CertificationVendor,
    plan_id: int = Path(ge=1),
):
    """推荐路径：直接使用批次（Plan）ID。"""
    return await _plan_attachments_response(vendor, plan_id)


@router.get("/{vendor}/notice/{batch_id}")
async def notice(vendor: CertificationVendor, batch_id: int = Path(ge=1), registration_type: str | None = Query(None, max_length=20)):
    value = await service.notice(vendor, batch_id, registration_type)
    return success(data={key: item for key, item in value.items() if not key.startswith("_")})


@router.get("/{vendor}/registrations/{registration_id}")
async def journey(vendor: CertificationVendor, registration_id: int = Path(ge=1), user=Depends(get_current_user)):
    return success(data=await user_journey(vendor, registration_id, user.id))


@admin_router.get("/{vendor}/catalog")
async def admin_catalog(vendor: CertificationVendor, admin=Depends(journey_admin)):
    return success(data=await service.catalog(vendor, admin=True))


@admin_router.get("/{vendor}/content")
async def get_content(vendor: CertificationVendor, plan_id: int | None = Query(None, ge=1), admin=Depends(journey_admin)):
    return success(data=await service.get_content(vendor, plan_id))


@admin_router.put("/{vendor}/content")
async def save_content(vendor: CertificationVendor, body: JourneyContentUpdate, plan_id: int | None = Query(None, ge=1), admin=Depends(journey_admin)):
    return success(data=await service.update_content(vendor, body, admin.id, plan_id))
