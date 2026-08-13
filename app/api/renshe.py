from fastapi import APIRouter, Depends, File, Path, Query, Request, UploadFile

from app.domain.user.src.index import User
from app.integrations.renshe_storage import RensheObjectStorage
from app.middleware.auth import get_current_user
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.renshe import (
    RensheApplicationDetailResponse,
    RensheApplicationListItem,
    RensheApplicationResponse,
    RensheApplicationStatus,
    RensheApplicationSubmitResponse,
    RensheDraftUpsert,
    RensheMaterialKind,
    RensheRefundCreate,
    RensheRefundResponse,
    RensheSignedUrlResponse,
    RensheVerificationMaterialResponse,
)
from app.services.renshe_application import RensheApplicationService
from app.services.renshe_audit import record_best_effort_audit


router = APIRouter(prefix="/renshe", tags=["人社报名"])
MAX_MATERIAL_READ_BYTES = 20 * 1024 * 1024 + 1


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post(
    "/verification-materials/{kind}",
    response_model=APIResponse[RensheVerificationMaterialResponse],
    summary="上传实名或学生认证材料",
    description="仅接受首版六类私有材料；后端校验扩展名、MIME、文件头和大小。",
)
async def upload_verification_material(
    kind: RensheMaterialKind = Path(..., description="材料槽位"),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
) -> APIResponse[RensheVerificationMaterialResponse]:
    content = await file.read(MAX_MATERIAL_READ_BYTES)
    try:
        stored = await RensheObjectStorage().save_source(
            user_id=current_user.id,
            kind=kind,
            filename=file.filename or "material",
            content_type=file.content_type,
            data=content,
        )
    except Exception as exc:
        await record_best_effort_audit(
            actor_type="user",
            actor_id=current_user.id,
            action="verification_material.upload",
            object_type="verification_material",
            object_id=current_user.id,
            result="failed",
            summary={"kind": kind, "error_type": type(exc).__name__},
        )
        raise
    await record_best_effort_audit(
        actor_type="user",
        actor_id=current_user.id,
        action="verification_material.upload",
        object_type="verification_material",
        object_id=current_user.id,
        result="succeeded",
        summary={"kind": kind, "size_bytes": stored.size_bytes},
    )
    return success(
        data=RensheVerificationMaterialResponse(
            kind=kind,
            storage_key=stored.storage_key,
            original_filename=stored.original_filename,
            content_type=stored.content_type,
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
        )
    )


@router.get(
    "/verification-materials/{kind}/signed-url",
    response_model=APIResponse[RensheSignedUrlResponse],
    summary="获取本人认证材料私有预览或下载短链接",
    description="只能访问当前微信账号已提交的材料；签名最长 5 分钟并记录访问审计。",
)
async def get_own_verification_material_signed_url(
    request: Request,
    kind: RensheMaterialKind = Path(...),
    download: bool = Query(False),
    current_user: User = Depends(get_current_user),
) -> APIResponse[RensheSignedUrlResponse]:
    from app.services.renshe_export import RensheExportService

    result = await RensheExportService().user_verification_material_signed_url(
        user_id=current_user.id,
        kind=kind,
        download=download,
        ip_address=_client_ip(request),
    )
    return success(data=result)


@router.post(
    "/applications/draft",
    response_model=APIResponse[RensheApplicationResponse],
    summary="新建或保存人社报名草稿",
    description="仅实名认证和学生信息均已人工通过的微信登录用户可用。",
)
async def save_application_draft(
    body: RensheDraftUpsert,
    current_user: User = Depends(get_current_user),
) -> APIResponse[RensheApplicationResponse]:
    result = await RensheApplicationService().save_draft(current_user.id, body)
    return success(data=result)


@router.get(
    "/applications",
    response_model=APIResponse[PaginatedData[RensheApplicationListItem]],
    summary="查看本人人社报名列表",
    description=(
        "仅返回当前微信账号的草稿、待支付、审核中、驳回、通过及已关闭历史，"
        "用于换机、重装或 Token 刷新后的报名恢复入口。"
    ),
)
async def list_own_applications(
    plan_id: int | None = Query(None, ge=1, description="按批次 ID 筛选"),
    status: RensheApplicationStatus | None = Query(None, description="按报名状态筛选"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[PaginatedData[RensheApplicationListItem]]:
    result = await RensheApplicationService().list_applications(
        current_user.id,
        plan_id=plan_id,
        status=status,
        page=page,
        page_size=page_size,
    )
    return success(data=result)


@router.post(
    "/applications/{application_id}/submit",
    response_model=APIResponse[RensheApplicationSubmitResponse],
    summary="提交或重提人社报名",
    description="生成不可变版本；首次提交按批次价格创建 60 分钟待支付订单。",
)
async def submit_application(
    application_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse[RensheApplicationSubmitResponse]:
    result = await RensheApplicationService().submit(current_user.id, application_id)
    return success(data=result)


@router.get(
    "/applications/{application_id}",
    response_model=APIResponse[RensheApplicationDetailResponse],
    summary="查看本人人社报名详情",
)
async def get_application(
    application_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse[RensheApplicationDetailResponse]:
    result = await RensheApplicationService().get_application(
        current_user.id, application_id
    )
    return success(data=result)


@router.post(
    "/applications/{application_id}/cancel-payment",
    response_model=APIResponse,
    summary="取消待支付报名订单",
    description="关闭当前待支付订单、释放名额并把报名恢复为草稿，保留版本历史。",
)
async def cancel_application_payment(
    application_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse:
    await RensheApplicationService().cancel_pending_payment(
        current_user.id, application_id
    )
    return success(message="待支付订单已取消，报名已恢复为草稿")


@router.post(
    "/applications/{application_id}/refunds",
    response_model=APIResponse[RensheRefundResponse],
    summary="申请人社报名全额退款",
    description="申请后冻结报名；普通退款仅限外审通过前，例外退款必须填写原因。",
)
async def request_refund(
    body: RensheRefundCreate,
    application_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse[RensheRefundResponse]:
    from app.services.renshe_refund import RensheRefundService

    result = await RensheRefundService().request_refund(
        user_id=current_user.id,
        application_id=application_id,
        data=body,
    )
    return success(data=result)


@router.get(
    "/refunds/{refund_id}",
    response_model=APIResponse[RensheRefundResponse],
    summary="查看本人退款进度",
)
async def get_refund(
    refund_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse[RensheRefundResponse]:
    from app.services.renshe_refund import RensheRefundService

    result = await RensheRefundService().get_refund(current_user.id, refund_id)
    return success(data=result)
