from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

QuestionType = Literal["single", "multiple", "judge", "blank", "short"]


# ── 管理端（老师） ──────────────────────────────────────────

class ClassroomCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, description="课堂名称")


class ClassroomUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)


class ClassroomListItem(BaseModel):
    id: int
    name: str
    status: str
    join_code: str | None = None
    join_code_expires_at: datetime | None = None
    student_count: int = 0
    video_count: int = 0
    question_count: int = 0
    ongoing_quiz: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class ClassroomStudentItem(BaseModel):
    id: int
    user_id: int
    real_name: str
    joined_at: datetime


class ClassroomVideoCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=128)
    storage_key: str = Field(..., min_length=1, max_length=512)
    duration_seconds: int = Field(0, ge=0)
    size_bytes: int = Field(0, ge=0)


class ClassroomVideoUploadUrlRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=256)
    content_type: str = Field(..., min_length=1, max_length=128)
    size_bytes: int = Field(..., gt=0)


class ClassroomVideoItem(BaseModel):
    id: int
    title: str
    duration_seconds: int
    size_bytes: int
    sort_order: int
    created_at: datetime


class ClassroomQuestionInput(BaseModel):
    type: QuestionType
    stem: str = Field(..., min_length=1)
    options: list[str] | None = None
    answer: str | None = None
    analysis: str | None = None
    score: int = Field(1, ge=1, le=100)


class ClassroomQuestionImport(BaseModel):
    questions: list[ClassroomQuestionInput] = Field(..., min_length=1, max_length=500)


class ClassroomQuestionItem(ClassroomQuestionInput):
    id: int
    status: str
    created_at: datetime


class ClassroomQuizCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=128)
    duration_minutes: int = Field(..., ge=1, le=180)
    question_ids: list[int] = Field(..., min_length=1, max_length=200)


class ClassroomQuizItem(BaseModel):
    id: int
    title: str
    duration_minutes: int
    question_count: int = 0
    status: str
    started_at: datetime
    ended_at: datetime | None = None
    submitted_count: int = 0
    student_count: int = 0


class ClassroomSubmissionItem(BaseModel):
    id: int
    user_id: int
    student_name: str = ""
    answers: dict = Field(default_factory=dict)
    auto_score: int
    manual_score: int
    total_score: int
    status: str
    submitted_at: datetime


class ClassroomSubmissionReview(BaseModel):
    """批改：简答给分 / 填空改判（question_id → 分数），并放行"""

    manual_scores: dict[str, int] = Field(default_factory=dict)
    approve: bool = True


class ClassroomAttachmentUploadUrlRequest(BaseModel):
    question_id: int = Field(..., ge=1)
    filename: str = Field(..., min_length=1, max_length=256)
    content_type: str = Field(..., min_length=1, max_length=128)
    size_bytes: int = Field(..., gt=0, le=50 * 1024 * 1024)


class ClassroomAttachmentUploadResponse(BaseModel):
    attachment_id: int
    object_key: str
    upload_url: str
    expires_at: datetime


class ClassroomAttachmentItem(BaseModel):
    id: int
    question_id: int
    kind: str
    filename: str
    content_type: str
    size_bytes: int
    url: str


# ── 用户端（小程序） ──────────────────────────────────────────

class ClassroomJoinRequest(BaseModel):
    code: str = Field(..., min_length=4, max_length=8, description="课堂码")


class ClassroomMyItem(BaseModel):
    id: int
    name: str
    status: str
    video_count: int = 0
    ongoing_quiz_id: int | None = None
    joined_at: datetime


class ClassroomQuizBrief(BaseModel):
    id: int
    title: str
    duration_minutes: int
    status: str
    started_at: datetime
    ends_at: datetime
    submitted: bool = False


class ClassroomDetail(BaseModel):
    id: int
    name: str
    status: str
    teacher_name: str = ""
    videos: list[ClassroomVideoItem] = Field(default_factory=list)
    quizzes: list[ClassroomQuizBrief] = Field(default_factory=list)


class ClassroomQuizPaper(BaseModel):
    """学生答卷页：题目 + 剩余时间；不含答案"""

    id: int
    title: str
    duration_minutes: int
    ends_at: datetime
    questions: list[dict] = Field(default_factory=list)


class ClassroomQuizSubmit(BaseModel):
    answers: dict[str, str] = Field(..., description="{question_id: 答案；short 为富文本 HTML}")
    attachments: dict[str, list[int]] = Field(
        default_factory=dict, description="{question_id: [attachment_id]}"
    )


class ClassroomQuizResult(BaseModel):
    status: str
    total_score: int | None = None
    submitted_at: datetime | None = None


class ClassroomSubmissionDetailQuestion(BaseModel):
    id: int
    type: str
    stem: str
    options: list | None = None
    score: int


class ClassroomSubmissionDetail(BaseModel):
    """交卷详情回看：仅老师批改完成（approved）后回发作答内容与附件。"""

    status: str
    total_score: int | None = None
    submitted_at: datetime | None = None
    approved_at: datetime | None = None
    questions: list[ClassroomSubmissionDetailQuestion] = Field(default_factory=list)
    answers: dict = Field(default_factory=dict)
    attachments: list[ClassroomAttachmentItem] = Field(default_factory=list)
