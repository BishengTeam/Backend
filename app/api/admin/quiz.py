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
    AdminQuizQuestionRevisionResponse,
    AdminQuizQuestionStatsResponse,
    AdminQuizQuestionStatsListItem,
    AdminQuizStatsOverviewResponse,
    AdminQuizStatsQuestionQuery,
    AdminQuizDailyStatsQuery,
    AdminQuizDailyStatsItem,
    AdminQuizUserStatsQuery,
    AdminQuizUserStatsListItem,
    AdminQuizImageUploadCreate,
    AdminQuizImageUploadResponse,
    AdminQuizQuestionUpdate,
    AdminQuizVersionRequest,
    AdminQuizContentStatusUpdate,
    AdminQuizCourseBindingCreate,
    AdminQuizCourseOptionResponse,
    AdminQuizCourseBindingResponse,
    AdminQuizCourseBindingStatusUpdate,
    AdminQuizContentTreeResponse,
    AdminQuizKnowledgePointCreate,
    AdminQuizKnowledgePointResponse,
    AdminQuizKnowledgePointUpdate,
    AdminQuizLibraryCreate,
    AdminQuizLibraryQuery,
    AdminQuizLibraryResponse,
    AdminQuizLibraryStatusUpdate,
    AdminQuizLibraryUpdate,
    AdminQuizMigrationReportResponse,
    AdminQuizModuleCreate,
    AdminQuizModuleResponse,
    AdminQuizModuleUpdate,
)
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.admin_quiz import AdminQuizService
from app.services.quiz_image_upload import QuizImageUploadService
from app.services.admin_quiz_v2 import AdminQuizV2Service
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


# ── Fixed hierarchy V2 routes ──


@router.get(
    "/course-options",
    response_model=APIResponse[list[AdminQuizCourseOptionResponse]],
    summary="题库课程绑定候选项",
)
async def list_course_options(
    keyword: str | None = Query(None, min_length=1, max_length=128),
    limit: int = Query(100, ge=1, le=100),
    _admin=Depends(require_permission("course_quiz_bind")),
) -> APIResponse[list[AdminQuizCourseOptionResponse]]:
    return success(
        data=await AdminQuizV2Service().list_course_options(
            keyword=keyword,
            limit=limit,
        )
    )


@router.get(
    "/libraries",
    response_model=APIResponse[list[AdminQuizLibraryResponse]],
    summary="V2 题库列表",
)
async def list_libraries(
    status: str | None = Query(None),
    access_mode: str | None = Query(None),
    keyword: str | None = Query(None, min_length=1, max_length=128),
    include_deleted: bool = Query(False),
    _admin=Depends(require_permission("quiz:list")),
) -> APIResponse[list[AdminQuizLibraryResponse]]:
    query = _validated_query(
        AdminQuizLibraryQuery,
        {
            "status": status,
            "access_mode": access_mode,
            "keyword": keyword,
            "include_deleted": include_deleted,
        },
    )
    return success(data=await AdminQuizV2Service().list_libraries(query))


@router.post(
    "/libraries",
    response_model=APIResponse[AdminQuizLibraryResponse],
    summary="创建 V2 题库",
)
async def create_library(
    body: AdminQuizLibraryCreate,
    admin=Depends(require_permission("quiz_library_manage")),
) -> APIResponse[AdminQuizLibraryResponse]:
    return success(
        data=await AdminQuizV2Service().create_library(body, admin_id=admin.id)
    )


@router.get(
    "/libraries/{library_id}",
    response_model=APIResponse[AdminQuizLibraryResponse],
    summary="V2 题库详情",
)
async def get_library(
    library_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("quiz:list")),
) -> APIResponse[AdminQuizLibraryResponse]:
    return success(data=await AdminQuizV2Service().get_library(library_id))


