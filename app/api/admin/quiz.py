from fastapi import APIRouter, Depends, File, Form, Path, Query, UploadFile

from app.port.exceptions import BusinessException
from app.middleware.auth import require_permission
from app.schemas.admin import AdminBatchDeleteRequest
from app.schemas.admin_quiz import (
    AdminQuizCategoryCreate,
    AdminQuizCategoryUpdate,
    AdminQuizImportJsonRequest,
    AdminQuizQuestionCreate,
    AdminQuizQuestionResponse,
    AdminQuizQuestionUpdate,
)
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.quiz import QuizCategoryResponse, QuizQuestionResponse
from app.services.admin_quiz import AdminQuizService

router = APIRouter(prefix="/quiz", tags=["管理后台-题库管理"])

CATEGORY = "/categories"
QUESTION = "/questions"
CSV_ALLOWED_CONTENT_TYPES = {"text/csv", "application/vnd.ms-excel"}
CSV_MAX_UPLOAD_SIZE = 5 * 1024 * 1024  # 5MB


# ── Category routes ──

@router.get(CATEGORY,
    response_model=APIResponse[list[QuizCategoryResponse]],
    summary="题库分类列表",
    description="""
管理后台 **题库管理** 页面使用。

**页面路径**: `/admin/quiz`
**使用场景**: 页面加载时获取所有题库分类，用于分类筛选和导航
    """,
)
async def list_categories(
    _admin=Depends(require_permission("quiz:list")),
) -> APIResponse[list[QuizCategoryResponse]]:
    result = await AdminQuizService().list_categories()
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
- `question_type`: 题型（single_choice / multiple_choice / judge）
- `page`: 页码
- `page_size`: 每页条数
**响应**: 分页题目数据
    """,
)
async def list_questions(
    category_id: int | None = Query(None, ge=1, description="分类 ID"),
    question_type: str | None = Query(None, description="题型：single_choice / multiple_choice / judge"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(require_permission("quiz:list")),
) -> APIResponse[PaginatedData[AdminQuizQuestionResponse]]:
    result = await AdminQuizService().list_questions(
        category_id=category_id, question_type=question_type, page=page, page_size=page_size,
    )
    return success(data=result)


# ── Category write routes ──

@router.post(CATEGORY,
    response_model=APIResponse[QuizCategoryResponse],
    summary="新增分类",
    description="""
管理后台 **题库管理** 页面使用。

**页面路径**: `/admin/quiz`
**使用场景**: 管理员点击新增分类按钮，在弹出的表单中创建新的题目分类
    """,
)
async def create_category(
    body: AdminQuizCategoryCreate,
    _admin=Depends(require_permission("quiz:write")),
) -> APIResponse[QuizCategoryResponse]:
    result = await AdminQuizService().create_category(body)
    return success(data=result)


@router.put(f"{CATEGORY}/{{category_id}}",
    response_model=APIResponse[QuizCategoryResponse],
    summary="编辑分类",
    description="""
管理后台 **题库管理** 页面使用。

**页面路径**: `/admin/quiz`
**使用场景**: 管理员在分类列表中编辑已有分类的名称或排序信息
    """,
)
async def update_category(
    body: AdminQuizCategoryUpdate,
    category_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("quiz:write")),
) -> APIResponse[QuizCategoryResponse]:
    result = await AdminQuizService().update_category(category_id, body)
    return success(data=result)


@router.delete(f"{CATEGORY}/{{category_id}}",
    response_model=APIResponse,
    summary="删除分类",
    description="""
管理后台 **题库管理** 页面使用。

**页面路径**: `/admin/quiz`
**使用场景**: 管理员删除不再使用的题目分类
    """,
)
async def delete_category(
    category_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("quiz:write")),
):
    await AdminQuizService().delete_category(category_id)
    return success(message="分类已删除")


# ── Question routes ──

@router.post(QUESTION,
    response_model=APIResponse[QuizQuestionResponse],
    summary="新增题目",
    description="""
管理后台 **题库管理** 页面使用。

**页面路径**: `/admin/quiz`
**使用场景**: 管理员在题目列表中点击新增按钮，创建一道新题目（支持单选、多选、判断）
    """,
)
async def create_question(
    body: AdminQuizQuestionCreate,
    _admin=Depends(require_permission("quiz:write")),
) -> APIResponse[QuizQuestionResponse]:
    result = await AdminQuizService().create_question(body)
    return success(data=result)


@router.put(f"{QUESTION}/{{question_id}}",
    response_model=APIResponse[QuizQuestionResponse],
    summary="编辑题目",
    description="""
管理后台 **题库管理** 页面使用。

**页面路径**: `/admin/quiz`
**使用场景**: 管理员点击题目行进入编辑态，修改题目题干、选项或正确答案
    """,
)
async def update_question(
    body: AdminQuizQuestionUpdate,
    question_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("quiz:write")),
) -> APIResponse[QuizQuestionResponse]:
    result = await AdminQuizService().update_question(question_id, body)
    return success(data=result)


@router.delete(f"{QUESTION}/{{question_id}}",
    response_model=APIResponse,
    summary="删除题目",
    description="""
管理后台 **题库管理** 页面使用。

**页面路径**: `/admin/quiz`
**使用场景**: 管理员删除不需要的题目
    """,
)
async def delete_question(
    question_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("quiz:write")),
):
    await AdminQuizService().delete_question(question_id)
    return success(message="题目已删除")


@router.post(f"{QUESTION}/batch-delete",
    response_model=APIResponse[int],
    summary="批量删除题目",
    description="""
管理后台 **题库管理** 页面使用。

**页面路径**: `/admin/quiz`
**使用场景**: 管理员勾选多道题目后点击批量删除按钮
    """,
)
async def batch_delete_questions(
    body: AdminBatchDeleteRequest,
    _admin=Depends(require_permission("quiz:write")),
):
    count = await AdminQuizService().batch_delete_questions(body.ids)
    return success(data=count, message=f"已删除 {count} 道题目")


# ── Import route ──

@router.post("/import",
    response_model=APIResponse,
    summary="CSV导入题目",
    description="""
管理后台 **题库管理** 页面使用。

**页面路径**: `/admin/quiz`
**使用场景**: 管理员上传 CSV 文件批量导入题目，支持自动创建缺失的分类
    """,
)
async def import_questions(
    file: UploadFile = File(..., description="CSV 文件"),
    create_missing_categories: bool = Form(False, description="是否自动创建缺失的分类"),
    _admin=Depends(require_permission("quiz:import")),
):
    if file.content_type not in CSV_ALLOWED_CONTENT_TYPES:
        raise BusinessException("不支持的文件类型，仅允许上传 CSV 文件")
    content = await file.read()
    if len(content) > CSV_MAX_UPLOAD_SIZE:
        raise BusinessException("文件大小超过限制（最大 5MB）")
    result = await AdminQuizService().import_questions_csv(content, create_missing_categories)
    return success(data=result)


@router.post("/import/json",
    response_model=APIResponse,
    summary="JSON导入题目",
    description="""
管理后台 **题库管理** 页面使用。

**页面路径**: `/admin/quiz`
**使用场景**: 管理员通过 JSON 格式批量导入题目数据
    """,
)
async def import_questions_json(
    body: AdminQuizImportJsonRequest,
    _admin=Depends(require_permission("quiz:import")),
):
    result = await AdminQuizService().import_questions_json(body)
    return success(data=result)