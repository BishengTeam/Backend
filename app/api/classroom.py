from fastapi import APIRouter, Depends, Path

from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.schemas.classroom import (
    ClassroomJoinRequest,
    ClassroomQuizSubmit,
)
from app.schemas.common import APIResponse, success
from app.services.classroom import ClassroomService

router = APIRouter(prefix="/classroom", tags=["课堂"])


@router.post("/join", response_model=APIResponse, summary="课堂码加入（需实名认证）")
async def join_classroom(
    body: ClassroomJoinRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse:
    result = await ClassroomService().join(current_user.id, body.code)
    return success(data=result)


@router.get("/my", response_model=APIResponse, summary="我的课堂列表（仅进行中）")
async def my_classrooms(
    current_user: User = Depends(get_current_user),
) -> APIResponse:
    return success(data=await ClassroomService().my_classrooms(current_user.id))


@router.get("/{classroom_id}", response_model=APIResponse, summary="课堂详情（视频+测验）")
async def classroom_detail(
    classroom_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse:
    return success(data=await ClassroomService().detail(current_user.id, classroom_id))


@router.get("/videos/{video_id}/play-url", response_model=APIResponse, summary="视频播放地址")
async def video_play_url(
    video_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse:
    url = await ClassroomService().video_play_url(current_user.id, video_id)
    return success(data={"url": url})


@router.get("/quizzes/{quiz_id}/paper", response_model=APIResponse, summary="答卷页（题目+剩余时间）")
async def quiz_paper(
    quiz_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse:
    return success(data=await ClassroomService().quiz_paper(current_user.id, quiz_id))


@router.post("/quizzes/{quiz_id}/submit", response_model=APIResponse, summary="交卷（进入待批改）")
async def submit_quiz(
    body: ClassroomQuizSubmit,
    quiz_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse:
    await ClassroomService().submit_quiz(current_user.id, quiz_id, body.answers)
    return success(message="答卷已提交，等待老师批改")


@router.get("/quizzes/{quiz_id}/result", response_model=APIResponse, summary="成绩（审批后可见）")
async def quiz_result(
    quiz_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse:
    return success(data=await ClassroomService().quiz_result(current_user.id, quiz_id))
