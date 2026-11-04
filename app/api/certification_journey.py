from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy import select

from app.adapter.database import get_db_ctx
from app.domain.order.src.index import Order
from app.middleware.auth import get_current_user, require_normal_admin
from app.policy.permissions import ROLE_PERMISSIONS
from app.port.exceptions import ForbiddenException, NotFoundException
from app.schemas.certification_journey import CertificationVendor, JourneyContentUpdate
from app.schemas.common import success
from app.services.certification_journey import CertificationJourneyService, user_journey

router = APIRouter(prefix="/cert/enrollment", tags=["认证统一报名"])
admin_router = APIRouter(prefix="/certification-journeys", tags=["管理后台-认证报名须知"])
service = CertificationJourneyService()
WRITE_PERMISSION = {"h3c": "h3c:batch_manage", "nisp": "nisp:batch_manage", "renshe": "user:write", "sangfor": "content:write"}


async def journey_admin(request: Request, vendor: CertificationVendor, admin=Depends(require_normal_admin)):
    allowed = ROLE_PERMISSIONS.get(admin.role, [])
    permission = WRITE_PERMISSION[vendor] if request.method == "PUT" else "content:read"
    if admin.role != "super_admin" and "*" not in allowed and permission not in allowed:
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
