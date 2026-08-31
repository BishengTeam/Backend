from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.schemas.common import APIResponse, success
from app.schemas.course_assignment import (
    CourseAssignmentAnswerSave,
    CourseAssignmentAnswerSaved,
    CourseAssignmentSubmitResponse,
    CourseAssignmentWithdrawResponse,
    UserCourseAssignmentDetail,
    UserCourseAssignmentListItem,
)
from app.services.course_assignment import UserCourseAssignmentService

router = APIRouter(prefix="/course-assignments", tags=["小程序-课程作业"])


@router.get("", response_model=APIResponse[list[UserCourseAssignmentListItem]])
async def list_my_assignments(
    course_id: int | None = Query(None, ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse[list[UserCourseAssignmentListItem]]:
    return success(
        data=await UserCourseAssignmentService().list_assignments(
            current_user.id, course_id=course_id
        )
    )


@router.post(
    "/{assignment_id}/start",
    response_model=APIResponse[UserCourseAssignmentDetail],
)
async def start_assignment(
    assignment_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse[UserCourseAssignmentDetail]:
    return success(
        data=await UserCourseAssignmentService().start_assignment(
            current_user.id, assignment_id
        )
    )


@router.get(
    "/{assignment_id}",
    response_model=APIResponse[UserCourseAssignmentDetail],
)
async def get_assignment(
    assignment_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse[UserCourseAssignmentDetail]:
    return success(
        data=await UserCourseAssignmentService().get_assignment(
            current_user.id, assignment_id
        )
    )


@router.put(
    "/{assignment_id}/answers",
    response_model=APIResponse[CourseAssignmentAnswerSaved],
)
async def save_answers(
    body: CourseAssignmentAnswerSave,
    assignment_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse[CourseAssignmentAnswerSaved]:
    return success(
        data=await UserCourseAssignmentService().save_answers(
            current_user.id, assignment_id, body.answers
        )
    )


@router.post(
    "/{assignment_id}/submit",
    response_model=APIResponse[CourseAssignmentSubmitResponse],
)
async def submit_assignment(
    assignment_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse[CourseAssignmentSubmitResponse]:
    return success(
        data=await UserCourseAssignmentService().submit_assignment(
            current_user.id, assignment_id
        )
    )


@router.post(
    "/{assignment_id}/withdraw",
    response_model=APIResponse[CourseAssignmentWithdrawResponse],
)
async def withdraw_assignment(
    assignment_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse[CourseAssignmentWithdrawResponse]:
    return success(
        data=await UserCourseAssignmentService().withdraw_assignment(
            current_user.id, assignment_id
        )
    )
