"""课堂用户端服务：学生加入、查看、限时作答。"""

from datetime import timedelta

from sqlalchemy import case, select

from app.adapter.database import get_db_ctx
from app.domain.classroom.src.index import (
    Classroom,
    ClassroomMember,
    ClassroomQuestion,
    ClassroomQuiz,
    ClassroomQuizSubmission,
    ClassroomQuizAttachment,
    ClassroomVideo,
)
from app.domain.user.src.index import UserRealname
from app.port.exceptions import (
    BusinessException,
    ForbiddenException,
    NotFoundException,
    ValidationException,
)
from app.services.classroom_admin import _grade_answer, _now
from app.services.classroom_attachment import (
    SHORT_ANSWER_HTML_MAX_BYTES,
    ClassroomAttachmentService,
    canonicalize_short_answer_html,
    make_read_signer,
    resign_short_answer_html,
    sanitize_short_answer_html,
)


class ClassroomService:

    async def join(self, user_id: int, code: str) -> dict:
        """课堂码加入：码有效 + 课堂 active + 用户已实名 verified。"""
        async with get_db_ctx() as db:
            realname = (await db.execute(
                select(UserRealname).where(UserRealname.user_id == user_id)
            )).scalar_one_or_none()
            if realname is None or realname.status != "verified":
                raise BusinessException("请先在小程序「我的-编辑资料」完成实名认证后再加入课堂")

            classroom = (await db.execute(
                select(Classroom).where(
                    Classroom.join_code == code.strip(),
                    Classroom.status == "active",
                )
            )).scalar_one_or_none()
            if classroom is None:
                raise BusinessException("课堂码无效或课堂已停课")
            if (
                classroom.join_code_expires_at is None
                or classroom.join_code_expires_at <= _now()
            ):
                raise BusinessException("课堂码已过期，请联系老师刷新")

            existing = (await db.execute(
                select(ClassroomMember).where(
                    ClassroomMember.classroom_id == classroom.id,
                    ClassroomMember.user_id == user_id,
                )
            )).scalar_one_or_none()
            if existing is None:
                db.add(ClassroomMember(
                    classroom_id=classroom.id,
                    user_id=user_id,
                    real_name_snapshot=realname.real_name,
                ))
                await db.commit()
            return {"classroom_id": classroom.id, "name": classroom.name}

    async def my_classrooms(self, user_id: int) -> list[dict]:
        """我的课堂列表：active 在前、stopped 在后；停课课堂只读可见。"""
        async with get_db_ctx() as db:
            rows = (await db.execute(
                select(Classroom, ClassroomMember)
                .join(ClassroomMember, ClassroomMember.classroom_id == Classroom.id)
                .where(
                    ClassroomMember.user_id == user_id,
                ).order_by(
                    case((Classroom.status == "active", 0), else_=1),
                    ClassroomMember.id.desc(),
                )
            )).all()
            result = []
            for classroom, member in rows:
                video_count = len([v for v in (await db.execute(
                    select(ClassroomVideo.id).where(
                        ClassroomVideo.classroom_id == classroom.id)
                )).scalars().all()])
                ongoing = (await db.execute(
                    select(ClassroomQuiz).where(
                        ClassroomQuiz.classroom_id == classroom.id,
                        ClassroomQuiz.status == "ongoing",
                    )
                )).scalar_one_or_none()
                result.append({
                    "id": classroom.id, "name": classroom.name, "status": classroom.status,
                    "video_count": video_count,
                    "ongoing_quiz_id": ongoing.id if ongoing else None,
                    "joined_at": member.created_at,
                })
            return result

    async def detail(self, user_id: int, classroom_id: int) -> dict:
        async with get_db_ctx() as db:
            classroom, member = await self._member_classroom(
                db, user_id, classroom_id, allow_stopped=True
            )
            videos = (await db.execute(
                select(ClassroomVideo).where(ClassroomVideo.classroom_id == classroom_id)
                .order_by(ClassroomVideo.sort_order, ClassroomVideo.id)
            )).scalars().all()
            quizzes = (await db.execute(
                select(ClassroomQuiz).where(ClassroomQuiz.classroom_id == classroom_id)
                .order_by(ClassroomQuiz.id.desc())
            )).scalars().all()
            quiz_items = []
            for q in quizzes:
                ends_at = q.started_at + timedelta(minutes=q.duration_minutes)
                if q.status == "ongoing" and ends_at <= _now():
                    q.status = "ended"
                    q.ended_at = ends_at
                    await db.commit()
                submission_status = (await db.execute(
                    select(ClassroomQuizSubmission.status).where(
                        ClassroomQuizSubmission.quiz_id == q.id,
                        ClassroomQuizSubmission.user_id == user_id,
                    )
                )).scalar_one_or_none()
                submitted = submission_status is not None
                quiz_items.append({
                    "id": q.id, "title": q.title, "duration_minutes": q.duration_minutes,
                    "status": q.status, "started_at": q.started_at, "ends_at": ends_at,
                    "submitted": submitted,
                    "submission_status": submission_status,
                })
            return {
                "id": classroom.id, "name": classroom.name, "status": classroom.status,
                "videos": [
                    {"id": v.id, "title": v.title, "duration_seconds": v.duration_seconds,
                     "size_bytes": v.size_bytes, "sort_order": v.sort_order,
                     "created_at": v.created_at}
                    for v in videos
                ],
                "quizzes": quiz_items,
            }

    async def video_play_url(self, user_id: int, video_id: int) -> str:
        from app.services.course_storage import CourseStorage

        async with get_db_ctx() as db:
            video = await db.get(ClassroomVideo, video_id)
            if video is None:
                raise NotFoundException("课堂视频")
            await self._member_classroom(
                db, user_id, video.classroom_id, allow_stopped=True
            )
            return await CourseStorage.signed_url(video.storage_key)

    async def quiz_paper(self, user_id: int, quiz_id: int) -> dict:
        """答卷页：题目（不含答案）+ 截止时间。"""
        async with get_db_ctx() as db:
            quiz = await self._member_quiz(db, user_id, quiz_id)
            ends_at = quiz.started_at + timedelta(minutes=quiz.duration_minutes)
            if quiz.status == "ongoing" and ends_at <= _now():
                quiz.status = "ended"
                quiz.ended_at = ends_at
                await db.commit()
            existing = (await db.execute(
                select(ClassroomQuizSubmission).where(
                    ClassroomQuizSubmission.quiz_id == quiz_id,
                    ClassroomQuizSubmission.user_id == user_id,
                )
            )).scalar_one_or_none()
            if existing is not None:
                raise BusinessException("已提交过答卷")
            if quiz.status == "ended" or ends_at <= _now():
                raise BusinessException("测验已结束")
            questions = (await db.execute(
                select(ClassroomQuestion).where(
                    ClassroomQuestion.id.in_(quiz.question_ids or [])
                )
            )).scalars().all()
            qmap = {q.id: q for q in questions}
            ordered = [qmap[qid] for qid in (quiz.question_ids or []) if qid in qmap]
            return {
                "id": quiz.id, "title": quiz.title,
                "duration_minutes": quiz.duration_minutes, "ends_at": ends_at,
                "questions": [
                    {"id": q.id, "type": q.type, "stem": q.stem,
                     "options": q.options, "score": q.score}
                    for q in ordered
                ],
            }

    async def submit_quiz(
        self,
        user_id: int,
        quiz_id: int,
        answers: dict,
        attachments: dict | None = None,
    ) -> None:
        async with get_db_ctx() as db:
            quiz = await self._member_quiz(db, user_id, quiz_id)
            ends_at = quiz.started_at + timedelta(minutes=quiz.duration_minutes)
            if ends_at <= _now() or quiz.status == "ended":
                raise BusinessException("测验已结束，无法提交")
            existing = (await db.execute(
                select(ClassroomQuizSubmission).where(
                    ClassroomQuizSubmission.quiz_id == quiz_id,
                    ClassroomQuizSubmission.user_id == user_id,
                )
            )).scalar_one_or_none()
            if existing is not None:
                raise BusinessException("已提交过答卷")
            questions = (await db.execute(
                select(ClassroomQuestion).where(
                    ClassroomQuestion.id.in_(quiz.question_ids or [])
                )
            )).scalars().all()
            ordered = [
                q for q in questions if q.id in (quiz.question_ids or [])
            ]
            short_map = {q.id: q for q in ordered if q.type == "short"}
            draft_rows = (await db.execute(
                select(ClassroomQuizAttachment).where(
                    ClassroomQuizAttachment.quiz_id == quiz_id,
                    ClassroomQuizAttachment.user_id == user_id,
                    ClassroomQuizAttachment.status == "uploaded",
                )
            )).scalars().all()
            allowed_keys: dict[int, set[str]] = {}
            for row in draft_rows:
                allowed_keys.setdefault(row.question_id, set()).add(row.object_key)
            processed: dict[str, str] = {}
            for key, value in answers.items():
                question_id = int(key) if str(key).isdigit() else None
                if question_id in short_map:
                    sanitized = sanitize_short_answer_html(str(value))
                    canonical = canonicalize_short_answer_html(
                        sanitized, allowed_keys.get(question_id, set())
                    )
                    if len(canonical.encode("utf-8")) > SHORT_ANSWER_HTML_MAX_BYTES:
                        raise ValidationException("问答题答案内容过大")
                    processed[key] = canonical
                else:
                    processed[key] = str(value)
            auto = sum(
                _grade_answer(q, processed.get(str(q.id)))
                for q in ordered
            )
            submission = ClassroomQuizSubmission(
                quiz_id=quiz_id, user_id=user_id,
                answers=processed,
                auto_score=auto, total_score=auto,
                status="pending_review", submitted_at=_now(),
            )
            db.add(submission)
            await db.flush()
            canonical_answers = {
                question_id: processed[str(question_id)]
                for question_id in short_map
                if processed.get(str(question_id))
            }
            await ClassroomAttachmentService.bind_submitted_attachments(
                db,
                user_id=user_id,
                quiz=quiz,
                submission_id=submission.id,
                requested=attachments or {},
                canonical_answers=canonical_answers,
                short_questions=short_map,
            )
            await db.commit()

    async def quiz_result(self, user_id: int, quiz_id: int) -> dict:
        """审批通过后才返回分数。"""
        async with get_db_ctx() as db:
            await self._member_quiz(db, user_id, quiz_id, allow_stopped=True)
            sub = (await db.execute(
                select(ClassroomQuizSubmission).where(
                    ClassroomQuizSubmission.quiz_id == quiz_id,
                    ClassroomQuizSubmission.user_id == user_id,
                )
            )).scalar_one_or_none()
            if sub is None:
                return {"status": "not_submitted", "total_score": None, "submitted_at": None}
            return {
                "status": sub.status,
                "total_score": sub.total_score if sub.status == "approved" else None,
                "submitted_at": sub.submitted_at,
            }

    async def quiz_submission_detail(self, user_id: int, quiz_id: int) -> dict:
        """提交详情回看：批改完成前只回状态，不回发作答内容（防错峰泄题）。"""
        async with get_db_ctx() as db:
            quiz = await self._member_quiz(db, user_id, quiz_id, allow_stopped=True)
            sub = (await db.execute(
                select(ClassroomQuizSubmission).where(
                    ClassroomQuizSubmission.quiz_id == quiz_id,
                    ClassroomQuizSubmission.user_id == user_id,
                )
            )).scalar_one_or_none()
            if sub is None:
                raise NotFoundException("答卷")
            if sub.status != "approved":
                return {"status": sub.status}
            questions = (await db.execute(
                select(ClassroomQuestion).where(
                    ClassroomQuestion.id.in_(quiz.question_ids or [])
                )
            )).scalars().all()
            qmap = {q.id: q for q in questions}
            ordered = [qmap[qid] for qid in (quiz.question_ids or []) if qid in qmap]
            short_ids = {q.id for q in ordered if q.type == "short"}
            rows = (await db.execute(
                select(ClassroomQuizAttachment).where(
                    ClassroomQuizAttachment.submission_id == sub.id,
                    ClassroomQuizAttachment.status == "bound",
                ).order_by(ClassroomQuizAttachment.id)
            )).scalars().all()
            urls = await ClassroomAttachmentService.sign_read_urls(
                [row.object_key for row in rows]
            )
            signer = make_read_signer()
            answers = {}
            for key, value in (sub.answers or {}).items():
                question_id = int(key) if str(key).isdigit() else None
                if question_id in short_ids and value:
                    answers[key] = resign_short_answer_html(str(value), signer)
                else:
                    answers[key] = value
            return {
                "status": sub.status,
                "total_score": sub.total_score,
                "submitted_at": sub.submitted_at,
                "approved_at": sub.approved_at,
                "questions": [
                    {"id": q.id, "type": q.type, "stem": q.stem,
                     "options": q.options, "score": q.score}
                    for q in ordered
                ],
                "answers": answers,
                "attachments": [
                    {
                        "id": row.id, "question_id": row.question_id, "kind": row.kind,
                        "filename": row.filename, "content_type": row.content_type,
                        "size_bytes": row.size_bytes,
                        "url": urls.get(row.object_key, ""),
                    }
                    for row in rows
                ],
            }

    # ── helpers ─────────────────────────────────────────────

    @staticmethod
    async def _member_classroom(
        db, user_id: int, classroom_id: int, *, allow_stopped: bool = False
    ):
        classroom = await db.get(Classroom, classroom_id)
        allowed = {"active", "stopped"} if allow_stopped else {"active"}
        if classroom is None or classroom.status not in allowed:
            raise NotFoundException("课堂不存在或已停课")
        member = (await db.execute(
            select(ClassroomMember).where(
                ClassroomMember.classroom_id == classroom_id,
                ClassroomMember.user_id == user_id,
            )
        )).scalar_one_or_none()
        if member is None:
            raise ForbiddenException("未加入该课堂")
        return classroom, member

    async def _member_quiz(
        self, db, user_id: int, quiz_id: int, *, allow_stopped: bool = False
    ) -> ClassroomQuiz:
        quiz = await db.get(ClassroomQuiz, quiz_id)
        if quiz is None:
            raise NotFoundException("测验")
        await self._member_classroom(
            db, user_id, quiz.classroom_id, allow_stopped=allow_stopped
        )
        return quiz
