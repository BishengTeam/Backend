from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.middleware.auth import get_current_user
from app.middleware.rate_limit import limiter, quiz_user_key
from app.domain.user.src.index import User
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.quiz_contract import (
    QuizCategoryNode,
    QuizLibraryCatalogDetail,
    QuizLibraryCatalogItem,
    QuizCheckinCalendarQuery,
    QuizCheckinDay,
    QuizCheckinStatusResponse,
    QuizCollectionCreate,
    QuizCollectionItem,
    QuizCollectionMutationResponse,
    QuizExamActionResponse,
    QuizExamAnswerSave,
    QuizExamAnswerSaved,
    QuizExamCreate,
    QuizExamDetailResponse,
    QuizExamListItem,
    QuizExamListQuery,
    QuizPracticeAbandonResponse,
    QuizPracticeAnswerSave,
    QuizPracticeAnswerSaved,
    QuizPracticeAttemptCreate,
    QuizPracticeAttemptResult,
    QuizPracticeHistoryItem,
    QuizPracticeHistoryQuery,
    QuizPracticeSessionCreate,
    QuizPracticeSessionResponse,
    QuizPracticeScopePreview,
    QuizPracticeSkipResponse,
    QuizPublicQuestion,
    QuizLibraryProgressResponse,
    QuizQuestionListQuery,
    QuizStatsResponse as QuizContractStatsResponse,
    QuizStatsQuery,
    QuizWrongBookItem,
    QuizWrongBookQuery,
)
from app.services.quiz_exam import QuizExamService
from app.services.quiz_practice import QuizPracticeService
from app.services.quiz_v2 import QuizV2Service
from app.port.config import settings
from app.domain.community.src.rule.quiz import QuizQuestionType
from app.domain.community.src.rule.quiz import QuizPracticeMode, QuizPracticeScopeType

router = APIRouter(prefix="/quiz", tags=["题库"])


def _validated_query(model, values: dict):
    """Construct a Pydantic query model without FastAPI's body-model quirk."""

    try:
        return model(**values)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc


