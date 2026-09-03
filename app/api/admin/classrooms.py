from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import require_permission
from app.schemas.classroom import (
    ClassroomCreate,
    ClassroomQuestionImport,
    ClassroomQuizCreate,
    ClassroomSubmissionReview,
    ClassroomUpdate,
    ClassroomVideoCreate,
    ClassroomVideoUploadUrlRequest,
)
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.classroom_admin import ClassroomAdminService

router = APIRouter(prefix="/classrooms", tags=["管理后台-课堂"])


def _teacher_scope(admin) -> int | None:
    """teacher 只能看自己的课堂；其他角色（super_admin）可监督全部。"""
    return admin.id if admin.role == "teacher" else None


@router.get("", response_model=APIResponse[PaginatedData], summary="课堂列表")
async def list_classrooms(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    admin=Depends(require_permission("classroom:manage")),
):
    result = await ClassroomAdminService().list_classrooms(
        _teacher_scope(admin), page, page_size
    )
    return success(data=PaginatedData(**result))


@router.post("", response_model=APIResponse, summary="创建课堂")
async def create_classroom(
    body: ClassroomCreate,
    admin=Depends(require_permission("classroom:manage")),
):
    result = await ClassroomAdminService().create(admin.id, body)
    return success(data=result)


@router.get("/{classroom_id}", response_model=APIResponse, summary="课堂详情")
async def get_classroom(
    classroom_id: int = Path(..., ge=1),
    admin=Depends(require_permission("classroom:manage")),
):
    return success(data=await ClassroomAdminService().get(classroom_id, _teacher_scope(admin)))


@router.put("/{classroom_id}", response_model=APIResponse, summary="改名")
async def update_classroom(
    body: ClassroomUpdate,
    classroom_id: int = Path(..., ge=1),
    admin=Depends(require_permission("classroom:manage")),
):
    await ClassroomAdminService().update(classroom_id, _teacher_scope(admin), body)
    return success(message="课堂已更新")


@router.post("/{classroom_id}/stop", response_model=APIResponse, summary="停课（冻结学生访问）")
async def stop_classroom(
    classroom_id: int = Path(..., ge=1),
    admin=Depends(require_permission("classroom:manage")),
):
    await ClassroomAdminService().stop(classroom_id, _teacher_scope(admin))
    return success(message="课堂已停课")


@router.post("/{classroom_id}/join-code/refresh", response_model=APIResponse, summary="刷新课堂码（30 分钟有效）")
async def refresh_join_code(
    classroom_id: int = Path(..., ge=1),
    admin=Depends(require_permission("classroom:manage")),
):
    return success(data=await ClassroomAdminService().refresh_join_code(classroom_id, _teacher_scope(admin)))


@router.get("/{classroom_id}/students", response_model=APIResponse, summary="学生名单")
async def list_students(
    classroom_id: int = Path(..., ge=1),
    admin=Depends(require_permission("classroom:manage")),
):
    return success(data=await ClassroomAdminService().list_students(classroom_id, _teacher_scope(admin)))


@router.delete("/{classroom_id}/students/{user_id}", response_model=APIResponse, summary="移除学生")
async def remove_student(
    classroom_id: int = Path(..., ge=1), user_id: int = Path(..., ge=1),
    admin=Depends(require_permission("classroom:manage")),
):
    await ClassroomAdminService().remove_student(classroom_id, user_id, _teacher_scope(admin))
    return success(message="学生已移除")


# ── 视频 ──────────────────────────────────────────────────

@router.post("/{classroom_id}/videos/upload-url", response_model=APIResponse, summary="获取视频直传 URL")
async def video_upload_url(
    body: ClassroomVideoUploadUrlRequest,
    classroom_id: int = Path(..., ge=1),
    admin=Depends(require_permission("classroom:manage")),
):
    result = await ClassroomAdminService().video_upload_url(
        classroom_id, _teacher_scope(admin),
        body.filename, body.content_type, body.size_bytes,
    )
    return success(data=result)


@router.post("/{classroom_id}/videos", response_model=APIResponse, summary="确认视频（创建记录）")
async def create_video(
    body: ClassroomVideoCreate,
    classroom_id: int = Path(..., ge=1),
    admin=Depends(require_permission("classroom:manage")),
):
    return success(data=await ClassroomAdminService().create_video(
        classroom_id, _teacher_scope(admin), body))


@router.get("/{classroom_id}/videos", response_model=APIResponse, summary="视频列表")
async def list_videos(
    classroom_id: int = Path(..., ge=1),
    admin=Depends(require_permission("classroom:manage")),
):
    return success(data=await ClassroomAdminService().list_videos(classroom_id, _teacher_scope(admin)))


@router.get("/{classroom_id}/videos/{video_id}/play-url", response_model=APIResponse, summary="视频预览地址（管理端）")
async def video_play_url(
    classroom_id: int = Path(..., ge=1), video_id: int = Path(..., ge=1),
    admin=Depends(require_permission("classroom:manage")),
):
    url = await ClassroomAdminService().video_play_url(classroom_id, video_id, _teacher_scope(admin))
    return success(data={"url": url})

