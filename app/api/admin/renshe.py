from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, File, Path, Query, Request, UploadFile
from fastapi.responses import FileResponse
from app.schemas.renshe_project import RensheProjectWrite, RensheProjectResponse
from app.services.renshe_project import RensheProjectService
from app.port.exceptions import BusinessException

from app.middleware.auth import require_permission, require_super_admin
from app.middleware.rate_limit import limiter
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.renshe import (
    RensheAdminApplicationDetailResponse,
    RensheAdminApplicationListItem,
    RensheAuditLogResponse,
    RensheApplicationStatus,
    RensheBatchExternalReviewRequest,
    RensheBatchExternalReviewResponse,
    RensheCleanupRunResponse,
    RensheExportCreate,
    RensheExportJobResponse,
    RensheReviewCorrectionCreate,
    RensheReviewCorrectionResponse,
    RensheReviewCreate,
    RensheReviewResponse,
    RensheRefundDecision,
    RensheRefundResponse,
    RensheProgramResponse,
    RensheSignedUrlResponse,
    RensheMaterialKind,
)
from app.domain.renshe.src.index import RENSHE_PROGRAMS
from app.services.renshe_review import RensheReviewService


router = APIRouter(prefix="/cert-products/renshe", tags=["管理后台-人社报名"])


@router.get("/project-capabilities", response_model=APIResponse[dict])
async def project_capabilities(_admin=Depends(require_permission("content:read"))):
    return success(data=RensheProjectService.capabilities())


@router.get("/export-template")
async def download_current_registration_template(_admin=Depends(require_permission("content:read"))):
    from app.services.renshe_export import RensheExportService
    registration, _ = RensheExportService._template_paths()
    return FileResponse(registration, filename=registration.name)


@router.post("/commitment-templates")
async def upload_commitment_template(file: UploadFile = File(...), admin=Depends(require_permission("content:write"))):
    from app.services.renshe_graduated import save_template
    from app.services.renshe_audit import record_best_effort_audit
    template = save_template(file.filename or "template", await file.read(10 * 1024 * 1024 + 1))
    await record_best_effort_audit(actor_type="admin", actor_id=admin.id, action="commitment_template.upload",
                                 object_type="commitment_template", object_id=0, result="succeeded", summary={"sha256": template["sha256"]})
    return success(data=template)


@router.get("/commitment-templates/{filename}")
async def download_commitment_template(filename: str, _admin=Depends(require_permission("content:read"))):
    import hashlib
    import re
    from pathlib import Path as FilePath
    from app.port.config import settings
    from app.port.exceptions import NotFoundException
    if not re.fullmatch(r"commitment_[a-f0-9]{64}\.(pdf|docx)", filename):
        raise NotFoundException("承诺书模板")
    path = FilePath(settings.RENSHE_TEMPLATE_DIR).resolve() / filename
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != filename[11:75]:
        raise NotFoundException("承诺书模板")
    return FileResponse(path, filename=filename)


@router.get("/projects", response_model=APIResponse[list[RensheProjectResponse]])
async def list_projects(_admin=Depends(require_permission("content:list"))):
    return success(data=await RensheProjectService().list_projects())


@router.post("/projects", response_model=APIResponse[RensheProjectResponse])
async def create_project(body: RensheProjectWrite, admin=Depends(require_permission("content:write"))):
    assert_project_selection_payload(body, creating=True)
    return success(data=await RensheProjectService().save(body, admin_id=admin.id))


@router.put("/projects/{project_id}", response_model=APIResponse[RensheProjectResponse])
async def update_project(project_id: int, body: RensheProjectWrite, admin=Depends(require_permission("content:write"))):
    assert_project_selection_payload(body, creating=False)
    return success(data=await RensheProjectService().save(body, product_id=project_id, admin_id=admin.id))


def assert_project_selection_payload(body: RensheProjectWrite, *, creating: bool) -> None:
    # Config JSON is a persisted snapshot, never an operator-authored rules API.
    if body.config is not None or body.occupation_code is not None or body.skill_level is not None:
        raise BusinessException("请更新后台并提交职业／工种、等级及申报类型的组合标识，不能提交底层配置")
    if creating and body.selection_id is None:
        raise BusinessException("请选择职业／工种、技能等级和申报类型")


@router.delete("/projects/{project_id}")
async def delete_project(project_id: int, admin=Depends(require_permission("content:write"))):
    await RensheProjectService().delete(project_id, admin_id=admin.id)
    return success(message="草稿项目已删除")


