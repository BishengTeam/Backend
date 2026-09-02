from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class Classroom(Base, TimestampMixin):
    """线下课堂。teacher_admin_id 是数据隔离键：老师只能访问自己的课堂。"""

    __tablename__ = "classroom"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'stopped')", name="ck_classroom_status"
        ),
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    teacher_admin_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("admin_user.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )
    join_code: Mapped[str | None] = mapped_column(String(8))
    join_code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ClassroomMember(Base, TimestampMixin):
    """课堂学生。加入时快照实名姓名供老师名单展示。"""

    __tablename__ = "classroom_member"
    __table_args__ = (
        UniqueConstraint("classroom_id", "user_id", name="uq_classroom_member"),
    )

    classroom_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("classroom.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=False, index=True
    )
    real_name_snapshot: Mapped[str] = mapped_column(String(64), nullable=False)


class ClassroomVideo(Base, TimestampMixin):
    """课堂视频。storage_key 使用 classroom/ 前缀，与课程模块物理隔离。"""

    __tablename__ = "classroom_video"

    classroom_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("classroom.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class ClassroomQuestion(Base, TimestampMixin):
    """随堂练习题。每个课堂独立题库，与其他课堂和线上题库完全隔离。"""

    __tablename__ = "classroom_question"
    __table_args__ = (
        CheckConstraint(
            "type IN ('single', 'multiple', 'judge', 'blank', 'short')",
            name="ck_classroom_question_type",
        ),
        CheckConstraint(
            "status IN ('draft', 'published')",
            name="ck_classroom_question_status",
        ),
    )

    classroom_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("classroom.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    stem: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list | None] = mapped_column(JSON)  # 客观题选项数组
    answer: Mapped[str | None] = mapped_column(Text)  # 客观题答案/判断题 true/false/填空标准答案
    analysis: Mapped[str | None] = mapped_column(Text)
    score: Mapped[int] = mapped_column(Integer, default=1, server_default="1")  # 每题分值
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft", server_default="draft"
    )


class ClassroomQuiz(Base, TimestampMixin):
    """限时测验会话。老师发起，学生限时作答。"""

    __tablename__ = "classroom_quiz"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ongoing', 'ended')", name="ck_classroom_quiz_status"
        ),
    )

    classroom_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("classroom.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    question_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ongoing", server_default="ongoing"
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ClassroomQuizSubmission(Base, TimestampMixin):
    """学生答卷。待老师批改放行后学生才能看到分数。"""

    __tablename__ = "classroom_quiz_submission"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_review', 'approved')",
            name="ck_classroom_submission_status",
        ),
        UniqueConstraint("quiz_id", "user_id", name="uq_classroom_submission"),
    )

    quiz_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("classroom_quiz.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=False, index=True
    )
    answers: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    auto_score: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    manual_score: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    manual_scores: Mapped[dict | None] = mapped_column(JSON)  # {question_id: 分数} 简答/改判
    total_score: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending_review", server_default="pending_review"
    )
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ClassroomQuizAttachment(Base, TimestampMixin):
    """问答题作答附件（Editor 内嵌图片与 Word/zip 文件共用）。

    uploaded = 已上传待交卷绑定；bound = 已随答卷绑定（含 submission_id）。
    object_key 归属路径自带 quiz/user/question，交卷时按归属白名单校验。
    """

    __tablename__ = "classroom_quiz_attachment"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('image', 'document', 'archive')",
            name="ck_classroom_attachment_kind",
        ),
        CheckConstraint(
            "status IN ('uploaded', 'bound')",
            name="ck_classroom_attachment_status",
        ),
        CheckConstraint("size_bytes > 0", name="ck_classroom_attachment_size"),
        UniqueConstraint("object_key", name="uq_classroom_attachment_key"),
        Index(
            "ix_classroom_attachment_draft",
            "quiz_id",
            "user_id",
            "question_id",
        ),
    )

    quiz_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("classroom_quiz.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=False
    )
    question_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("classroom_question.id", ondelete="CASCADE"), nullable=False
    )
    submission_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("classroom_quiz_submission.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="uploaded", server_default="uploaded"
    )
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    filename: Mapped[str] = mapped_column(String(256), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    bound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
