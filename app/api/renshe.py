from fastapi import APIRouter, Depends, File, Path, Query, Request, UploadFile
from fastapi.responses import FileResponse

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
    RensheApplicationMaterialKind,
    RensheRefundCreate,
    RensheRefundResponse,
    RensheSignedUrlResponse,
    RensheVerificationMaterialResponse,
)
from app.services.renshe_application import RensheApplicationService
from app.services.renshe_audit import record_best_effort_audit


router = APIRouter(prefix="/renshe", tags=["人社报名"])
MAX_MATERIAL_READ_BYTES = 20 * 1024 * 1024 + 1


@router.get("/projects")
async def list_projects(current_user: User = Depends(get_current_user)):
    from app.services.renshe_project import RensheProjectService
    # Public responses contain only business data; no internal code or mappings.
    projects = await RensheProjectService().list_projects(public=True)
    return success(data=[{"id": item.id, "name": item.chinese_name, "description": item.description,
                          "home_cover_url": item.home_cover_url, "occupation_name": item.config.occupation_name,
                          "occupation_code": item.config.occupation_code,
                          "skill_level": item.config.skill_level,
                          "qualification_rule": item.config.qualification_rule,
                          "application_types": item.config.application_types,
                          "education_levels": item.config.education_levels,
                          "institution_types": item.config.institution_types,
                          "materials": [material.model_dump() for material in item.config.materials],
                          "admission_notice": "本期仅接受已取得毕业证、本专业或相关专业申请人；学历及专业以报名初审为准。无法提供学历备案表请联系管理员。" if item.config.qualification_rule == "related_major_graduated_degree_v1" else "历史项目仅办理已有报名。本期新报名请选择已毕业学历型批次。"} for item in projects])


@router.get("/batches")
async def list_batches(project_id: int | None = Query(None, ge=1), current_user: User = Depends(get_current_user)):
    from app.services.plan import PlanService
    return success(data=await PlanService().list_renshe_plans(public=True, project_id=project_id))


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("/batches/{plan_id}/reporting-options")
async def reporting_options(plan_id: int = Path(..., ge=1), current_user: User = Depends(get_current_user)):
    from app.adapter.database import get_db_ctx
    from app.domain.plan.src.index import Plan
    from app.services.renshe_project import batch_configuration, is_renshe_plan
    from app.services.renshe_reporting import resolve_contract, public_options
    from app.port.exceptions import NotFoundException
    async with get_db_ctx() as db:
        plan = await db.get(Plan, plan_id)
        if not await is_renshe_plan(db, plan) or plan.status == "draft":
            raise NotFoundException("人社批次")
        return success(data=public_options(await resolve_contract(db, plan), batch_configuration(plan)))


@router.post("/batches/{plan_id}/materials/{kind}")
async def upload_application_material(
    plan_id: int = Path(..., ge=1),
    kind: RensheApplicationMaterialKind = Path(...),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    result = await RensheApplicationService().upload_application_material(
        current_user.id, plan_id, kind, filename=file.filename or "material",
        content_type=file.content_type, content=await file.read(500 * 1024 + 1 if kind == "registration_portrait" else MAX_MATERIAL_READ_BYTES),
    )
    return success(data=result)


@router.get("/batches/{plan_id}/commitment-template")
async def download_commitment_template(plan_id: int = Path(..., ge=1), reporting_config_id: int | None = Query(None, ge=1), current_user: User = Depends(get_current_user)):
    from app.adapter.database import get_db_ctx
    from app.domain.plan.src.index import Plan
    from app.services.renshe_project import batch_configuration, is_renshe_plan
    from app.services.renshe_graduated import is_graduated, template_path
    from app.port.exceptions import NotFoundException
    async with get_db_ctx() as db:
        plan = await db.get(Plan, plan_id)
        if not await is_renshe_plan(db, plan):
            raise NotFoundException("人社批次")
        config = batch_configuration(plan)
        if not is_graduated(config) or plan.status == "draft":
            raise NotFoundException("承诺书模板")
        if reporting_config_id is not None:
            from app.services.renshe_reporting import resolve_contract
            config = (await resolve_contract(db, plan, reporting_config_id))["config"]
        path = template_path(config["reporting_policy"])
    return FileResponse(path, filename=path.name)


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
    description="依据批次冻结配置校验本人实名与报名条件；学历型报名独立审核毕业学历。",
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