@router.get("/batches")
async def list_renshe_batches(_admin=Depends(require_permission("user:list"))):
    from app.services.plan import PlanService
    return success(data=await PlanService().list_renshe_plans())


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get(
    "/programs",
    response_model=APIResponse[list[RensheProgramResponse]],
    summary="当前支持的人社报考组合",
)
async def list_programs(
    _admin=Depends(require_permission("user:write")),
) -> APIResponse[list[RensheProgramResponse]]:
    return success(
        data=[
            RensheProgramResponse(
                program_code=program.program_code,
                occupation_name=program.occupation_name,
                occupation_code=program.occupation_code,
                skill_level=program.skill_level,
                exam_type=program.exam_type,
                application_type=program.application_type,
            )
            for program in RENSHE_PROGRAMS
        ]
    )


@router.get(
    "/applications",
    response_model=APIResponse[PaginatedData[RensheAdminApplicationListItem]],
    summary="人社报名列表",
)
async def list_applications(
    plan_id: int | None = Query(None, ge=1),
    status: RensheApplicationStatus | None = Query(None),
    payment_status: Literal["pending", "paid", "completed", "refunded", "closed"] | None = Query(None),
    keyword: str | None = Query(None, max_length=64, description="姓名、身份证号或联系电话"),
    submitted_at_start: datetime | None = Query(None),
    submitted_at_end: datetime | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin=Depends(require_permission("user:list")),
) -> APIResponse[PaginatedData[RensheAdminApplicationListItem]]:
    result = await RensheReviewService().list_applications(
        plan_id=plan_id,
        status=status,
        payment_status=payment_status,
        keyword=keyword,
        submitted_at_start=submitted_at_start,
        submitted_at_end=submitted_at_end,
        page=page,
        page_size=page_size,
    )
    return success(data=result)


@router.get(
    "/applications/{application_id}",
    response_model=APIResponse[RensheAdminApplicationDetailResponse],
    summary="人社报名详情与版本历史",
)
async def get_application(
    application_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("user:list")),
) -> APIResponse[RensheAdminApplicationDetailResponse]:
    result = await RensheReviewService().get_application(
        application_id, actor_id=_admin.id
    )
    return success(data=result)


@router.post(
    "/applications/{application_id}/initial-review",
    response_model=APIResponse[RensheReviewResponse],
    summary="人社报名初审",
    description="驳回只要求修改，不触发退款或关单。",
)
async def initial_review(
    body: RensheReviewCreate,
    application_id: int = Path(..., ge=1),
    admin=Depends(require_permission("renshe:review")),
) -> APIResponse[RensheReviewResponse]:
    result = await RensheReviewService().review(
        reviewer_id=admin.id,
        application_id=application_id,
        stage="initial",
        data=body,
    )
    return success(data=result)


@router.post(
    "/applications/{application_id}/return-correction",
    response_model=APIResponse[RensheReviewResponse],
    summary="打回补正",
    description=(
        "用于初审通过后仍发现资料问题的日常处理；保留原审核历史，"
        "生成新的初审驳回记录，考生补交后重新进入初审。"
    ),
)
async def return_application_for_correction(
    body: RensheReviewCreate,
    application_id: int = Path(..., ge=1),
    admin=Depends(require_permission("renshe:review")),
) -> APIResponse[RensheReviewResponse]:
    result = await RensheReviewService().return_for_correction(
        reviewer_id=admin.id,
        application_id=application_id,
        data=body,
    )
    return success(data=result)


@router.post(
    "/external-reviews/batch",
    response_model=APIResponse[RensheBatchExternalReviewResponse],
    summary="批量登记外审结果",
    description="逐人校验当前版本是否已成功导出；部分失败不影响其他人员。",
)
async def batch_external_review(
    body: RensheBatchExternalReviewRequest,
    admin=Depends(require_permission("renshe:review")),
) -> APIResponse[RensheBatchExternalReviewResponse]:
    result = await RensheReviewService().batch_external_review(
        reviewer_id=admin.id,
        data=body,
    )
    return success(data=RensheBatchExternalReviewResponse(items=result))


@router.post(
    "/applications/{application_id}/external-review",
    response_model=APIResponse[RensheReviewResponse],
    summary="登记外部复审结果",
)
async def external_review(
    body: RensheReviewCreate,
    application_id: int = Path(..., ge=1),
    admin=Depends(require_permission("renshe:review")),
) -> APIResponse[RensheReviewResponse]:
    result = await RensheReviewService().review(
        reviewer_id=admin.id,
        application_id=application_id,
        stage="external",
        data=body,
    )
    return success(data=result)


