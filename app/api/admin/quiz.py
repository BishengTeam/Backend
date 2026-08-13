from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, Path, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.port.exceptions import BusinessException
from app.port.config import settings
from app.middleware.auth import require_permission
from app.middleware.rate_limit import limiter, quiz_admin_key
from app.schemas.admin_quiz_contract import (
    AdminQuizBatchRequest,
    AdminQuizBatchResponse,
    AdminQuizAuditLogResponse,
    AdminQuizAuditQuery,
    AdminQuizImportJobQuery,
    AdminQuizImportJobResponse,
    AdminQuizImportErrorPage,
    AdminQuizImportErrorQuery,
    AdminQuizImportCategoryImpactResponse,
    AdminQuizImportConfirmCategoriesRequest,
    AdminQuizImportCancelRequest,
    AdminQuizImportReportResponse,
    AdminQuizJsonImportRequest,
    AdminQuizSignedUrlResponse,
    AdminQuizCategoryCreate,
    AdminQuizCategoryImpactQuery,
    AdminQuizCategoryImpactResponse,
    AdminQuizCategoryQuery,
    AdminQuizCategoryResponse,
    AdminQuizCategoryStatusUpdate,
    AdminQuizCategoryUpdate,
    AdminQuizQuestionCreate,
    AdminQuizQuestionQuery,
    AdminQuizQuestionResponse,
    AdminQuizQuestionStatsResponse,
    AdminQuizQuestionStatsListItem,
    AdminQuizStatsOverviewResponse,
    AdminQuizStatsQuestionQuery,
    AdminQuizQuestionUpdate,
    AdminQuizVersionRequest,
)
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.admin_quiz import AdminQuizService
from app.domain.community.src.rule.quiz import (
    QuizCategoryStatus,
    QuizImportSourceType,
    QuizImportStatus,
    QuizQuestionStatus,
    QuizQuestionType,
)

router = APIRouter(prefix="/quiz", tags=["管理后台-题库管理"])

CATEGORY = "/categories"
QUESTION = "/questions"


def _validated_query(model, values: dict):
    try:
        return model(**values)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc


async def admin_category_query(
    status: QuizCategoryStatus | None = Query(None),
    parent_id: int | None = Query(None, ge=1),
) -> AdminQuizCategoryQuery:
    return _validated_query(
        AdminQuizCategoryQuery, {"status": status, "parent_id": parent_id}
    )


