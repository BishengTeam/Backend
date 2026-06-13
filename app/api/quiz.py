from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.quiz import (
    QuizCategoryTreeResponse,
    QuizCheckinRequest,
    QuizCheckinResponse,
    QuizCollectionRequest,
    QuizCollectionResponse,
    QuizExamStartRequest,
    QuizExamSubmitRequest,
    QuizQuestionResponse,
    QuizQuestionType,
    QuizRecordQuestionResponse,
    QuizSubmitRequest,
    QuizSubmitResponse,
    QuizWrongBookRequest,
    QuizWrongBookResponse,
)
from app.services.quiz import QuizService

router = APIRouter(prefix="/quiz", tags=["题库"])


@router.get("/categories",
    response_model=APIResponse[list[QuizCategoryTreeResponse]],
    summary="题库分类树",
    description="""
小程序 **题库** 页面使用。

**使用场景**: 获取题库分类的树形结构，用于分类导航和筛选题库

**响应**: 分类树列表，每项含分类 ID、名称和子分类

**认证**: 可选登录
    """,
)
async def list_categories() -> APIResponse[list[QuizCategoryTreeResponse]]:
    result = await QuizService().list_categories()
    return success(data=result)


@router.get("/questions",
    response_model=APIResponse[PaginatedData[QuizQuestionResponse]],
    summary="题目列表",
    description="""
小程序 **题库** 页面使用。

**使用场景**: 按分类和题型加载题目列表，支持分页

**查询参数**:
- `category_id`: 分类 ID（可选）
- `question_type`: 题型筛选：single_choice / multiple_choice / judge
- `page`: 页码，从 1 开始
- `page_size`: 每页条数，默认 20，最大 100

**响应**: 分页题目数据，含题干、选项、正确答案

**认证**: 可选登录
    """,
)
async def list_questions(
    category_id: int | None = Query(None, ge=1, description="分类 ID"),
    question_type: QuizQuestionType | None = Query(
        None,
        description="题型：single_choice / multiple_choice / judge",
    ),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
) -> APIResponse[PaginatedData[QuizQuestionResponse]]:
    result = await QuizService().list_questions(
        category_id=category_id, question_type=question_type,
        page=page, page_size=page_size,
    )
    return success(data=result)


@router.post("/submit",
    response_model=APIResponse[QuizSubmitResponse],
    summary="提交答题",
    description="""
小程序 **题库** 页面使用。

**使用场景**: 用户提交一道题的答案，返回判题结果和解析

**请求体**:
- `question_id`: 题目 ID
- `answer`: 用户答案

**响应**: 判题结果（正确/错误）、正确答案、解析

**认证**: 需登录
    """,
)
async def submit_answer(
    body: QuizSubmitRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizSubmitResponse]:
    result = await QuizService().submit_answer(current_user.id, body)
    return success(data=result)