@router.post(
    "/reviews/{review_id}/corrections",
    response_model=APIResponse[RensheReviewCorrectionResponse],
    summary="更正审核结果（仅超级管理员）",
)
async def correct_review(
    body: RensheReviewCorrectionCreate,
    review_id: int = Path(..., ge=1),
    admin=Depends(require_super_admin),
) -> APIResponse[RensheReviewCorrectionResponse]:
    result = await RensheReviewService().correct_review(
        super_admin_id=admin.id,
        review_id=review_id,
        data=body,
    )
    return success(data=result)


@router.get(
    "/refunds",
    response_model=APIResponse[PaginatedData[RensheRefundResponse]],
    summary="退款申请工作台",
)
async def list_refunds(
    application_id: int | None = Query(None, ge=1),
    plan_id: int | None = Query(None, ge=1),
    keyword: str | None = Query(None, max_length=64),
    reason_code: str | None = Query(None, max_length=64),
    request_kind: str | None = Query(None, max_length=24),
    requested_at_start: datetime | None = Query(None),
    requested_at_end: datetime | None = Query(None),
    group: str | None = Query(None, pattern="^(pending|processing|abnormal)$"),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin=Depends(require_permission("order:list")),
) -> APIResponse[PaginatedData[RensheRefundResponse]]:
    from app.services.renshe_refund import RensheRefundService

    result = await RensheRefundService().list_refunds(
        application_id=application_id,
        plan_id=plan_id,
        keyword=keyword,
        reason_code=reason_code,
        request_kind=request_kind,
        requested_at_start=requested_at_start,
        requested_at_end=requested_at_end,
        group=group,
        status=status,
        page=page,
        page_size=page_size,
    )
    return success(data=result)


@router.post(
    "/refunds/{refund_id}/decision",
    response_model=APIResponse[RensheRefundResponse],
    summary="批准或驳回退款（仅超级管理员）",
    description=(
        "批准时固定商户退款单号并提交微信 V3；接口返回 processing 仅表示"
        "微信已受理，最终成功由退款通知或主动查询确认。"
    ),
)
@limiter.limit("10/minute")
async def decide_refund(
    request: Request,
    body: RensheRefundDecision,
    refund_id: int = Path(..., ge=1),
    admin=Depends(require_super_admin),
) -> APIResponse[RensheRefundResponse]:
    from app.services.renshe_refund import RensheRefundService

    result = await RensheRefundService().decide(
        super_admin_id=admin.id,
        refund_id=refund_id,
        data=body,
    )
    return success(data=result)


@router.post(
    "/plans/{plan_id}/exports",
    response_model=APIResponse[RensheExportJobResponse],
    summary="新建全批次导出任务",
    description="每次重新快照全批次可导出考生，并生成多个不超过 10 GiB 的独立 ZIP。",
)
async def create_export_job(
    plan_id: int = Path(..., ge=1),
    body: RensheExportCreate | None = None,
    admin=Depends(require_permission("renshe:export")),
) -> APIResponse[RensheExportJobResponse]:
    from app.services.renshe_export import RensheExportService

    result = await RensheExportService().create_job(
        plan_id=plan_id,
        admin_id=admin.id,
        application_ids=body.application_ids if body else None,
    )
    return success(data=result, message="导出任务已创建")


@router.get(
    "/plans/{plan_id}/exports",
    response_model=APIResponse[list[RensheExportJobResponse]],
    summary="查看批次导出历史",
)
async def list_export_jobs(
    plan_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("user:list")),
) -> APIResponse[list[RensheExportJobResponse]]:
    from app.services.renshe_export import RensheExportService

    return success(data=await RensheExportService().list_jobs(plan_id))


@router.get(
    "/exports/{job_id}",
    response_model=APIResponse[RensheExportJobResponse],
    summary="查看导出进度与分卷",
)
async def get_export_job(
    job_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("user:list")),
) -> APIResponse[RensheExportJobResponse]:
    from app.services.renshe_export import RensheExportService

    return success(data=await RensheExportService().get_job(job_id))


@router.post(
    "/exports/{job_id}/retry",
    response_model=APIResponse[RensheExportJobResponse],
    summary="重试失败的导出任务",
)
async def retry_export_job(
    job_id: int = Path(..., ge=1),
    admin=Depends(require_permission("renshe:export")),
) -> APIResponse[RensheExportJobResponse]:
    from app.services.renshe_export import RensheExportService

    result = await RensheExportService().retry_job(job_id=job_id, admin_id=admin.id)
    return success(data=result, message="导出任务已重新排队")


