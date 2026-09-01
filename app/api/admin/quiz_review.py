"""Admin endpoints for manual essay review of submitted exams."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import require_permission
from app.schemas.admin_quiz_review_contract import (
    AdminQuizReviewClaimResponse,
    AdminQuizReviewCompleteResponse,
    AdminQuizReviewDetail,
    AdminQuizReviewListItem,
    AdminQuizReviewListQuery,
    AdminQuizReviewSubmitRequest,
)
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.admin_quiz_review import AdminQuizReviewService


router = APIRouter(prefix="/quiz/reviews", tags=["管理后台-考试人工评阅"])
service = AdminQuizReviewService()


@router.get(
    "",
    response_model=APIResponse[PaginatedData[AdminQuizReviewListItem]],
    summary="待评阅考试列表",
)
async def list_reviews(
    status: str | None = Query(default=None, pattern="^(pending|in_progress|recalled)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _admin=Depends(require_permission("quiz_review")),
) -> APIResponse[PaginatedData[AdminQuizReviewListItem]]:
    return success(
        data=await service.list_reviews(
            AdminQuizReviewListQuery(
                status=status,  # type: ignore[arg-type]
                page=page,
                page_size=page_size,
            )
        )
    )


@router.post(
    "/{exam_id}/claim",
    response_model=APIResponse[AdminQuizReviewClaimResponse],
    summary="领取评阅任务",
)
async def claim_review(
    exam_id: int = Path(..., ge=1),
    admin=Depends(require_permission("quiz_review")),
) -> APIResponse[AdminQuizReviewClaimResponse]:
    return success(data=await service.claim_review(exam_id, admin_id=admin.id))


@router.get(
    "/{exam_id}",
    response_model=APIResponse[AdminQuizReviewDetail],
    summary="评阅详情（学生答案 + 参考答案 + 已评分况）",
)
async def get_review_detail(
    exam_id: int = Path(..., ge=1),
    admin=Depends(require_permission("quiz_review")),
) -> APIResponse[AdminQuizReviewDetail]:
    return success(data=await service.get_review_detail(exam_id, admin_id=admin.id))


@router.post(
    "/{exam_id}/verdicts",
    response_model=APIResponse[AdminQuizReviewClaimResponse],
    summary="提交问答题评分（逐题三档 + 评语）",
)
async def submit_verdicts(
    body: AdminQuizReviewSubmitRequest,
    exam_id: int = Path(..., ge=1),
    admin=Depends(require_permission("quiz_review")),
) -> APIResponse[AdminQuizReviewClaimResponse]:
    return success(
        data=await service.submit_verdicts(exam_id, body, admin_id=admin.id)
    )


@router.post(
    "/{exam_id}/complete",
    response_model=APIResponse[AdminQuizReviewCompleteResponse],
    summary="完成评阅并公布成绩",
)
async def complete_review(
    exam_id: int = Path(..., ge=1),
    admin=Depends(require_permission("quiz_review")),
) -> APIResponse[AdminQuizReviewCompleteResponse]:
    return success(data=await service.complete_review(exam_id, admin_id=admin.id))


@router.post(
    "/{exam_id}/recall",
    response_model=APIResponse[AdminQuizReviewClaimResponse],
    summary="撤回评分重新评阅",
)
async def recall_review(
    exam_id: int = Path(..., ge=1),
    admin=Depends(require_permission("quiz_review")),
) -> APIResponse[AdminQuizReviewClaimResponse]:
    return success(
        data=await service.recall_review(
            exam_id,
            admin_id=admin.id,
            is_super_admin=admin.role == "super_admin",
        )
    )
