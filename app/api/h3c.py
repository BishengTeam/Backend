from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Path, Query, UploadFile

from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.h3c_registration import (
    H3cMaterialUploadResponse,
    H3cOrderCreate,
    H3cProfileDefaults,
    H3cRegistrationResponse,
    H3cResubmissionCreate,
    H3cUserExamBatchResponse,
)
from app.services.h3c_registration import H3cRegistrationService


router = APIRouter(prefix="/h3c", tags=["H3C 认证报名"])
order_router = APIRouter(prefix="/orders/h3c", tags=["H3C 认证报名"])


@order_router.get(
    "/profile",
    response_model=APIResponse[H3cProfileDefaults],
    summary="获取 H3C 报名预填值",
)
async def get_h3c_profile_defaults(
    current_user: User = Depends(get_current_user),
) -> APIResponse[H3cProfileDefaults]:
    return success(
        data=await H3cRegistrationService().get_profile_defaults(current_user.id)
    )


@router.get(
    "/exam-batches",
    response_model=APIResponse[list[H3cUserExamBatchResponse]],
    summary="获取可报名 H3C 考试批次",
)
async def list_exam_batches(
    current_user: User = Depends(get_current_user),
) -> APIResponse[list[H3cUserExamBatchResponse]]:
    return success(data=await H3cRegistrationService().list_user_batches())


@router.post(
    "/materials",
    response_model=APIResponse[H3cMaterialUploadResponse],
    summary="上传 H3C 证明材料",
)
async def upload_material(
    batch_id: int = Form(..., gt=0),
    material_type: str = Form(...),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
) -> APIResponse[H3cMaterialUploadResponse]:
    data = await file.read()
    result = await H3cRegistrationService().upload_material(
        user_id=current_user.id,
        batch_id=batch_id,
        material_type=material_type,
        filename=file.filename or "material.jpg",
        content_type=file.content_type,
        data=data,
    )
    return success(data=result)


@order_router.post(
    "",
    response_model=APIResponse[H3cRegistrationResponse],
    summary="创建 H3C 报名订单",
)
async def create_h3c_order(
    body: H3cOrderCreate,
    current_user: User = Depends(get_current_user),
) -> APIResponse[H3cRegistrationResponse]:
    return success(
        data=await H3cRegistrationService().create_order(
            current_user.id,
            body,
        )
    )


@router.get(
    "/registrations",
    response_model=APIResponse[PaginatedData[H3cRegistrationResponse]],
    summary="我的 H3C 报名列表",
)
async def list_my_registrations(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
) -> APIResponse[PaginatedData[H3cRegistrationResponse]]:
    return success(
        data=await H3cRegistrationService().list_registrations(
            current_user.id,
            page=page,
            page_size=page_size,
        )
    )


@router.get(
    "/registrations/{registration_id}",
    response_model=APIResponse[H3cRegistrationResponse],
    summary="我的 H3C 报名详情",
)
async def get_my_registration(
    registration_id: int = Path(..., gt=0),
    current_user: User = Depends(get_current_user),
) -> APIResponse[H3cRegistrationResponse]:
    return success(
        data=await H3cRegistrationService().get_registration(
            current_user.id,
            registration_id,
        )
    )


@router.post(
    "/registrations/{registration_id}/cancel-payment",
    response_model=APIResponse[H3cRegistrationResponse],
    summary="取消待支付 H3C 报名",
)
async def cancel_payment(
    registration_id: int = Path(..., gt=0),
    current_user: User = Depends(get_current_user),
) -> APIResponse[H3cRegistrationResponse]:
    return success(
        data=await H3cRegistrationService().cancel_pending_payment(
            current_user.id,
            registration_id,
        )
    )


@router.post(
    "/registrations/{registration_id}/materials",
    response_model=APIResponse[H3cRegistrationResponse],
    summary="重新提交被拒绝的 H3C 材料",
)
async def resubmit_materials(
    registration_id: int = Path(..., gt=0),
    body: H3cResubmissionCreate = ...,
    current_user: User = Depends(get_current_user),
) -> APIResponse[H3cRegistrationResponse]:
    return success(
        data=await H3cRegistrationService().resubmit_materials(
            current_user.id,
            registration_id,
            body,
        )
    )