@router.get(
    "/materials/{material_id}/signed-url",
    response_model=APIResponse[RensheSignedUrlResponse],
    summary="获取材料私有预览或下载短链接",
    description="签名最长 5 分钟；每次请求均记录管理员、材料、版本、IP 和结果。",
)
async def get_material_signed_url(
    request: Request,
    material_id: int = Path(..., ge=1),
    download: bool = Query(False, description="true 为下载，false 为预览"),
    admin=Depends(require_permission("user:list")),
) -> APIResponse[RensheSignedUrlResponse]:
    from app.services.renshe_export import RensheExportService

    result = await RensheExportService().material_signed_url(
        material_id=material_id,
        admin_id=admin.id,
        download=download,
        ip_address=_client_ip(request),
    )
    return success(data=result)


@router.get(
    "/users/{user_id}/verification-materials/{kind}/signed-url",
    response_model=APIResponse[RensheSignedUrlResponse],
    summary="获取待审核实名/学生材料短链接",
    description="供管理员在报名建立前人工审核认证资料；预览和下载均留痕。",
)
async def get_verification_material_signed_url(
    request: Request,
    user_id: int = Path(..., ge=1),
    kind: RensheMaterialKind = Path(...),
    download: bool = Query(False),
    admin=Depends(require_permission("user:list")),
) -> APIResponse[RensheSignedUrlResponse]:
    from app.services.renshe_export import RensheExportService

    result = await RensheExportService().verification_material_signed_url(
        user_id=user_id,
        kind=kind,
        admin_id=admin.id,
        download=download,
        ip_address=_client_ip(request),
    )
    return success(data=result)


@router.get(
    "/export-volumes/{volume_id}/signed-url",
    response_model=APIResponse[RensheSignedUrlResponse],
    summary="获取导出分卷下载短链接",
    description="只允许下载生成成功且尚未清理的分卷，并记录下载审计。",
)
async def get_export_volume_signed_url(
    request: Request,
    volume_id: int = Path(..., ge=1),
    admin=Depends(require_permission("renshe:export")),
) -> APIResponse[RensheSignedUrlResponse]:
    from app.services.renshe_export import RensheExportService

    result = await RensheExportService().volume_signed_url(
        volume_id=volume_id,
        admin_id=admin.id,
        ip_address=_client_ip(request),
    )
    return success(data=result)


@router.get(
    "/plans/{plan_id}/cleanup-runs",
    response_model=APIResponse[list[RensheCleanupRunResponse]],
    summary="查看批次级清理任务",
)
async def list_cleanup_runs(
    plan_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("user:list")),
) -> APIResponse[list[RensheCleanupRunResponse]]:
    from app.services.renshe_cleanup import RensheCleanupService

    return success(data=await RensheCleanupService().list_runs(plan_id))


@router.get(
    "/audit-logs",
    response_model=APIResponse[PaginatedData[RensheAuditLogResponse]],
    summary="查询人社审计日志",
    description=(
        "按批次、报名、管理员、动作、结果和时间分页查询脱敏审计；"
        "该资源只读，没有更新、删除或敏感原文导出接口。"
    ),
)
async def list_audit_logs(
    request: Request,
    plan_id: int | None = Query(None, ge=1),
    application_id: int | None = Query(None, ge=1),
    admin_id: int | None = Query(None, ge=1, description="按操作管理员筛选"),
    action: str | None = Query(None, min_length=1, max_length=64),
    result: Literal["succeeded", "failed"] | None = Query(None),
    started_at: datetime | None = Query(None),
    ended_at: datetime | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin=Depends(require_permission("user:list")),
) -> APIResponse[PaginatedData[RensheAuditLogResponse]]:
    from app.services.renshe_audit import RensheAuditQueryService

    rows = await RensheAuditQueryService().list_logs(
        admin_id=admin.id,
        ip_address=_client_ip(request),
        plan_id=plan_id,
        application_id=application_id,
        actor_admin_id=admin_id,
        action=action,
        result=result,
        started_at=started_at,
        ended_at=ended_at,
        page=page,
        page_size=page_size,
    )
    return success(data=rows)


@router.post(
    "/cleanup-runs/{run_id}/retry",
    response_model=APIResponse[RensheCleanupRunResponse],
    summary="重试批次清理任务（仅超级管理员）",
)
async def retry_cleanup_run(
    run_id: int = Path(..., ge=1),
    admin=Depends(require_super_admin),
) -> APIResponse[RensheCleanupRunResponse]:
    from app.services.renshe_cleanup import RensheCleanupService

    result = await RensheCleanupService().retry_run(
        run_id=run_id, super_admin_id=admin.id
    )
    return success(data=result, message="清理任务已重新排队")