@router.put(
    "/libraries/{library_id}",
    response_model=APIResponse[AdminQuizLibraryResponse],
    summary="编辑 V2 题库",
)
async def update_library(
    body: AdminQuizLibraryUpdate,
    library_id: int = Path(..., ge=1),
    admin=Depends(require_permission("quiz_library_manage")),
) -> APIResponse[AdminQuizLibraryResponse]:
    return success(
        data=await AdminQuizV2Service().update_library(
            library_id, body, admin_id=admin.id
        )
    )


@router.post(
    "/libraries/{library_id}/lifecycle",
    response_model=APIResponse[AdminQuizLibraryResponse],
    summary="变更 V2 题库生命周期",
)
async def transition_library(
    body: AdminQuizLibraryStatusUpdate,
    library_id: int = Path(..., ge=1),
    admin=Depends(require_permission("quiz_library_manage")),
) -> APIResponse[AdminQuizLibraryResponse]:
    return success(
        data=await AdminQuizV2Service().transition_library(
            library_id, body, admin_id=admin.id
        )
    )


@router.get(
    "/libraries/{library_id}/course-bindings",
    response_model=APIResponse[list[AdminQuizCourseBindingResponse]],
    summary="题库课程绑定列表",
)
async def list_library_course_bindings(
    library_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("quiz:list")),
) -> APIResponse[list[AdminQuizCourseBindingResponse]]:
    return success(
        data=await AdminQuizV2Service().list_course_bindings(library_id)
    )


@router.post(
    "/libraries/{library_id}/course-bindings",
    response_model=APIResponse[AdminQuizCourseBindingResponse],
    summary="绑定课程与题库",
)
async def create_library_course_binding(
    body: AdminQuizCourseBindingCreate,
    library_id: int = Path(..., ge=1),
    admin=Depends(require_permission("course_quiz_bind")),
) -> APIResponse[AdminQuizCourseBindingResponse]:
    return success(
        data=await AdminQuizV2Service().create_course_binding(
            library_id, body, admin_id=admin.id
        )
    )


@router.post(
    "/course-bindings/{binding_id}/status",
    response_model=APIResponse[AdminQuizCourseBindingResponse],
    summary="启停课程题库绑定",
)
async def set_library_course_binding_status(
    body: AdminQuizCourseBindingStatusUpdate,
    binding_id: int = Path(..., ge=1),
    admin=Depends(require_permission("course_quiz_bind")),
) -> APIResponse[AdminQuizCourseBindingResponse]:
    return success(
        data=await AdminQuizV2Service().set_course_binding_status(
            binding_id, body, admin_id=admin.id
        )
    )


@router.get(
    "/libraries/{library_id}/content-tree",
    response_model=APIResponse[AdminQuizContentTreeResponse],
    summary="V2 模块与知识点树",
)
async def get_content_tree(
    library_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("quiz:list")),
) -> APIResponse[AdminQuizContentTreeResponse]:
    return success(data=await AdminQuizV2Service().get_content_tree(library_id))


@router.post(
    "/modules",
    response_model=APIResponse[AdminQuizModuleResponse],
    summary="创建模块",
)
async def create_module(
    body: AdminQuizModuleCreate,
    admin=Depends(require_permission("quiz_content_edit")),
) -> APIResponse[AdminQuizModuleResponse]:
    return success(data=await AdminQuizV2Service().create_module(body, admin_id=admin.id))


@router.put(
    "/modules/{module_id}",
    response_model=APIResponse[AdminQuizModuleResponse],
    summary="编辑模块",
)
async def update_module(
    body: AdminQuizModuleUpdate,
    module_id: int = Path(..., ge=1),
    admin=Depends(require_permission("quiz_content_edit")),
) -> APIResponse[AdminQuizModuleResponse]:
    return success(
        data=await AdminQuizV2Service().update_module(module_id, body, admin_id=admin.id)
    )