async def quiz_question_list_query(
    category_id: int | None = Query(None, ge=1),
    question_type: QuizQuestionType | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> QuizQuestionListQuery:
    return _validated_query(
        QuizQuestionListQuery,
        {
            "category_id": category_id,
            "question_type": question_type,
            "page": page,
            "page_size": page_size,
        },
    )


async def quiz_practice_history_query(
    category_id: int | None = Query(None, ge=1),
    question_type: QuizQuestionType | None = Query(None),
    is_correct: bool | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> QuizPracticeHistoryQuery:
    return _validated_query(
        QuizPracticeHistoryQuery,
        {
            "category_id": category_id,
            "question_type": question_type,
            "is_correct": is_correct,
            "date_from": date_from,
            "date_to": date_to,
            "page": page,
            "page_size": page_size,
        },
    )


async def quiz_page_query(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> QuizWrongBookQuery:
    return QuizWrongBookQuery(page=page, page_size=page_size)


async def quiz_checkin_calendar_query(
    date_from: date = Query(...),
    date_to: date = Query(...),
) -> QuizCheckinCalendarQuery:
    return _validated_query(
        QuizCheckinCalendarQuery,
        {"date_from": date_from, "date_to": date_to},
    )


async def quiz_exam_list_query(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> QuizExamListQuery:
    return QuizExamListQuery(page=page, page_size=page_size)


async def quiz_stats_query(
    scope_type: QuizPracticeScopeType | None = Query(None),
    scope_id: int | None = Query(None, ge=1),
) -> QuizStatsQuery:
    return _validated_query(
        QuizStatsQuery,
        {"scope_type": scope_type, "scope_id": scope_id},
    )


@router.get(
    "/libraries",
    response_model=APIResponse[list[QuizLibraryCatalogItem]],
    summary="当前用户可见的 V2 题库",
)
async def list_quiz_libraries(
    current_user: User = Depends(get_current_user),
) -> APIResponse[list[QuizLibraryCatalogItem]]:
    return success(data=await QuizV2Service().list_libraries(current_user.id))


@router.get(
    "/libraries/{library_id}",
    response_model=APIResponse[QuizLibraryCatalogDetail],
    summary="V2 题库固定层级目录",
)
async def get_quiz_library(
    library_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizLibraryCatalogDetail]:
    return success(
        data=await QuizV2Service().get_library(current_user.id, library_id)
    )


@router.get(
    "/libraries/{library_id}/progress",
    response_model=APIResponse[QuizLibraryProgressResponse],
    summary="题库章节练习进度",
    description="""
小程序 **题库目录** 使用。

**使用场景**: 展开题库目录时加载整库/模块/知识点三层的练习进度。

**口径**: 仅统计练习作答（含错题专项）；已做为去重题目数，正确率为首答正确率；模拟考试不计入。

**响应**: 库汇总 + 模块列表（含各自知识点列表），每级含总题数、已做题数、正确率。

**认证**: 需登录
    """,
)
async def get_quiz_library_progress(
    library_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizLibraryProgressResponse]:
    return success(
        data=await QuizV2Service().get_library_progress(
            current_user.id, library_id
        )
    )


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
@limiter.limit(
    f"{settings.QUIZ_QUESTION_LIST_RATE_PER_MINUTE}/minute",
    key_func=quiz_user_key,
    error_message="题目列表请求过于频繁",
)
async def list_questions(
    request: Request,
    query: QuizQuestionListQuery = Depends(quiz_question_list_query),
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
    if body.scope_type is not None:
        result = await QuizV2Service().create_practice_session(current_user.id, body)
    else:
        result = await QuizPracticeService().create_session(current_user.id, body)
    return success(data=result)


@router.get(
    "/practice-scopes/preview",
    response_model=APIResponse[QuizPracticeScopePreview],
    summary="预览 V2 全量练习范围",
)
async def preview_practice_scope(
    scope_type: QuizPracticeScopeType = Query(...),
    scope_id: int = Query(..., ge=1),
    mode: QuizPracticeMode = Query(QuizPracticeMode.FULL),
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizPracticeScopePreview]:
    return success(
        data=await QuizV2Service().preview_practice_scope(
            current_user.id, str(scope_type), scope_id, str(mode)
        )
    )


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
    v2 = QuizV2Service()
    result = (
        await v2.get_practice_session(current_user.id, session_id)
        if await v2.is_v2_session(current_user.id, session_id)
        else await QuizPracticeService().get_session(current_user.id, session_id)
    )
    return success(data=result)


@router.post(
    "/practice-sessions/{session_id}/questions/{session_question_id}/skip",
    response_model=APIResponse[QuizPracticeSkipResponse],
    summary="V2 练习跳过当前题（每题一次）",
)
async def skip_practice_question(
    session_id: int = Path(..., ge=1),
    session_question_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizPracticeSkipResponse]:
    return success(
        data=await QuizV2Service().skip_practice_question(
            current_user.id, session_id, session_question_id
        )
    )


@router.put(
    "/practice-sessions/{session_id}/answers/{session_question_id}",
    response_model=APIResponse[QuizPracticeAnswerSaved],
    summary="暂存练习答案",
)
@limiter.limit(
    f"{settings.QUIZ_ANSWER_SAVE_RATE_PER_MINUTE}/minute",
    key_func=quiz_user_key,
    error_message="练习答案保存请求过于频繁",
)
async def save_practice_answer(
    request: Request,
    body: QuizPracticeAnswerSave,
    session_id: int = Path(..., ge=1),
    session_question_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizPracticeAnswerSaved]:
    result = await QuizPracticeService().save_answer(
        current_user.id,
        session_id,
        session_question_id,
        body,
    )
    return success(data=result)


@router.post(
    "/practice-sessions/{session_id}/submit",
    response_model=APIResponse[QuizPracticeSessionResponse],
    summary="交卷并统一结算练习",
)
async def submit_practice_session(
    session_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizPracticeSessionResponse]:
    result = await QuizPracticeService().submit_session(current_user.id, session_id)
    return success(data=result)


@router.post(
    "/practice-sessions/{session_id}/attempts",
    response_model=APIResponse[QuizPracticeAttemptResult],
    summary="提交练习作答",
)
@limiter.limit(
    f"{settings.QUIZ_ANSWER_SAVE_RATE_PER_MINUTE}/minute",
    key_func=quiz_user_key,
    error_message="练习作答请求过于频繁",
)
async def submit_practice_attempt(
    request: Request,
    body: QuizPracticeAttemptCreate,
    session_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizPracticeAttemptResult]:
    # V2 currently shares the immutable attempt/statistics transaction with
    # the legacy-compatible service, whose V2 branch enforces pause/expiry and
    # revision-snapshot semantics before accepting the answer.
    result = await QuizPracticeService().submit_attempt(
        current_user.id, session_id, body
    )
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
    query: QuizPracticeHistoryQuery = Depends(quiz_practice_history_query),
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
    query: QuizWrongBookQuery = Depends(quiz_page_query),
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
    query: QuizWrongBookQuery = Depends(quiz_page_query),
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
    query: QuizCheckinCalendarQuery = Depends(quiz_checkin_calendar_query),
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
    query: QuizStatsQuery = Depends(quiz_stats_query),
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizContractStatsResponse]:
    result = await QuizPracticeService().get_stats(current_user.id, query)
    return success(data=result)


# ── 新版模拟考试 ───────────────────────────────────────────────

@router.post(
    "/exams",
    response_model=APIResponse[QuizExamDetailResponse],
    summary="创建模拟考试",
)
async def create_exam(
    body: QuizExamCreate,
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizExamDetailResponse]:
    result = await QuizExamService().create_exam(current_user.id, body)
    return success(data=result)


@router.get(
    "/exams/current",
    response_model=APIResponse[QuizExamDetailResponse | None],
    summary="当前模拟考试",
)
async def get_current_exam_v2(
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizExamDetailResponse | None]:
    result = await QuizExamService().get_current_exam(current_user.id)
    return success(data=result)


@router.get(
    "/exams",
    response_model=APIResponse[PaginatedData[QuizExamListItem]],
    summary="模拟考试历史",
)
async def list_exams_v2(
    query: QuizExamListQuery = Depends(quiz_exam_list_query),
    current_user: User = Depends(get_current_user),
) -> APIResponse[PaginatedData[QuizExamListItem]]:
    result = await QuizExamService().list_exams(current_user.id, query)
    return success(data=result)


@router.get(
    "/exams/{exam_id}",
    response_model=APIResponse[QuizExamDetailResponse],
    summary="模拟考试详情",
)
async def get_exam_v2(
    exam_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizExamDetailResponse]:
    result = await QuizExamService().get_exam(current_user.id, exam_id)
    return success(data=result)


@router.put(
    "/exams/{exam_id}/answers/{exam_question_id}",
    response_model=APIResponse[QuizExamAnswerSaved],
    summary="保存模拟考试答案",
)
@limiter.limit(
    f"{settings.QUIZ_ANSWER_SAVE_RATE_PER_MINUTE}/minute",
    key_func=quiz_user_key,
    error_message="考试答案保存请求过于频繁",
)
async def save_exam_answer_v2(
    request: Request,
    body: QuizExamAnswerSave,
    exam_id: int = Path(..., ge=1),
    exam_question_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizExamAnswerSaved]:
    result = await QuizExamService().save_answer(
        current_user.id,
        exam_id,
        exam_question_id,
        body,
    )
    return success(data=result)


@router.post(
    "/exams/{exam_id}/submit",
    response_model=APIResponse[QuizExamActionResponse],
    summary="模拟考试交卷",
)
async def submit_exam_v2(
    exam_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizExamActionResponse]:
    result = await QuizExamService().submit_exam(current_user.id, exam_id)
    return success(data=result)


@router.post(
    "/exams/{exam_id}/abandon",
    response_model=APIResponse[QuizExamActionResponse],
    summary="放弃模拟考试",
)
async def abandon_exam_v2(
    exam_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizExamActionResponse]:
    result = await QuizExamService().abandon_exam(current_user.id, exam_id)
    return success(data=result)
