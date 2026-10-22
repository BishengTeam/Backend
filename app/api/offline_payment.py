from urllib.parse import quote

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response

from app.middleware.auth import (get_current_user, require_permission,
    require_reauthenticated_admin, require_reauthenticated_super_admin)
from app.schemas.common import success
from app.schemas.offline_payment import ConfirmInput, DecisionInput, ExceptionRefundInput, RefundInput, SettingsInput, SubmissionInput
from app.services.offline_payment import OfflinePaymentService

router = APIRouter(prefix="/orders", tags=["银行转账"])
admin_router = APIRouter(prefix="/orders", tags=["管理后台-线下核账"])
service = OfflinePaymentService()


@admin_router.get("/offline-payment-summary")
async def summary(admin=Depends(require_permission("order:list"))):
    from sqlalchemy import func, select
    from app.adapter.database import get_db_ctx
    from app.domain.order.src.index import Order
    from app.domain.plan.src.index import Plan
    from app.models.cert_product import CertProduct
    async with get_db_ctx() as db:
        counts = (await db.execute(select(Order.offline_status, func.count()).where(
            Order.payment_channel == "bank_transfer").group_by(Order.offline_status))).all()
        catalog = (await db.execute(select(CertProduct.code, CertProduct.chinese_name))).all()
        historical = list(await db.scalars(select(Order.product_type).distinct()))
        products = {code: name or code for code, name in catalog}
        for code in historical:
            products.setdefault(code, code)
        plans = (await db.execute(select(Plan.id, Plan.name))).all()
        return success(data={"counts": {key: count for key, count in counts if key},
            "products": [{"value": code, "label": name or code} for code, name in products.items()],
            "batches": [{"value": id, "label": name} for id, name in plans]})


@router.get("/{order_id}/offline-payment")
async def detail(order_id: int, user=Depends(get_current_user)):
    return success(data=await service.detail(order_id, user.id))


@router.post("/{order_id}/offline-payment/materials")
async def upload(order_id: int, file: UploadFile = File(...), user=Depends(get_current_user)):
    return success(data=await service.upload(order_id, file, user_id=user.id))


def content_response(result):
    data, content_type, filename = result
    return Response(data, media_type=content_type, headers={"Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff", "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}"})


@router.get("/{order_id}/offline-payment/materials/{material_id}/content")
async def content(order_id: int, material_id: int, user=Depends(get_current_user)):
    return content_response(await service.content(order_id, material_id, user.id))


@router.post("/{order_id}/offline-payment/submissions")
async def submit(order_id: int, body: SubmissionInput, user=Depends(get_current_user)):
    return success(data=await service.submit(order_id, user.id, body))


@router.post("/{order_id}/offline-payment/free")
async def settle_free(order_id: int, user=Depends(get_current_user)):
    return success(data=await service.settle_free(order_id, user.id))


@router.post("/{order_id}/offline-payment/exceptions")
async def exception(order_id: int, body: SubmissionInput, user=Depends(get_current_user)):
    return success(data=await service.submit(order_id, user.id, body, exception=True))


@admin_router.get("/offline-payment-settings")
async def settings(admin=Depends(require_permission("offline:settings"))):
    return success(data=await service.get_settings())


@admin_router.put("/offline-payment-settings")
async def save_settings(body: SettingsInput, admin=Depends(require_reauthenticated_super_admin)):
    return success(data=await service.save_settings(admin.id, body))


@admin_router.get("/{order_id}/offline-payment")
async def admin_detail(order_id: int, admin=Depends(require_permission("offline:view"))):
    return success(data=await service.detail(order_id))


@admin_router.get("/{order_id}/offline-payment/materials/{material_id}/content")
async def admin_content(order_id: int, material_id: int, admin=Depends(require_permission("offline:view"))):
    return content_response(await service.content(order_id, material_id))


@admin_router.post("/{order_id}/offline-payment/materials")
async def admin_upload(order_id: int, file: UploadFile = File(...), admin=Depends(require_permission("offline:refund"))):
    return success(data=await service.upload(order_id, file, admin_id=admin.id))


@admin_router.post("/{order_id}/offline-payment/confirm")
async def confirm(order_id: int, body: ConfirmInput, admin=Depends(require_permission("offline:confirm")),
                  reauth=Depends(require_reauthenticated_admin)):
    return success(data=await service.decide(order_id, admin.id, body, "confirm"))


@admin_router.post("/{order_id}/offline-payment/return")
async def return_submission(order_id: int, body: DecisionInput, admin=Depends(require_permission("offline:return"))):
    return success(data=await service.decide(order_id, admin.id, body, "return"))


@admin_router.post("/{order_id}/offline-payment/exception")
async def mark_exception(order_id: int, body: DecisionInput, admin=Depends(require_permission("offline:confirm"))):
    return success(data=await service.decide(order_id, admin.id, body, "exception"))


@admin_router.post("/{order_id}/offline-payment/refund")
async def record_refund(order_id: int, body: RefundInput, admin=Depends(require_permission("offline:refund")),
                        reauth=Depends(require_reauthenticated_admin)):
    return success(data=await service.refund(order_id, admin.id, body))


@admin_router.post("/{order_id}/offline-payment/resolve")
async def resolve_exception(order_id: int, body: ExceptionRefundInput,
                            admin=Depends(require_permission("offline:refund")),
                            reauth=Depends(require_reauthenticated_admin)):
    return success(data=await service.resolve_exception(order_id, admin.id, body))


@admin_router.post("/{order_id}/offline-payment/refund-approval")
async def approve_generic_refund(order_id: int, body: DecisionInput,
                                 admin=Depends(require_permission("order:write")),
                                 reauth=Depends(require_reauthenticated_admin)):
    return success(data=await service.approve_generic_refund(order_id, admin.id, body))
