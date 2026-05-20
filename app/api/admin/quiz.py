from fastapi import APIRouter, Depends, File, Form, Path, UploadFile

from app.middleware.auth import get_current_admin
from app.schemas.admin_quiz import (
    AdminQuizCategoryCreate,
    AdminQuizCategoryUpdate,
    AdminQuizQuestionCreate,
    AdminQuizQuestionUpdate,
)
from app.schemas.common import APIResponse, success
from app.schemas.quiz import QuizCategoryResponse, QuizQuestionResponse
from app.services.admin_quiz import AdminQuizService

router = APIRouter(prefix="/quiz", tags=["管理后台-题库管理"])

CATEGORY = "/categories"
QUESTION = "/questions"


# ── Category routes ──

@router.post(CATEGORY, response_model=APIResponse[QuizCategoryResponse])
async def create_category(
    body: AdminQuizCategoryCreate,
    _admin=Depends(get_current_admin),
) -> APIResponse[QuizCategoryResponse]:
    result = await AdminQuizService().create_category(body)
    return success(data=result)


@router.put(f"{CATEGORY}/{{category_id}}", response_model=APIResponse[QuizCategoryResponse])
async def update_category(
    body: AdminQuizCategoryUpdate,
    category_id: int = Path(..., ge=1),
    _admin=Depends(get_current_admin),
) -> APIResponse[QuizCategoryResponse]:
    result = await AdminQuizService().update_category(category_id, body)
    return success(data=result)


@router.delete(f"{CATEGORY}/{{category_id}}", response_model=APIResponse)
async def delete_category(
    category_id: int = Path(..., ge=1),
    _admin=Depends(get_current_admin),
):
    await AdminQuizService().delete_category(category_id)
    return success(message="分类已删除")


# ── Question routes ──

@router.post(QUESTION, response_model=APIResponse[QuizQuestionResponse])
async def create_question(
    body: AdminQuizQuestionCreate,
    _admin=Depends(get_current_admin),
) -> APIResponse[QuizQuestionResponse]:
    result = await AdminQuizService().create_question(body)
    return success(data=result)


@router.put(f"{QUESTION}/{{question_id}}", response_model=APIResponse[QuizQuestionResponse])
async def update_question(
    body: AdminQuizQuestionUpdate,
    question_id: int = Path(..., ge=1),
    _admin=Depends(get_current_admin),
) -> APIResponse[QuizQuestionResponse]:
    result = await AdminQuizService().update_question(question_id, body)
    return success(data=result)


@router.delete(f"{QUESTION}/{{question_id}}", response_model=APIResponse)
async def delete_question(
    question_id: int = Path(..., ge=1),
    _admin=Depends(get_current_admin),
):
    await AdminQuizService().delete_question(question_id)
    return success(message="题目已删除")


# ── Import route ──

@router.post("/import", response_model=APIResponse)
async def import_questions(
    file: UploadFile = File(..., description="CSV 文件"),
    create_missing_categories: bool = Form(False, description="是否自动创建缺失的分类"),
    _admin=Depends(get_current_admin),
):
    content = await file.read()
    result = await AdminQuizService().import_questions_csv(content, create_missing_categories)
    return success(data=result)