@router.post(
    "/modules/{module_id}/status",
    response_model=APIResponse[AdminQuizModuleResponse],
    summary="启停模块",
)
async def set_module_status(
    body: AdminQuizContentStatusUpdate,
    module_id: int = Path(..., ge=1),
    admin=Depends(require_permission("quiz_content_edit")),
) -> APIResponse[AdminQuizModuleResponse]:
    return success(
        data=await AdminQuizV2Service().set_module_status(
            module_id, body, admin_id=admin.id
        )
    )


@router.delete(
    "/modules/{module_id}",
    response_model=APIResponse[None],
    summary="逻辑删除模块",
)
async def delete_module_v2(
    body: AdminQuizVersionRequest,
    module_id: int = Path(..., ge=1),
    admin=Depends(require_permission("quiz_content_edit")),
) -> APIResponse[None]:
    await AdminQuizV2Service().delete_module(module_id, body, admin_id=admin.id)
    return success(message="模块已删除，可在 7 天内撤销")


@router.post(
    "/modules/{module_id}/undo-delete",
    response_model=APIResponse[AdminQuizModuleResponse],
    summary="撤销模块删除",
)
async def undo_delete_module_v2(
    body: AdminQuizVersionRequest,
    module_id: int = Path(..., ge=1),
    admin=Depends(require_permission("quiz_content_edit")),
) -> APIResponse[AdminQuizModuleResponse]:
    return success(
        data=await AdminQuizV2Service().undo_delete_module(
            module_id, body, admin_id=admin.id
        )
    )


@router.post(
    "/knowledge-points",
    response_model=APIResponse[AdminQuizKnowledgePointResponse],
    summary="创建知识点",
)
async def create_knowledge_point(
    body: AdminQuizKnowledgePointCreate,
    admin=Depends(require_permission("quiz_content_edit")),
) -> APIResponse[AdminQuizKnowledgePointResponse]:
    return success(
        data=await AdminQuizV2Service().create_knowledge_point(
            body, admin_id=admin.id
        )
    )


@router.put(
    "/knowledge-points/{point_id}",
    response_model=APIResponse[AdminQuizKnowledgePointResponse],
    summary="编辑或移动知识点",
)
async def update_knowledge_point(
    body: AdminQuizKnowledgePointUpdate,
    point_id: int = Path(..., ge=1),
    admin=Depends(require_permission("quiz_content_edit")),
) -> APIResponse[AdminQuizKnowledgePointResponse]:
    return success(
        data=await AdminQuizV2Service().update_knowledge_point(
            point_id, body, admin_id=admin.id
        )
    )


@router.post(
    "/knowledge-points/{point_id}/status",
    response_model=APIResponse[AdminQuizKnowledgePointResponse],
    summary="启停知识点",
)
async def set_knowledge_point_status(
    body: AdminQuizContentStatusUpdate,
    point_id: int = Path(..., ge=1),
    admin=Depends(require_permission("quiz_content_edit")),
) -> APIResponse[AdminQuizKnowledgePointResponse]:
    return success(
        data=await AdminQuizV2Service().set_knowledge_point_status(
            point_id, body, admin_id=admin.id
        )
    )


@router.delete(
    "/knowledge-points/{point_id}",
    response_model=APIResponse[None],
    summary="逻辑删除知识点",
)
async def delete_knowledge_point(
    body: AdminQuizVersionRequest,
    point_id: int = Path(..., ge=1),
    admin=Depends(require_permission("quiz_content_edit")),
) -> APIResponse[None]:
    await AdminQuizV2Service().delete_knowledge_point(
        point_id, body, admin_id=admin.id
    )
    return success(message="知识点已删除，可在 7 天内撤销")


