from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.quiz_contract import (
    QuizCategoryNode,
    QuizCheckinCalendarQuery,
    QuizCheckinDay,
    QuizCheckinStatusResponse,
    QuizCollectionCreate,
    QuizCollectionItem,
    QuizCollectionMutationResponse,
    QuizPracticeAbandonResponse,
    QuizPracticeAttemptCreate,
    QuizPracticeAttemptResult,
    QuizPracticeHistoryItem,
    QuizPracticeHistoryQuery,
    QuizPracticeSessionCreate,
    QuizPracticeSessionResponse,
    QuizPublicQuestion,
    QuizQuestionListQuery,
    QuizStatsResponse as QuizContractStatsResponse,
    QuizWrongBookItem,
    QuizWrongBookQuery,
)
from app.schemas.quiz import (
    QuizExamStartRequest,
    QuizExamSubmitRequest,
)
from app.services.quiz import QuizService
from app.services.quiz_practice import QuizPracticeService

router = APIRouter(prefix="/quiz", tags=["题库"])


@router.get("/categories",
    response_model=APIResponse[list[QuizCategoryNode]],
    summary="题库分类树",
    description="""
小程序 **题库** 页面使用。

**使用场景**: 获取题库分类的树形结构，用于分类导航和筛选题库

**响应**: 分类树列表，每项含分类 ID、名称和子分类

**认证**: 可选登录
    """,
)
async def list_categories() -> APIResponse[list[QuizCategoryNode]]:
    result = await QuizPracticeService().list_categories()
    return success(data=result)


@router.get("/questions",
    response_model=APIResponse[PaginatedData[QuizPublicQuestion]],
    summary="题目列表",
    description="""
小程序 **题库** 页面使用。

**使用场景**: 按分类和题型加载题目列表，支持分页

**查询参数**:
- `category_id`: 分类 ID（可选）
- `question_type`: 题型筛选：single_choice / multiple_choice / judge
- `page`: 页码，从 1 开始
- `page_size`: 每页条数，默认 20，最大 100

**响应**: 分页题目数据，仅含题干和选项

**认证**: 需登录
    """,
)
async def list_questions(
    query: QuizQuestionListQuery = Depends(),
    current_user: User = Depends(get_current_user),
) -> APIResponse[PaginatedData[QuizPublicQuestion]]:
    result = await QuizPracticeService().list_questions(current_user.id, query)
    return success(data=result)


@router.post(
    "/practice-sessions",
    response_model=APIResponse[QuizPracticeSessionResponse],
    summary="创建练习会话",
)
async def create_practice_session(
    body: QuizPracticeSessionCreate,
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizPracticeSessionResponse]:
    result = await QuizPracticeService().create_session(current_user.id, body)
    return success(data=result)


@router.get(
    "/practice-sessions/current",
    response_model=APIResponse[QuizPracticeSessionResponse | None],
    summary="当前练习会话",
)
async def get_current_practice_session(
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizPracticeSessionResponse | None]:
    result = await QuizPracticeService().get_current_session(current_user.id)
    return success(data=result)


@router.get(
    "/practice-sessions/{session_id}",
    response_model=APIResponse[QuizPracticeSessionResponse],
    summary="练习会话详情",
)
async def get_practice_session(
    session_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizPracticeSessionResponse]:
    result = await QuizPracticeService().get_session(current_user.id, session_id)
    return success(data=result)


@router.post(
    "/practice-sessions/{session_id}/attempts",
    response_model=APIResponse[QuizPracticeAttemptResult],
    summary="提交练习作答",
)
async def submit_practice_attempt(
    body: QuizPracticeAttemptCreate,
    session_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizPracticeAttemptResult]:
    result = await QuizPracticeService().submit_attempt(current_user.id, session_id, body)
    return success(data=result)


@router.post(
    "/practice-sessions/{session_id}/abandon",
    response_model=APIResponse[QuizPracticeAbandonResponse],
    summary="放弃练习会话",
)
async def abandon_practice_session(
    session_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizPracticeAbandonResponse]:
    result = await QuizPracticeService().abandon_session(current_user.id, session_id)
    return success(data=result)


@router.get(
    "/practice-history",
    response_model=APIResponse[PaginatedData[QuizPracticeHistoryItem]],
    summary="练习历史",
)
async def get_practice_history(
    query: QuizPracticeHistoryQuery = Depends(),
    current_user: User = Depends(get_current_user),
) -> APIResponse[PaginatedData[QuizPracticeHistoryItem]]:
    result = await QuizPracticeService().get_history(current_user.id, query)
    return success(data=result)


@router.get("/wrong-book",
    response_model=APIResponse[PaginatedData[QuizWrongBookItem]],
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
    query: QuizWrongBookQuery = Depends(),
    current_user: User = Depends(get_current_user),
) -> APIResponse[PaginatedData[QuizWrongBookItem]]:
    result = await QuizPracticeService().list_wrong_book(current_user.id, query)
    return success(data=result)


@router.get("/collections",
    response_model=APIResponse[PaginatedData[QuizCollectionItem]],
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
    query: QuizWrongBookQuery = Depends(),
    current_user: User = Depends(get_current_user),
) -> APIResponse[PaginatedData[QuizCollectionItem]]:
    result = await QuizPracticeService().list_collections(current_user.id, query)
    return success(data=result)


@router.post("/collections",
    response_model=APIResponse[QuizCollectionMutationResponse],
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
    body: QuizCollectionCreate,
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizCollectionMutationResponse]:
    result = await QuizPracticeService().add_collection(current_user.id, body)
    return success(data=result)


@router.delete("/collections/{question_id}",
    response_model=APIResponse[QuizCollectionMutationResponse],
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
    question_id: int = Path(..., ge=1, description="题目 ID"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizCollectionMutationResponse]:
    result = await QuizPracticeService().remove_collection(current_user.id, question_id)
    return success(data=result)


@router.get("/checkin",
    response_model=APIResponse[QuizCheckinStatusResponse],
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
) -> APIResponse[QuizCheckinStatusResponse]:
    result = await QuizPracticeService().get_checkin_status(current_user.id)
    return success(data=result)


@router.get("/checkin/calendar",
    response_model=APIResponse[list[QuizCheckinDay]],
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
    query: QuizCheckinCalendarQuery = Depends(),
    current_user: User = Depends(get_current_user),
) -> APIResponse[list[QuizCheckinDay]]:
    result = await QuizPracticeService().get_checkin_calendar(current_user.id, query)
    return success(data=result)


# ── 练习统计 ──────────────────────────────────────────────────

@router.get("/stats",
    response_model=APIResponse[QuizContractStatsResponse],
    summary="练习统计",
)
async def get_stats(
    current_user: User = Depends(get_current_user),
):
    result = await QuizPracticeService().get_stats(current_user.id)
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