@router.get("/wrong-book",
    response_model=APIResponse[PaginatedData[QuizRecordQuestionResponse]],
    summary="错题本列表",
    description="""
小程序 **题库** 页面使用。

**使用场景**: 查看当前用户的错题本，支持分页

**查询参数**:
- `page`: 页码，从 1 开始
- `page_size`: 每页条数，默认 20，最大 100

**响应**: 分页错题记录，含题目详情

**认证**: 需登录
    """,
)
async def list_wrong_book(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[PaginatedData[QuizRecordQuestionResponse]]:
    result = await QuizService().list_wrong_book(current_user.id, page=page, page_size=page_size)
    return success(data=result)


@router.post("/wrong-book",
    response_model=APIResponse[QuizWrongBookResponse],
    summary="加入错题本",
    description="""
小程序 **题库** 页面使用。

**使用场景**: 用户答题错误后，系统自动或手动将错题加入错题本

**请求体**:
- `question_id`: 题目 ID

**响应**: 错题记录

**认证**: 需登录
    """,
)
async def add_wrong_question(
    body: QuizWrongBookRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizWrongBookResponse]:
    result = await QuizService().add_wrong_question(current_user.id, body)
    return success(data=result)


@router.delete("/wrong-book/{id}",
    response_model=APIResponse[QuizWrongBookResponse],
    summary="移除错题",
    description="""
小程序 **题库** 页面使用。

**使用场景**: 用户从错题本中移除某道错题

**路径参数**:
- `id`: 错题记录 ID

**响应**: 删除结果

**认证**: 需登录
    """,
)
async def remove_wrong_question(
    id: int = Path(..., ge=1, description="错题记录 ID"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizWrongBookResponse]:
    result = await QuizService().remove_wrong_question(current_user.id, record_id=id)
    return success(data=result)


@router.get("/collections",
    response_model=APIResponse[PaginatedData[QuizRecordQuestionResponse]],
    summary="收藏列表",
    description="""
小程序 **题库** 页面使用。

**使用场景**: 查看当前用户收藏的题目列表，支持分页

**查询参数**:
- `page`: 页码，从 1 开始
- `page_size`: 每页条数，默认 20，最大 100

**响应**: 分页收藏记录，含题目详情

**认证**: 需登录
    """,
)
async def list_collections(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[PaginatedData[QuizRecordQuestionResponse]]:
    result = await QuizService().list_collections(current_user.id, page=page, page_size=page_size)
    return success(data=result)


@router.post("/collections",
    response_model=APIResponse[QuizCollectionResponse],
    summary="收藏题目",
    description="""
小程序 **题库** 页面使用。

**使用场景**: 用户收藏一道题目，方便后续复习

**请求体**:
- `question_id`: 题目 ID

**响应**: 收藏记录

**认证**: 需登录
    """,
)
async def add_collection(
    body: QuizCollectionRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizCollectionResponse]:
    result = await QuizService().add_collection(current_user.id, body)
    return success(data=result)


@router.delete("/collections/{id}",
    response_model=APIResponse[QuizCollectionResponse],
    summary="取消收藏",
    description="""
小程序 **题库** 页面使用。

**使用场景**: 用户取消收藏某道题目

**路径参数**:
- `id`: 收藏记录 ID

**响应**: 删除结果

**认证**: 需登录
    """,
)
async def remove_collection(
    id: int = Path(..., ge=1, description="收藏记录 ID"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizCollectionResponse]:
    result = await QuizService().remove_collection(current_user.id, record_id=id)
    return success(data=result)


@router.get("/checkin",
    response_model=APIResponse[QuizCheckinResponse],
    summary="签到状态",
    description="""
小程序 **题库** 页面使用。

**使用场景**: 查看当前用户今日签到状态和连续签到天数

**响应**: 签到状态，含今日是否已签到、连续签到天数

**认证**: 需登录
    """,
)
async def get_checkin_status(
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizCheckinResponse]:
    result = await QuizService().get_checkin_status(current_user.id)
    return success(data=result)


@router.post("/checkin",
    response_model=APIResponse[QuizCheckinResponse],
    summary="执行签到",
    description="""
小程序 **题库** 页面使用。

**使用场景**: 用户点击签到按钮，完成每日签到

**请求体**:
- `date`: 签到日期（通常为当日）

**响应**: 签到结果，含连续签到天数

**认证**: 需登录
    """,
)
async def checkin(
    body: QuizCheckinRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizCheckinResponse]:
    result = await QuizService().checkin(current_user.id, body)
    return success(data=result)


@router.get("/checkin/calendar",
    response_model=APIResponse[list[QuizCheckinResponse]],
    summary="签到日历",
    description="""
小程序 **打卡日历** 页面使用。

**使用场景**: 获取近 N 天（默认 30 天）的每日签到状态，用于日历展示

**查询参数**:
- `days`: 返回天数（默认 30，最大 365）

**响应**: 每日签到状态列表，含是否已签到、连续签到天数

**认证**: 需登录
    """,
)
async def get_checkin_calendar(
    days: int = Query(30, ge=1, le=365, description="返回天数"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[list[QuizCheckinResponse]]:
    result = await QuizService().get_checkin_calendar(current_user.id, days)
    return success(data=result)


# ── 练习统计 ──────────────────────────────────────────────────

@router.get("/stats",
    response_model=APIResponse,
    summary="练习统计",
)
async def get_stats(
    current_user: User = Depends(get_current_user),
):
    result = await QuizService().get_stats(current_user.id)
    return success(data=result)


# ── 模拟考试 ──────────────────────────────────────────────────

@router.post("/exam/start",
    response_model=APIResponse,
    summary="开始模拟考试",
)
async def start_exam(
    body: QuizExamStartRequest,
    current_user: User = Depends(get_current_user),
):
    result = await QuizService().start_exam(current_user.id, body)
    return success(data=result)


@router.post("/exam/submit",
    response_model=APIResponse,
    summary="提交模拟考试",
)
async def submit_exam(
    body: QuizExamSubmitRequest,
    current_user: User = Depends(get_current_user),
):
    result = await QuizService().submit_exam(current_user.id, body)
    return success(data=result)


@router.get("/exam/history",
    response_model=APIResponse,
    summary="考试记录",
)
async def list_exams(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
):
    result = await QuizService().list_exams(current_user.id, page, page_size)
    return success(data=result)


@router.get("/exam/current",
    response_model=APIResponse,
    summary="当前考试（断点续考）",
)
async def get_current_exam(
    current_user: User = Depends(get_current_user),
):
    result = await QuizService().get_current_exam(current_user.id)
    return success(data=result)


@router.get("/exam/{exam_id}",
    response_model=APIResponse,
    summary="考试详情",
)
async def get_exam(
    exam_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
):
    result = await QuizService().get_exam(current_user.id, exam_id)
    return success(data=result)


# ── 分类进度 ──────────────────────────────────────────────────

@router.get("/progress",
    response_model=APIResponse,
    summary="分类维度进度",
)
async def get_progress(
    category_id: int | None = Query(None, ge=1),
    current_user: User = Depends(get_current_user),
):
    result = await QuizService().get_progress(current_user.id, category_id)
    return success(data=result)


# ── 近期记录 ──────────────────────────────────────────────────

@router.get("/recent",
    response_model=APIResponse,
    summary="近期答题记录",
)
async def get_recent(
    limit: int = Query(10, ge=1, le=50),
    current_user: User = Depends(get_current_user),
):
    result = await QuizService().get_recent(current_user.id, limit)
    return success(data=result)