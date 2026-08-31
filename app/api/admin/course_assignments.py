from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import require_permission
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.course_assignment import (
    AdminCourseAssignmentSubmissionDetail,
    CourseAssignmentAction,
    CourseAssignmentCreate,
    CourseAssignmentResponse,
    CourseAssignmentReviewLogItem,
    CourseAssignmentReviewSave,
    CourseAssignmentReviewSaved,
    CourseAssignmentSubmissionListItem,
    CourseAssignmentSubmissionQuery,
    CourseAssignmentUpdate,
)
from app.services.course_assignment import (
    CourseAssignmentReviewService,
    CourseAssignmentService,
)

router = APIRouter(prefix="/course-assignments", tags=["管理后台-课程作业"])


@router.get("", response_model=APIResponse[list[CourseAssignmentResponse]])
async def list_assignments(
    course_id: int | None = Query(None, ge=1),
    library_id: int | None = Query(None, ge=1),
    status: str | None = Query(None, pattern="^(draft|published|disabled)$"),
    _admin=Depends(require_permission("course_assignment_manage")),
) -> APIResponse[list[CourseAssignmentResponse]]:
    return success(
        data=await CourseAssignmentService().list_assignments(
            course_id=course_id,
            library_id=library_id,
            status=status,
        )
    )


@router.post("", response_model=APIResponse[CourseAssignmentResponse])
async def create_assignment(
    body: CourseAssignmentCreate,
    admin=Depends(require_permission("course_assignment_manage")),
) -> APIResponse[CourseAssignmentResponse]:
    return success(
        data=await CourseAssignmentService().create_assignment(
            body, admin_id=admin.id
        )
    )


@router.get("/submissions")
async def list_submissions(
    course_id: int | None = Query(None, ge=1),
    library_id: int | None = Query(None, ge=1),
    assignment_id: int | None = Query(None, ge=1),
    status: str | None = Query(
        None, pattern="^(draft|submitted|claimed|graded)$"
    ),
    keyword: str | None = Query(None, min_length=1, max_length=128),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin=Depends(require_permission("course_assignment_review")),
) -> APIResponse[PaginatedData[CourseAssignmentSubmissionListItem]]:
    query = CourseAssignmentSubmissionQuery(
        course_id=course_id,
        library_id=library_id,
        assignment_id=assignment_id,
        status=status,  # type: ignore[arg-type]
        keyword=keyword,
        page=page,
        page_size=page_size,
    )
    return success(data=await CourseAssignmentReviewService().list_submissions(query))


@router.get("/submissions/{submission_id}")
async def get_submission(
    submission_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("course_assignment_review")),
) -> APIResponse[AdminCourseAssignmentSubmissionDetail]:
    return success(
        data=await CourseAssignmentReviewService().get_submission(submission_id)
    )


@router.post(
    "/submissions/{submission_id}/claim",
    response_model=APIResponse[CourseAssignmentReviewSaved],
)
async def claim_submission(
    submission_id: int = Path(..., ge=1),
    body: CourseAssignmentAction | None = None,
    admin=Depends(require_permission("course_assignment_review")),
) -> APIResponse[CourseAssignmentReviewSaved]:
    lock_version = body.lock_version if body is not None else 0
    return success(
        data=await CourseAssignmentReviewService().claim(
            submission_id,
            lock_version=lock_version,
            admin_id=admin.id,
        )
    )


@router.put(
    "/submissions/{submission_id}/review",
    response_model=APIResponse[CourseAssignmentReviewSaved],
)
async def save_review(
    submission_id: int = Path(..., ge=1),
    body: CourseAssignmentReviewSave | None = None,
    admin=Depends(require_permission("course_assignment_review")),
) -> APIResponse[CourseAssignmentReviewSaved]:
    if body is None:
        body = CourseAssignmentReviewSave(lock_version=0)
    return success(
        data=await CourseAssignmentReviewService().save_review(
            submission_id,
            body,
            admin_id=admin.id,
        )
    )


@router.post(
    "/submissions/{submission_id}/reopen",
    response_model=APIResponse[CourseAssignmentReviewSaved],
)
async def reopen_submission(
    submission_id: int = Path(..., ge=1),
    body: CourseAssignmentAction | None = None,
    admin=Depends(require_permission("course_assignment_review")),
) -> APIResponse[CourseAssignmentReviewSaved]:
    lock_version = body.lock_version if body is not None else 0
    return success(
        data=await CourseAssignmentReviewService().reopen(
            submission_id,
            lock_version=lock_version,
            admin_id=admin.id,
        )
    )


@router.get(
    "/submissions/{submission_id}/logs",
    response_model=APIResponse[list[CourseAssignmentReviewLogItem]],
)
async def list_review_logs(
    submission_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("course_assignment_review")),
) -> APIResponse[list[CourseAssignmentReviewLogItem]]:
    return success(
        data=await CourseAssignmentReviewService().list_logs(submission_id)
    )


@router.get("/{assignment_id}", response_model=APIResponse[CourseAssignmentResponse])
async def get_assignment(
    assignment_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("course_assignment_manage")),
) -> APIResponse[CourseAssignmentResponse]:
    return success(data=await CourseAssignmentService().get_assignment(assignment_id))


@router.put("/{assignment_id}", response_model=APIResponse[CourseAssignmentResponse])
async def update_assignment(
    body: CourseAssignmentUpdate,
    assignment_id: int = Path(..., ge=1),
    admin=Depends(require_permission("course_assignment_manage")),
) -> APIResponse[CourseAssignmentResponse]:
    return success(
        data=await CourseAssignmentService().update_assignment(
            assignment_id,
            body,
            admin_id=admin.id,
        )
    )


@router.post(
    "/{assignment_id}/publish", response_model=APIResponse[CourseAssignmentResponse]
)
async def publish_assignment(
    assignment_id: int = Path(..., ge=1),
    body: CourseAssignmentAction | None = None,
    admin=Depends(require_permission("course_assignment_manage")),
) -> APIResponse[CourseAssignmentResponse]:
    return success(
        data=await CourseAssignmentService().publish_assignment(
            assignment_id,
            lock_version=body.lock_version if body is not None else 0,
            admin_id=admin.id,
        )
    )


@router.post(
    "/{assignment_id}/disable", response_model=APIResponse[CourseAssignmentResponse]
)
async def disable_assignment(
    assignment_id: int = Path(..., ge=1),
    body: CourseAssignmentAction | None = None,
    admin=Depends(require_permission("course_assignment_manage")),
) -> APIResponse[CourseAssignmentResponse]:
    return success(
        data=await CourseAssignmentService().disable_assignment(
            assignment_id,
            lock_version=body.lock_version if body is not None else 0,
            admin_id=admin.id,
        )
    )