@router.post(
    "/knowledge-points/{point_id}/undo-delete",
    response_model=APIResponse[AdminQuizKnowledgePointResponse],
    summary="撤销知识点删除",
)
async def undo_delete_knowledge_point_v2(
    body: AdminQuizVersionRequest,
    point_id: int = Path(..., ge=1),
    admin=Depends(require_permission("quiz_content_edit")),
) -> APIResponse[AdminQuizKnowledgePointResponse]:
    return success(
        data=await AdminQuizV2Service().undo_delete_knowledge_point(
            point_id, body, admin_id=admin.id
        )
    )


@router.get(
    "/migration-report",
    response_model=APIResponse[AdminQuizMigrationReportResponse],
    summary="V2 迁移预检与问题报告",
)
async def get_migration_report(
    _admin=Depends(require_permission("quiz:list")),
) -> APIResponse[AdminQuizMigrationReportResponse]:
    return success(data=await AdminQuizV2Service().migration_report())


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
    library_id: int | None = Query(None, ge=1),
    module_id: int | None = Query(None, ge=1),
    knowledge_point_id: int | None = Query(None, ge=1),
    question_type: QuizQuestionType | None = Query(None),
    status: QuizQuestionStatus | None = Query(None),
    keyword: str | None = Query(None, min_length=1, max_length=128),
    include_deleted: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> AdminQuizQuestionQuery:
    return _validated_query(
        AdminQuizQuestionQuery,
        {
            "category_id": category_id,
            "include_descendants": include_descendants,
            "library_id": library_id,
            "module_id": module_id,
            "knowledge_point_id": knowledge_point_id,
            "question_type": question_type,
            "status": status,
            "keyword": keyword,
            "include_deleted": include_deleted,
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
    library_id: int | None = Query(None, ge=1),
    module_id: int | None = Query(None, ge=1),
    knowledge_point_id: int | None = Query(None, ge=1),
    question_type: QuizQuestionType | None = Query(None),
    status: QuizQuestionStatus | None = Query(None),
    include_deleted: bool = Query(False),
    keyword: str | None = Query(None, min_length=1, max_length=128),
    sort: str = Query("updated_at"),
    order: str = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> AdminQuizStatsQuestionQuery:
    return _validated_query(
        AdminQuizStatsQuestionQuery,
        {
            "library_id": library_id,
            "module_id": module_id,
            "knowledge_point_id": knowledge_point_id,
            "question_type": question_type,
            "status": status,
            "include_deleted": include_deleted,
            "keyword": keyword,
            "sort": sort,
            "order": order,
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
    if any(
        value is not None
        for value in (query.library_id, query.module_id, query.knowledge_point_id)
    ) or query.include_deleted:
        return success(data=await AdminQuizV2Service().list_questions(query))
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
    if body.knowledge_point_id is not None:
        result = await AdminQuizV2Service().create_question(body, admin_id=admin.id)
    else:
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
    if await AdminQuizV2Service().is_v2_question(question_id):
        result = await AdminQuizV2Service().update_question(
            question_id, body, admin_id=admin.id
        )
    else:
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
) -> APIResponse[None]:
    if await AdminQuizV2Service().is_v2_question(question_id):
        await AdminQuizV2Service().transition_question(
            question_id, body, "delete", admin_id=admin.id
        )
        # The frozen DELETE contract is APIResponse[None].  The service returns
        # the transitioned entity for internal callers, but exposing it here
        # makes FastAPI fail response validation after the delete has already
        # committed (the client then sees a misleading 500/version conflict).
        return success(message="题目已删除，可在 7 天内撤销")
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
    if await AdminQuizV2Service().is_v2_question(question_id):
        result = await AdminQuizV2Service().publish_question_revision(
            question_id, body, admin_id=admin.id
        )
    else:
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
    if await AdminQuizV2Service().is_v2_question(question_id):
        result = await AdminQuizV2Service().transition_question(
            question_id, body, "disable", admin_id=admin.id
        )
    else:
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
    if await AdminQuizV2Service().is_v2_question(question_id):
        result = await AdminQuizV2Service().transition_question(
            question_id, body, "restore", admin_id=admin.id
        )
    else:
        result = await AdminQuizService().restore_question(
            question_id, body, admin_id=admin.id
        )
    return success(data=result)


@router.get(
    f"{QUESTION}/{{question_id}}/revisions",
    response_model=APIResponse[list[AdminQuizQuestionRevisionResponse]],
    summary="题目不可变版本列表",
)
async def list_question_revisions(
    question_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("quiz:list")),
) -> APIResponse[list[AdminQuizQuestionRevisionResponse]]:
    return success(
        data=await AdminQuizV2Service().list_question_revisions(question_id)
    )


@router.post(
    f"{QUESTION}/{{question_id}}/undo-delete",
    response_model=APIResponse[AdminQuizQuestionResponse],
    summary="撤销 V2 题目删除",
)
async def undo_delete_question(
    body: AdminQuizVersionRequest,
    question_id: int = Path(..., ge=1),
    admin=Depends(require_permission("quiz_content_edit")),
) -> APIResponse[AdminQuizQuestionResponse]:
    return success(
        data=await AdminQuizV2Service().transition_question(
            question_id, body, "undo_delete", admin_id=admin.id
        )
    )


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
    v2_flags = [await AdminQuizV2Service().is_v2_question(item.question_id) for item in body.items]
    if any(v2_flags) and not all(v2_flags):
        raise BusinessException("批量操作不能混合旧分类题目和 V2 题目")
    result = (
        await AdminQuizV2Service().batch_transition_questions(body, "publish", admin_id=admin.id)
        if all(v2_flags)
        else await AdminQuizService().batch_publish_questions(body, admin_id=admin.id)
    )
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
    v2_flags = [await AdminQuizV2Service().is_v2_question(item.question_id) for item in body.items]
    if any(v2_flags) and not all(v2_flags):
        raise BusinessException("批量操作不能混合旧分类题目和 V2 题目")
    result = (
        await AdminQuizV2Service().batch_transition_questions(body, "disable", admin_id=admin.id)
        if all(v2_flags)
        else await AdminQuizService().batch_disable_questions(body, admin_id=admin.id)
    )
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


@router.get(
    "/stats/daily",
    response_model=APIResponse[list[AdminQuizDailyStatsItem]],
    summary="每日刷题量与活跃人数趋势",
)
async def get_daily_stats(
    days: int = Query(30),
    _admin=Depends(require_permission("quiz:list")),
) -> APIResponse[list[AdminQuizDailyStatsItem]]:
    query = _validated_query(AdminQuizDailyStatsQuery, {"days": days})
    return success(data=await AdminQuizService().get_daily_stats(query))


@router.get(
    "/stats/users",
    response_model=APIResponse[PaginatedData[AdminQuizUserStatsListItem]],
    summary="用户做题排行榜",
)
async def list_user_stats(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin=Depends(require_permission("quiz:list")),
) -> APIResponse[PaginatedData[AdminQuizUserStatsListItem]]:
    query = _validated_query(
        AdminQuizUserStatsQuery,
        {"page": page, "page_size": page_size},
    )
    return success(data=await AdminQuizService().list_user_stats(query))


@router.post(
    "/uploads",
    response_model=APIResponse[AdminQuizImageUploadResponse],
    summary="题库图片直传 OSS 预签名",
)
async def create_quiz_image_upload(
    body: AdminQuizImageUploadCreate,
    _admin=Depends(require_permission("quiz:write")),
) -> APIResponse[AdminQuizImageUploadResponse]:
    return success(data=await QuizImageUploadService().create(body))


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
    library_id: int | None = Form(None, ge=1),
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
        library_id=library_id,
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
    content = body.model_dump_json(
        exclude_none=False, exclude={"library_id"}
    ).encode("utf-8")
    result = await AdminQuizService().create_import_job(
        source_type="json",
        content=content,
        admin_id=admin.id,
        filename="quiz-import.json",
        library_id=body.library_id,
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
