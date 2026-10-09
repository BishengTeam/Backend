import re
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile

from sqlalchemy import func, select

from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.port.exceptions import BusinessException
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.nisp import (
    NispMaterialUploadResponse,
    NispBatchListItem,
    NispOrderCreate,
    NispRegistrationResponse,
    NispResubmitRequest,
)
from app.services.nisp_registration import NispRegistrationService
from app.services.nisp_lifecycle import occupied_count

router = APIRouter(prefix="/nisp", tags=["NISP认证"])
_service = NispRegistrationService()


@router.get("/batches", response_model=APIResponse[list[NispBatchListItem]])
async def list_batches(
    level: Literal["1", "2"] | None = Query(
        None,
        description="NISP 级别：1=一级，2=二级；不传返回全部级别",
    ),
) -> APIResponse[list[NispBatchListItem]]:
    """List published NISP exam batches."""
    from app.adapter.database import get_db_ctx
    from app.domain.nisp.src import NispExamBatch
    from app.domain.plan.src.index import Plan
    from app.domain.order.src.index import Order

    async with get_db_ctx() as db:
        stmt = (
            select(NispExamBatch, Plan)
            .join(Plan, Plan.id == NispExamBatch.plan_id)
            .where(Plan.status == "published")
            .order_by(Plan.sort_order.desc(), NispExamBatch.id.desc())
        )
        if level is not None:
            stmt = stmt.where(NispExamBatch.level == level)
        rows = (await db.execute(stmt)).all()
        items = []
        for batch, plan in rows:
            occupied = await occupied_count(db, plan.id)
            remaining = max(0, plan.capacity - occupied) if plan.capacity > 0 else -1
            items.append(NispBatchListItem(
                id=batch.id,
                level=batch.level,
                name=plan.name,
                status=plan.status,
                apply_start=plan.apply_start,
                apply_end=plan.apply_end,
                exam_date=plan.exam_date,
                exam_location=plan.exam_location,
                remaining_count=remaining,
                level1_price_cents=batch.level1_price_cents,
                level2_price_cents=batch.level2_price_cents,
                payment_timeout_minutes=batch.payment_timeout_minutes,
                max_material_bytes=batch.max_material_bytes,
            ))
        return success(data=items)


@router.post("/materials/upload")
async def upload_material(
    material_type: str = Form(...),
    original_filename: str | None = Form(None, max_length=256),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
) -> APIResponse[NispMaterialUploadResponse]:
    """Upload a NISP registration material to OSS and return the storage key."""
    from app.adapter.database import get_db_ctx
    from app.domain.nisp.src import NispMaterialFile
    from app.integrations.nisp_storage import NispObjectStorage

    data = await file.read()
    if not data:
        raise BusinessException("文件不能为空")

    # Validate type
    allowed_types = ("id_card_both_sides", "portrait_photo", "xuexin_report", "application_form")
    if material_type not in allowed_types:
        raise BusinessException(f"不支持的材料类型: {material_type}")

    storage = NispObjectStorage()
    storage_key, size_bytes, sha256 = await storage.save_source(
        user_id=current_user.id,
        material_type=material_type,
        filename=file.filename or "material",
        content_type=file.content_type,
        data=data,
    )
    supplied_filename = (
        original_filename
        if isinstance(original_filename, str) and original_filename
        else (file.filename or "")
    )
    original_filename = re.sub(
        r'[\\/:*?"<>|\x00-\x1f]+',
        "",
        supplied_filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1],
    ).strip() or None
    normalized_type = (file.content_type or "").split(";", 1)[0].strip().lower()

    async with get_db_ctx() as db:
        material = NispMaterialFile(
            user_id=current_user.id,
            material_type=material_type,
            storage_key=storage_key,
            original_filename=original_filename,
            content_type=normalized_type or None,
            size_bytes=size_bytes,
            sha256=sha256,
            is_current=False,
        )
        db.add(material)
        await db.commit()
        await db.refresh(material)

    # 必须包 APIResponse 信封：前端按 {code: 0, data} 判定成功，
    # 裸 dict 的 code 是 undefined 会被当作业务错误（“材料上传失败”）。
    return success(
        data=NispMaterialUploadResponse(
            material_type=material_type,
            material_id=material.id,
            storage_key=storage_key,
            original_filename=material.original_filename,
            content_type=material.content_type,
            size_bytes=size_bytes,
            sha256=sha256,
        )
    )


@router.post("/orders", response_model=APIResponse[NispRegistrationResponse])
async def create_order(
    body: NispOrderCreate,
    current_user: User = Depends(get_current_user),
) -> APIResponse[NispRegistrationResponse]:
    return success(data=await _service.create_order(current_user.id, body))


@router.get("/registrations", response_model=APIResponse[PaginatedData[NispRegistrationResponse]])
async def list_registrations(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
) -> APIResponse[PaginatedData[NispRegistrationResponse]]:
    result = await _service.list_user_registrations(
        current_user.id, page=page, page_size=page_size
    )
    return success(data=PaginatedData(**result))


@router.get("/registrations/{registration_id}", response_model=APIResponse[NispRegistrationResponse])
async def get_registration(
    registration_id: int,
    current_user: User = Depends(get_current_user),
) -> APIResponse[NispRegistrationResponse]:
    return success(data=await _service.get_registration(current_user.id, registration_id))


@router.post(
    "/registrations/{registration_id}/cancel-payment",
    response_model=APIResponse[NispRegistrationResponse],
)
async def cancel_payment(
    registration_id: int,
    current_user: User = Depends(get_current_user),
) -> APIResponse[NispRegistrationResponse]:
    return success(
        data=await _service.cancel_pending_payment(current_user.id, registration_id)
    )


@router.post(
    "/registrations/{registration_id}/materials",
    response_model=APIResponse[NispRegistrationResponse],
)
async def resubmit_materials(
    registration_id: int,
    body: NispResubmitRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[NispRegistrationResponse]:
    return success(
        data=await _service.resubmit_materials(current_user.id, registration_id, body)
    )