@router.delete("/{classroom_id}/videos/{video_id}", response_model=APIResponse, summary="删除视频")
async def delete_video(
    classroom_id: int = Path(..., ge=1), video_id: int = Path(..., ge=1),
    admin=Depends(require_permission("classroom:manage")),
):
    await ClassroomAdminService().delete_video(classroom_id, video_id, _teacher_scope(admin))
    return success(message="视频已删除")


# ── 题库 ──────────────────────────────────────────────────

@router.post("/{classroom_id}/questions/import", response_model=APIResponse, summary="导入题目（草稿）")
async def import_questions(
    body: ClassroomQuestionImport,
    classroom_id: int = Path(..., ge=1),
    admin=Depends(require_permission("classroom:manage")),
):
    count = await ClassroomAdminService().import_questions(classroom_id, _teacher_scope(admin), body)
    return success(data={"imported": count})


@router.get("/{classroom_id}/questions", response_model=APIResponse, summary="题目列表")
async def list_questions(
    classroom_id: int = Path(..., ge=1),
    status: str | None = Query(None),
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    admin=Depends(require_permission("classroom:manage")),
):
    result = await ClassroomAdminService().list_questions(
        classroom_id, _teacher_scope(admin), status, page, page_size)
    return success(data=PaginatedData(**result))


@router.post("/{classroom_id}/questions/publish", response_model=APIResponse, summary="发布题目（老师自审）")
async def publish_questions(
    body: dict,
    classroom_id: int = Path(..., ge=1),
    admin=Depends(require_permission("classroom:manage")),
):
    count = await ClassroomAdminService().publish_questions(
        classroom_id, _teacher_scope(admin), body.get("question_ids", []))
    return success(data={"published": count})


@router.delete("/{classroom_id}/questions/{question_id}", response_model=APIResponse, summary="删除题目")
async def delete_question(
    classroom_id: int = Path(..., ge=1), question_id: int = Path(..., ge=1),
    admin=Depends(require_permission("classroom:manage")),
):
    await ClassroomAdminService().delete_question(classroom_id, question_id, _teacher_scope(admin))
    return success(message="题目已删除")


# ── 测验 ──────────────────────────────────────────────────

@router.post("/{classroom_id}/quizzes", response_model=APIResponse, summary="发起限时测验")
async def create_quiz(
    body: ClassroomQuizCreate,
    classroom_id: int = Path(..., ge=1),
    admin=Depends(require_permission("classroom:manage")),
):
    return success(data=await ClassroomAdminService().create_quiz(
        classroom_id, _teacher_scope(admin), body))


@router.get("/{classroom_id}/quizzes", response_model=APIResponse, summary="测验列表")
async def list_quizzes(
    classroom_id: int = Path(..., ge=1),
    admin=Depends(require_permission("classroom:manage")),
):
    return success(data=await ClassroomAdminService().list_quizzes(classroom_id, _teacher_scope(admin)))


@router.post("/{classroom_id}/quizzes/{quiz_id}/end", response_model=APIResponse, summary="结束测验")
async def end_quiz(
    classroom_id: int = Path(..., ge=1), quiz_id: int = Path(..., ge=1),
    admin=Depends(require_permission("classroom:manage")),
):
    await ClassroomAdminService().end_quiz(classroom_id, quiz_id, _teacher_scope(admin))
    return success(message="测验已结束")


@router.get("/{classroom_id}/quizzes/{quiz_id}/progress", response_model=APIResponse, summary="实时进度（轮询）")
async def quiz_progress(
    classroom_id: int = Path(..., ge=1), quiz_id: int = Path(..., ge=1),
    admin=Depends(require_permission("classroom:manage")),
):
    return success(data=await ClassroomAdminService().quiz_progress(
        classroom_id, quiz_id, _teacher_scope(admin)))


@router.get("/{classroom_id}/quizzes/{quiz_id}/submissions", response_model=APIResponse, summary="批改列表（含题目答案）")
async def list_submissions(
    classroom_id: int = Path(..., ge=1), quiz_id: int = Path(..., ge=1),
    admin=Depends(require_permission("classroom:manage")),
):
    return success(data=await ClassroomAdminService().list_submissions(
        classroom_id, quiz_id, _teacher_scope(admin)))


@router.post("/{classroom_id}/quizzes/{quiz_id}/submissions/{submission_id}/review",
    response_model=APIResponse, summary="批改放行（简答给分/填空改判）")
async def review_submission(
    body: ClassroomSubmissionReview,
    classroom_id: int = Path(..., ge=1), quiz_id: int = Path(..., ge=1),
    submission_id: int = Path(..., ge=1),
    admin=Depends(require_permission("classroom:manage")),
):
    await ClassroomAdminService().review_submission(
        classroom_id, quiz_id, submission_id, _teacher_scope(admin), body)
    return success(message="批改已保存")