async def admin_question_query(
    category_id: int | None = Query(None, ge=1),
    include_descendants: bool = Query(False),
    question_type: QuizQuestionType | None = Query(None),
    status: QuizQuestionStatus | None = Query(None),
    keyword: str | None = Query(None, min_length=1, max_length=128),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> AdminQuizQuestionQuery:
    return _validated_query(
        AdminQuizQuestionQuery,
        {
            "category_id": category_id,
            "include_descendants": include_descendants,
            "question_type": question_type,
            "status": status,
            "keyword": keyword,
            "page": page,
            "page_size": page_size,
        },
    )


async def admin_audit_query(
    admin_id: int | None = Query(None, ge=1),
    action: str | None = Query(None, min_length=1, max_length=64),
    object_type: str | None = Query(None, min_length=1, max_length=64),
    object_id: int | None = Query(None, ge=1),
    result: str | None = Query(None),
    request_id: str | None = Query(None, min_length=1, max_length=64),
    start_at: datetime | None = Query(None),
    end_at: datetime | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> AdminQuizAuditQuery:
    return _validated_query(
        AdminQuizAuditQuery,
        {
            "admin_id": admin_id,
            "action": action,
            "object_type": object_type,
            "object_id": object_id,
            "result": result,
            "request_id": request_id,
            "start_at": start_at,
            "end_at": end_at,
            "page": page,
            "page_size": page_size,
        },
    )


async def admin_category_impact_query(
    action: str = Query(...),
    target_parent_id: int | None = Query(None, ge=1),
) -> AdminQuizCategoryImpactQuery:
    values = {"action": action}
    if target_parent_id is not None:
        values["target_parent_id"] = target_parent_id
    return _validated_query(AdminQuizCategoryImpactQuery, values)


async def admin_stats_question_query(
    category_id: int | None = Query(None, ge=1),
    question_type: QuizQuestionType | None = Query(None),
    status: QuizQuestionStatus | None = Query(None),
    keyword: str | None = Query(None, min_length=1, max_length=128),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> AdminQuizStatsQuestionQuery:
    return _validated_query(
        AdminQuizStatsQuestionQuery,
        {
            "category_id": category_id,
            "question_type": question_type,
            "status": status,
            "keyword": keyword,
            "page": page,
            "page_size": page_size,
        },
    )


async def admin_import_query(
    status: QuizImportStatus | None = Query(None),
    source_type: QuizImportSourceType | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> AdminQuizImportJobQuery:
    return _validated_query(
        AdminQuizImportJobQuery,
        {
            "status": status,
            "source_type": source_type,
            "page": page,
            "page_size": page_size,
        },
    )


async def admin_import_error_query(
    field: str | None = Query(None, min_length=1, max_length=128),
    page: int = Query(1, ge=1),
) -> AdminQuizImportErrorQuery:
    return _validated_query(
        AdminQuizImportErrorQuery,
        {"field": field, "page": page},
    )


# ── Category routes ──

@router.get(CATEGORY,
    response_model=APIResponse[list[AdminQuizCategoryResponse]],
    summary="题库分类列表",
    description="""
管理后台 **题库管理** 页面使用。

**页面路径**: `/admin/quiz`

**使用场景**: 页面加载时获取所有题库分类，用于分类筛选和导航
    """,
)
async def list_categories(
    query: AdminQuizCategoryQuery = Depends(admin_category_query),
    _admin=Depends(require_permission("quiz:list")),
) -> APIResponse[list[AdminQuizCategoryResponse]]:
    result = await AdminQuizService().list_categories(query)
    return success(data=result)


@router.get(
    f"{CATEGORY}/{{category_id}}/impact",
    response_model=APIResponse[AdminQuizCategoryImpactResponse],
    summary="分类操作影响预览",
)
@limiter.limit(
    f"{settings.QUIZ_ADMIN_WRITE_RATE_PER_MINUTE}/minute",
    key_func=quiz_admin_key,
)
async def preview_category_impact(
    request: Request,
    category_id: int = Path(..., ge=1),
    query: AdminQuizCategoryImpactQuery = Depends(admin_category_impact_query),
    _admin=Depends(require_permission("quiz:write")),
) -> APIResponse[AdminQuizCategoryImpactResponse]:
    result = await AdminQuizService().preview_category_impact(category_id, query)
    return success(data=result)


# ── Question routes ──

@router.get(QUESTION,
    response_model=APIResponse[PaginatedData[AdminQuizQuestionResponse]],
    summary="题目列表",
    description="""
管理后台 **题库管理** 页面使用。

**页面路径**: `/admin/quiz`

**使用场景**: 页面加载时按分类/题型分页获取题目列表

**查询参数**:
- `category_id`: 分类 ID
- `include_descendants`: 是否同时筛选后代分类
- `question_type`: 题型（single_choice / multiple_choice / judge）
- `page`: 页码
- `page_size`: 每页条数

**响应**: 分页题目数据
    """,
)
async def list_questions(
    query: AdminQuizQuestionQuery = Depends(admin_question_query),
    _admin=Depends(require_permission("quiz:list")),
) -> APIResponse[PaginatedData[AdminQuizQuestionResponse]]:
    result = await AdminQuizService().list_questions(
        category_id=query.category_id,
        include_descendants=query.include_descendants,
        question_type=query.question_type,
        status=query.status,
        keyword=query.keyword,
        page=query.page,
        page_size=query.page_size,
    )
    return success(data=result)


# ── Category write routes ──

@router.post(CATEGORY,
    response_model=APIResponse[AdminQuizCategoryResponse],
    summary="新增分类",
    description="""
管理后台 **题库管理** 页面使用。

**页面路径**: `/admin/quiz`

**使用场景**: 管理员点击新增分类按钮，在弹出的表单中创建新的题目分类
    """,
)
@limiter.limit(
    f"{settings.QUIZ_ADMIN_WRITE_RATE_PER_MINUTE}/minute",
    key_func=quiz_admin_key,
)
async def create_category(
    request: Request,
    body: AdminQuizCategoryCreate,
    admin=Depends(require_permission("quiz:write")),
) -> APIResponse[AdminQuizCategoryResponse]:
    result = await AdminQuizService().create_category(body, admin_id=admin.id)
    return success(data=result)


@router.put(f"{CATEGORY}/{{category_id}}",
    response_model=APIResponse[AdminQuizCategoryResponse],
    summary="编辑分类",
    description="""
管理后台 **题库管理** 页面使用。

**页面路径**: `/admin/quiz`

**使用场景**: 管理员在分类列表中编辑已有分类的名称或排序信息
    """,
)
@limiter.limit(
    f"{settings.QUIZ_ADMIN_WRITE_RATE_PER_MINUTE}/minute",
    key_func=quiz_admin_key,
)
async def update_category(
    request: Request,
    body: AdminQuizCategoryUpdate,
    category_id: int = Path(..., ge=1),
    admin=Depends(require_permission("quiz:write")),
) -> APIResponse[AdminQuizCategoryResponse]:
    result = await AdminQuizService().update_category(category_id, body, admin_id=admin.id)
    return success(data=result)


@router.delete(f"{CATEGORY}/{{category_id}}",
    response_model=APIResponse[None],
    summary="删除分类",
    description="""
管理后台 **题库管理** 页面使用。

**页面路径**: `/admin/quiz`

**使用场景**: 管理员删除不再使用的题目分类
    """,
)
@limiter.limit(
    f"{settings.QUIZ_ADMIN_WRITE_RATE_PER_MINUTE}/minute",
    key_func=quiz_admin_key,
)
async def delete_category(
    request: Request,
    body: AdminQuizVersionRequest,
    category_id: int = Path(..., ge=1),
    admin=Depends(require_permission("quiz:write")),
):
    await AdminQuizService().delete_category(
        category_id, body.lock_version, admin_id=admin.id
    )
    return success(message="分类已删除")


@router.post(f"{CATEGORY}/{{category_id}}/status",
    response_model=APIResponse[AdminQuizCategoryResponse],
    summary="启用或停用分类",
)
@limiter.limit(
    f"{settings.QUIZ_ADMIN_WRITE_RATE_PER_MINUTE}/minute",
    key_func=quiz_admin_key,
)
async def update_category_status(
    request: Request,
    body: AdminQuizCategoryStatusUpdate,
    category_id: int = Path(..., ge=1),
    admin=Depends(require_permission("quiz:write")),
) -> APIResponse[AdminQuizCategoryResponse]:
    result = await AdminQuizService().update_category_status(
        category_id, body, admin_id=admin.id
    )
    return success(data=result)


# ── Question routes ──

@router.post(QUESTION,
    response_model=APIResponse[AdminQuizQuestionResponse],
    summary="新增题目",
    description="""
管理后台 **题库管理** 页面使用。

**页面路径**: `/admin/quiz`

**使用场景**: 管理员在题目列表中点击新增按钮，创建一道新题目（支持单选、多选、判断）
    """,
)
@limiter.limit(
    f"{settings.QUIZ_ADMIN_WRITE_RATE_PER_MINUTE}/minute",
    key_func=quiz_admin_key,
)
async def create_question(
    request: Request,
    body: AdminQuizQuestionCreate,
    admin=Depends(require_permission("quiz:write")),
) -> APIResponse[AdminQuizQuestionResponse]:
    result = await AdminQuizService().create_question(body, admin_id=admin.id)
    return success(data=result)


@router.put(f"{QUESTION}/{{question_id}}",
    response_model=APIResponse[AdminQuizQuestionResponse],
    summary="编辑题目",
    description="""
管理后台 **题库管理** 页面使用。

**页面路径**: `/admin/quiz`

**使用场景**: 管理员点击题目行进入编辑态，修改题目题干、选项或正确答案
    """,
)
@limiter.limit(
    f"{settings.QUIZ_ADMIN_WRITE_RATE_PER_MINUTE}/minute",
    key_func=quiz_admin_key,
)
async def update_question(
    request: Request,
    body: AdminQuizQuestionUpdate,
    question_id: int = Path(..., ge=1),
    admin=Depends(require_permission("quiz:write")),
) -> APIResponse[AdminQuizQuestionResponse]:
    result = await AdminQuizService().update_question(
        question_id, body, admin_id=admin.id
    )
    return success(data=result)


@router.delete(f"{QUESTION}/{{question_id}}",
    response_model=APIResponse[None],
    summary="删除题目",
    description="""
管理后台 **题库管理** 页面使用。

**页面路径**: `/admin/quiz`

**使用场景**: 管理员删除不需要的题目
    """,
)
@limiter.limit(
    f"{settings.QUIZ_ADMIN_WRITE_RATE_PER_MINUTE}/minute",
    key_func=quiz_admin_key,
)
async def delete_question(
    request: Request,
    body: AdminQuizVersionRequest,
    question_id: int = Path(..., ge=1),
    admin=Depends(require_permission("quiz:write")),
):
    await AdminQuizService().delete_question(
        question_id, body.lock_version, admin_id=admin.id
    )
    return success(message="题目已删除")


@router.post(f"{QUESTION}/{{question_id}}/publish",
    response_model=APIResponse[AdminQuizQuestionResponse],
    summary="发布题目",
)
@limiter.limit(
    f"{settings.QUIZ_ADMIN_WRITE_RATE_PER_MINUTE}/minute",
    key_func=quiz_admin_key,
)
async def publish_question(
    request: Request,
    body: AdminQuizVersionRequest,
    question_id: int = Path(..., ge=1),
    admin=Depends(require_permission("quiz:write")),
) -> APIResponse[AdminQuizQuestionResponse]:
    result = await AdminQuizService().publish_question(
        question_id, body, admin_id=admin.id
    )
    return success(data=result)


@router.post(f"{QUESTION}/{{question_id}}/disable",
    response_model=APIResponse[AdminQuizQuestionResponse],
    summary="停用题目",
)
@limiter.limit(
    f"{settings.QUIZ_ADMIN_WRITE_RATE_PER_MINUTE}/minute",
    key_func=quiz_admin_key,
)
async def disable_question(
    request: Request,
    body: AdminQuizVersionRequest,
    question_id: int = Path(..., ge=1),
    admin=Depends(require_permission("quiz:write")),
) -> APIResponse[AdminQuizQuestionResponse]:
    result = await AdminQuizService().disable_question(
        question_id, body, admin_id=admin.id
    )
    return success(data=result)


@router.post(f"{QUESTION}/{{question_id}}/restore",
    response_model=APIResponse[AdminQuizQuestionResponse],
    summary="恢复题目",
)
@limiter.limit(
    f"{settings.QUIZ_ADMIN_WRITE_RATE_PER_MINUTE}/minute",
    key_func=quiz_admin_key,
)
async def restore_question(
    request: Request,
    body: AdminQuizVersionRequest,
    question_id: int = Path(..., ge=1),
    admin=Depends(require_permission("quiz:write")),
) -> APIResponse[AdminQuizQuestionResponse]:
    result = await AdminQuizService().restore_question(
        question_id, body, admin_id=admin.id
    )
    return success(data=result)


@router.post(f"{QUESTION}/batch-publish",
    response_model=APIResponse[AdminQuizBatchResponse],
    summary="批量发布题目",
)
@limiter.limit(
    f"{settings.QUIZ_ADMIN_BATCH_RATE_PER_MINUTE}/minute",
    key_func=quiz_admin_key,
)
async def batch_publish_questions(
    request: Request,
    body: AdminQuizBatchRequest,
    admin=Depends(require_permission("quiz:write")),
) -> APIResponse[AdminQuizBatchResponse]:
    result = await AdminQuizService().batch_publish_questions(body, admin_id=admin.id)
    return success(data=result)


@router.post(f"{QUESTION}/batch-disable",
    response_model=APIResponse[AdminQuizBatchResponse],
    summary="批量停用题目",
)
@limiter.limit(
    f"{settings.QUIZ_ADMIN_BATCH_RATE_PER_MINUTE}/minute",
    key_func=quiz_admin_key,
)
async def batch_disable_questions(
    request: Request,
    body: AdminQuizBatchRequest,
    admin=Depends(require_permission("quiz:write")),
) -> APIResponse[AdminQuizBatchResponse]:
    result = await AdminQuizService().batch_disable_questions(body, admin_id=admin.id)
    return success(data=result)


@router.get(f"{QUESTION}/{{question_id}}/stats",
    response_model=APIResponse[AdminQuizQuestionStatsResponse],
    summary="题目统计",
)
async def get_question_stats(
    question_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("quiz:list")),
) -> APIResponse[AdminQuizQuestionStatsResponse]:
    result = await AdminQuizService().get_question_stats(question_id)
    return success(data=result)


@router.get(
    "/stats/overview",
    response_model=APIResponse[AdminQuizStatsOverviewResponse],
    summary="题库聚合统计总览",
)
async def get_stats_overview(
    _admin=Depends(require_permission("quiz:list")),
) -> APIResponse[AdminQuizStatsOverviewResponse]:
    return success(data=await AdminQuizService().get_stats_overview())


@router.get(
    "/stats/questions",
    response_model=APIResponse[PaginatedData[AdminQuizQuestionStatsListItem]],
    summary="单题聚合统计分页",
)
async def list_question_stats(
    query: AdminQuizStatsQuestionQuery = Depends(admin_stats_question_query),
    _admin=Depends(require_permission("quiz:list")),
) -> APIResponse[PaginatedData[AdminQuizQuestionStatsListItem]]:
    return success(data=await AdminQuizService().list_question_stats(query))


@router.get("/audit-logs",
    response_model=APIResponse[PaginatedData[AdminQuizAuditLogResponse]],
    summary="题库管理审计日志",
)
async def list_audit_logs(
    query: AdminQuizAuditQuery = Depends(admin_audit_query),
    _admin=Depends(require_permission("quiz:list")),
) -> APIResponse[PaginatedData[AdminQuizAuditLogResponse]]:
    result = await AdminQuizService().list_audit_logs(query)
    return success(data=result)


# ── Import task routes ──

@router.post("/imports/csv",
    response_model=APIResponse[AdminQuizImportJobResponse],
    summary="创建 CSV 导入任务",
)
@limiter.limit(
    f"{settings.QUIZ_ADMIN_IMPORT_RATE_PER_MINUTE}/minute",
    key_func=quiz_admin_key,
)
async def create_csv_import(
    request: Request,
    file: UploadFile = File(..., description="CSV 文件"),
    filename: str = Form(..., min_length=1, max_length=255),
    size_bytes: int = Form(..., ge=1, le=10 * 1024 * 1024),
    admin=Depends(require_permission("quiz:import")),
) -> APIResponse[AdminQuizImportJobResponse]:
    content = await file.read(settings.QUIZ_IMPORT_MAX_FILE_BYTES + 1)
    if len(content) > settings.QUIZ_IMPORT_MAX_FILE_BYTES:
        raise BusinessException("导入文件不能超过 10 MiB")
    if size_bytes != len(content):
        raise BusinessException("文件大小校验失败")
    if not filename.lower().endswith(".csv"):
        raise BusinessException("仅允许上传 .csv 文件")
    result = await AdminQuizService().create_import_job(
        source_type="csv",
        content=content,
        admin_id=admin.id,
        filename=filename,
    )
    return success(data=result)


@router.post("/imports/json",
    response_model=APIResponse[AdminQuizImportJobResponse],
    summary="创建 JSON 导入任务",
)
@limiter.limit(
    f"{settings.QUIZ_ADMIN_IMPORT_RATE_PER_MINUTE}/minute",
    key_func=quiz_admin_key,
)
async def create_json_import(
    request: Request,
    body: AdminQuizJsonImportRequest,
    admin=Depends(require_permission("quiz:import")),
) -> APIResponse[AdminQuizImportJobResponse]:
    content = body.model_dump_json(exclude_none=False).encode("utf-8")
    result = await AdminQuizService().create_import_job(
        source_type="json",
        content=content,
        admin_id=admin.id,
        filename="quiz-import.json",
    )
    return success(data=result)


@router.get("/imports",
    response_model=APIResponse[PaginatedData[AdminQuizImportJobResponse]],
    summary="导入任务列表",
)
async def list_import_jobs(
    query: AdminQuizImportJobQuery = Depends(admin_import_query),
    admin=Depends(require_permission("quiz:list")),
) -> APIResponse[PaginatedData[AdminQuizImportJobResponse]]:
    result = await AdminQuizService().list_import_jobs(
        query,
        admin_id=admin.id,
        is_super_admin=admin.role == "super_admin",
    )
    return success(data=result)


@router.get("/imports/{job_id}",
    response_model=APIResponse[AdminQuizImportJobResponse],
    summary="导入任务详情",
)
async def get_import_job(
    job_id: int = Path(..., ge=1),
    admin=Depends(require_permission("quiz:list")),
) -> APIResponse[AdminQuizImportJobResponse]:
    result = await AdminQuizService().get_import_job(
        job_id,
        admin_id=admin.id,
        is_super_admin=admin.role == "super_admin",
    )
    return success(data=result)


@router.get(
    "/imports/{job_id}/errors",
    response_model=APIResponse[AdminQuizImportErrorPage],
    summary="分页查看导入错误明细",
)
async def list_import_errors(
    job_id: int = Path(..., ge=1),
    query: AdminQuizImportErrorQuery = Depends(admin_import_error_query),
    admin=Depends(require_permission("quiz:list")),
) -> APIResponse[AdminQuizImportErrorPage]:
    result = await AdminQuizService().list_import_errors(
        job_id,
        query,
        admin_id=admin.id,
        is_super_admin=admin.role == "super_admin",
    )
    return success(data=result)


@router.get(
    "/imports/{job_id}/category-impact",
    response_model=APIResponse[AdminQuizImportCategoryImpactResponse],
    summary="查看缺失分类创建影响",
)
async def get_import_category_impact(
    job_id: int = Path(..., ge=1),
    admin=Depends(require_permission("quiz:list")),
) -> APIResponse[AdminQuizImportCategoryImpactResponse]:
    result = await AdminQuizService().get_import_category_impact(
        job_id,
        admin_id=admin.id,
        is_super_admin=admin.role == "super_admin",
    )
    return success(data=result)


@router.post(
    "/imports/{job_id}/confirm-categories",
    response_model=APIResponse[AdminQuizImportJobResponse],
    summary="确认创建导入文件中的缺失分类",
)
@limiter.limit(
    f"{settings.QUIZ_ADMIN_IMPORT_RATE_PER_MINUTE}/minute",
    key_func=quiz_admin_key,
)
async def confirm_import_categories(
    request: Request,
    body: AdminQuizImportConfirmCategoriesRequest,
    job_id: int = Path(..., ge=1),
    admin=Depends(require_permission("quiz:import")),
    _write_admin=Depends(require_permission("quiz:write")),
) -> APIResponse[AdminQuizImportJobResponse]:
    result = await AdminQuizService().confirm_import_categories(
        job_id,
        body,
        admin_id=admin.id,
        is_super_admin=admin.role == "super_admin",
    )
    return success(data=result)


@router.post(
    "/imports/{job_id}/cancel",
    response_model=APIResponse[AdminQuizImportJobResponse],
    summary="取消等待分类确认的导入任务",
)
@limiter.limit(
    f"{settings.QUIZ_ADMIN_IMPORT_RATE_PER_MINUTE}/minute",
    key_func=quiz_admin_key,
)
async def cancel_import_job(
    request: Request,
    body: AdminQuizImportCancelRequest,
    job_id: int = Path(..., ge=1),
    admin=Depends(require_permission("quiz:import")),
) -> APIResponse[AdminQuizImportJobResponse]:
    result = await AdminQuizService().cancel_import_job(
        job_id,
        body,
        admin_id=admin.id,
        is_super_admin=admin.role == "super_admin",
    )
    return success(data=result)


@router.get("/imports/{job_id}/report-url",
    response_model=APIResponse[AdminQuizSignedUrlResponse],
    summary="错误报告临时地址",
)
@limiter.limit(
    f"{settings.QUIZ_ADMIN_SIGNED_URL_RATE_PER_MINUTE}/minute",
    key_func=quiz_admin_key,
)
async def get_import_report_url(
    request: Request,
    job_id: int = Path(..., ge=1),
    admin=Depends(require_permission("quiz:list")),
) -> APIResponse[AdminQuizSignedUrlResponse]:
    result = await AdminQuizService().get_import_report_url(
        job_id,
        admin_id=admin.id,
        is_super_admin=admin.role == "super_admin",
    )
    return success(data=result)


@router.get(
    "/imports/{job_id}/source-url",
    response_model=APIResponse[AdminQuizSignedUrlResponse],
    summary="导入源文件临时地址",
)
@limiter.limit(
    f"{settings.QUIZ_ADMIN_SIGNED_URL_RATE_PER_MINUTE}/minute",
    key_func=quiz_admin_key,
)
async def get_import_source_url(
    request: Request,
    job_id: int = Path(..., ge=1),
    admin=Depends(require_permission("quiz:list")),
) -> APIResponse[AdminQuizSignedUrlResponse]:
    result = await AdminQuizService().get_import_source_url(
        job_id,
        admin_id=admin.id,
        is_super_admin=admin.role == "super_admin",
    )
    return success(data=result)


@router.post(
    "/imports/{job_id}/retry",
    response_model=APIResponse[AdminQuizImportJobResponse],
    summary="人工重试失败的导入任务",
)
@limiter.limit(
    f"{settings.QUIZ_ADMIN_IMPORT_RATE_PER_MINUTE}/minute",
    key_func=quiz_admin_key,
)
async def retry_import_job(
    request: Request,
    job_id: int = Path(..., ge=1),
    admin=Depends(require_permission("quiz:import")),
) -> APIResponse[AdminQuizImportJobResponse]:
    result = await AdminQuizService().retry_import_job(job_id, admin_id=admin.id)
    return success(data=result)


@router.get(
    "/imports/{job_id}/report",
    response_class=Response,
    include_in_schema=False,
    summary="读取本地开发错误报告",
)
async def read_import_report(
    job_id: int = Path(..., ge=1),
    expires: int = Query(..., ge=1),
    admin_id: int = Query(..., ge=1),
    token: str = Query(..., min_length=32, max_length=128),
) -> Response:
    report = await AdminQuizService().read_import_report(
        job_id, expires=expires, admin_id=admin_id, token=token
    )
    content = report.model_dump_json().encode("utf-8")
    return Response(
        content=content,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="quiz-import-{job_id}-errors.json"'
            ),
            "Cache-Control": "private, no-store",
        },
    )


@router.get(
    "/imports/{job_id}/source",
    response_class=Response,
    include_in_schema=False,
    summary="读取本地开发导入源文件",
)
async def read_import_source(
    job_id: int = Path(..., ge=1),
    expires: int = Query(..., ge=1),
    admin_id: int = Query(..., ge=1),
    token: str = Query(..., min_length=32, max_length=128),
) -> Response:
    content = await AdminQuizService().read_import_source(
        job_id, expires=expires, admin_id=admin_id, token=token
    )
    return Response(
        content=content.data,
        media_type=content.media_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="quiz-import-{job_id}.{content.extension}"'
            ),
            "Cache-Control": "private, no-store",
        },
    )
