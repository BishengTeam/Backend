"""课堂管理端服务。老师数据隔离：teacher 只能操作自己创建的课堂。"""

import random
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.domain.classroom.src.index import (
    Classroom,
    ClassroomMember,
    ClassroomQuestion,
    ClassroomQuiz,
    ClassroomQuizAttachment,
    ClassroomQuizSubmission,
    ClassroomVideo,
)
from app.domain.user.src.index import AdminUser, User, UserProfile
from app.port.exceptions import (
    BusinessException,
    ForbiddenException,
    NotFoundException,
    ValidationException,
)
from app.schemas.classroom import (
    ClassroomCreate,
    ClassroomQuestionImport,
    ClassroomQuestionInput,
    ClassroomQuizCreate,
    ClassroomSubmissionReview,
    ClassroomUpdate,
    ClassroomVideoCreate,
)
from app.services.course_storage import CourseStorage, validate_upload
from app.services.classroom_attachment import (
    ClassroomAttachmentService,
    make_read_signer,
    resign_short_answer_html,
)

JOIN_CODE_TTL_MINUTES = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _get_classroom(db, classroom_id: int, *, teacher_admin_id: int | None) -> Classroom:
    classroom = await db.get(Classroom, classroom_id)
    if classroom is None:
        raise NotFoundException("课堂")
    if teacher_admin_id is not None and classroom.teacher_admin_id != teacher_admin_id:
        raise ForbiddenException("无权访问他人课堂")
    return classroom


def _norm_answer(value: str | None) -> str:
    return (value or "").strip()


def _grade_answer(question: ClassroomQuestion, student_answer: str) -> int:
    """客观题自动判分：单选/判断精确比对；多选逗号排序比对；填空去空白严格比对。"""
    if question.type in ("single", "judge"):
        return question.score if _norm_answer(student_answer) == _norm_answer(question.answer) else 0
    if question.type == "multiple":
        def norm_set(v: str | None) -> list[str]:
            return sorted(x.strip() for x in (v or "").split(",") if x.strip())
        return question.score if norm_set(student_answer) == norm_set(question.answer) else 0
    if question.type == "blank":
        return question.score if _norm_answer(student_answer) == _norm_answer(question.answer) else 0
    return 0  # short 由老师批改


class ClassroomAdminService:

    # ── 课堂 CRUD / 码 ──────────────────────────────────────

    async def list_classrooms(
        self, teacher_admin_id: int | None, page: int, page_size: int
    ) -> dict:
        async with get_db_ctx() as db:
            base = select(Classroom)
            if teacher_admin_id is not None:
                base = base.where(Classroom.teacher_admin_id == teacher_admin_id)
            total = (await db.execute(
                select(func.count()).select_from(base.subquery())
            )).scalar() or 0
            rows = (await db.execute(
                base.order_by(Classroom.id.desc())
                .offset((page - 1) * page_size).limit(page_size)
            )).scalars().all()
            teacher_ids = {c.teacher_admin_id for c in rows}
            teachers = {}
            if teacher_ids:
                for t in (await db.execute(
                    select(AdminUser).where(AdminUser.id.in_(teacher_ids))
                )).scalars().all():
                    teachers[t.id] = t.display_name or t.username
            items = []
            for c in rows:
                student_count = (await db.execute(
                    select(func.count()).select_from(ClassroomMember)
                    .where(ClassroomMember.classroom_id == c.id)
                )).scalar() or 0
                video_count = (await db.execute(
                    select(func.count()).select_from(ClassroomVideo)
                    .where(ClassroomVideo.classroom_id == c.id)
                )).scalar() or 0
                question_count = (await db.execute(
                    select(func.count()).select_from(ClassroomQuestion)
                    .where(ClassroomQuestion.classroom_id == c.id)
                )).scalar() or 0
                ongoing = (await db.execute(
                    select(ClassroomQuiz.id).where(
                        ClassroomQuiz.classroom_id == c.id,
                        ClassroomQuiz.status == "ongoing",
                    ).limit(1)
                )).scalar()
                items.append({
                    "id": c.id, "name": c.name, "status": c.status,
                    "teacher_name": teachers.get(c.teacher_admin_id, str(c.teacher_admin_id)),
                    "join_code": c.join_code if c.status == "active" else None,
                    "join_code_expires_at": c.join_code_expires_at,
                    "student_count": int(student_count),
                    "video_count": int(video_count),
                    "question_count": int(question_count),
                    "ongoing_quiz": ongoing is not None,
                    "created_at": c.created_at,
                })
            return {"items": items, "total": total, "page": page, "page_size": page_size}

    async def create(self, teacher_admin_id: int, data: ClassroomCreate) -> dict:
        async with get_db_ctx() as db:
            async with db.begin():
                classroom = Classroom(name=data.name, teacher_admin_id=teacher_admin_id)
                db.add(classroom)
                await db.flush()
            return await self._detail(db, classroom.id, teacher_admin_id)

    async def update(self, classroom_id: int, teacher_admin_id: int | None, data: ClassroomUpdate) -> None:
        async with get_db_ctx() as db:
            classroom = await _get_classroom(db, classroom_id, teacher_admin_id=teacher_admin_id)
            if data.name is not None:
                classroom.name = data.name
            await db.commit()

    async def stop(self, classroom_id: int, teacher_admin_id: int | None) -> None:
        """停课：课堂冻结为只读，进行中的测验立即结束。"""
        async with get_db_ctx() as db:
            classroom = await _get_classroom(db, classroom_id, teacher_admin_id=teacher_admin_id)
            classroom.status = "stopped"
            now = _now()
            classroom.stopped_at = now
            classroom.join_code = None
            classroom.join_code_expires_at = None
            ongoing = (await db.execute(
                select(ClassroomQuiz).where(
                    ClassroomQuiz.classroom_id == classroom_id,
                    ClassroomQuiz.status == "ongoing",
                )
            )).scalars().all()
            for quiz in ongoing:
                quiz.status = "ended"
                quiz.ended_at = now
            await db.commit()

    async def refresh_join_code(self, classroom_id: int, teacher_admin_id: int | None) -> dict:
        """手动刷新课堂码：6 位数字，30 分钟有效。"""
        async with get_db_ctx() as db:
            classroom = await _get_classroom(db, classroom_id, teacher_admin_id=teacher_admin_id)
            if classroom.status != "active":
                raise BusinessException("课堂已停课，无法生成课堂码")
            code = f"{random.randint(0, 999999):06d}"
            expires = _now() + timedelta(minutes=JOIN_CODE_TTL_MINUTES)
            classroom.join_code = code
            classroom.join_code_expires_at = expires
            await db.commit()
            return {"join_code": code, "join_code_expires_at": expires}

    async def _detail(self, db, classroom_id: int, teacher_admin_id: int | None) -> dict:
        classroom = await _get_classroom(db, classroom_id, teacher_admin_id=teacher_admin_id)
        return {
            "id": classroom.id, "name": classroom.name, "status": classroom.status,
            "join_code": classroom.join_code, "join_code_expires_at": classroom.join_code_expires_at,
            "created_at": classroom.created_at,
        }

    async def get(self, classroom_id: int, teacher_admin_id: int | None) -> dict:
        async with get_db_ctx() as db:
            return await self._detail(db, classroom_id, teacher_admin_id)

    # ── 学生 ────────────────────────────────────────────────

    async def list_students(self, classroom_id: int, teacher_admin_id: int | None) -> list[dict]:
        async with get_db_ctx() as db:
            await _get_classroom(db, classroom_id, teacher_admin_id=teacher_admin_id)
            rows = (await db.execute(
                select(ClassroomMember)
                .where(ClassroomMember.classroom_id == classroom_id)
                .order_by(ClassroomMember.id.desc())
            )).scalars().all()
            return [
                {"id": m.id, "user_id": m.user_id, "real_name": m.real_name_snapshot,
                 "joined_at": m.created_at}
                for m in rows
            ]

    async def remove_student(self, classroom_id: int, user_id: int, teacher_admin_id: int | None) -> None:
        async with get_db_ctx() as db:
            await _get_classroom(db, classroom_id, teacher_admin_id=teacher_admin_id)
            member = (await db.execute(
                select(ClassroomMember).where(
                    ClassroomMember.classroom_id == classroom_id,
                    ClassroomMember.user_id == user_id,
                )
            )).scalar_one_or_none()
            if member is None:
                raise NotFoundException("课堂学生")
            await db.delete(member)
            await db.commit()

    # ── 视频 ────────────────────────────────────────────────

    async def video_upload_url(
        self, classroom_id: int, teacher_admin_id: int | None, filename: str,
        content_type: str, size_bytes: int,
    ) -> dict:
        async with get_db_ctx() as db:
            await _get_classroom(db, classroom_id, teacher_admin_id=teacher_admin_id)
        extension = validate_upload(
            kind="chapter_video", filename=filename,
            content_type=content_type, size_bytes=size_bytes,
        )
        installation = await CourseStorage.installation_id()
        object_key = f"classroom/{installation}/videos/{classroom_id}/{uuid.uuid4().hex}{extension}"
        url = await CourseStorage.put_url(object_key)
        return {"storage_key": object_key, "upload_url": url}

    async def create_video(
        self, classroom_id: int, teacher_admin_id: int | None, data: ClassroomVideoCreate
    ) -> dict:
        async with get_db_ctx() as db:
            await _get_classroom(db, classroom_id, teacher_admin_id=teacher_admin_id)
            if not await CourseStorage.object_exists(data.storage_key):
                raise ValidationException("视频尚未上传完成，请先上传")
            count = (await db.execute(
                select(func.count()).select_from(ClassroomVideo)
                .where(ClassroomVideo.classroom_id == classroom_id)
            )).scalar() or 0
            video = ClassroomVideo(
                classroom_id=classroom_id, title=data.title,
                storage_key=data.storage_key,
                duration_seconds=data.duration_seconds,
                size_bytes=data.size_bytes, sort_order=int(count),
            )
            db.add(video)
            await db.commit()
            await db.refresh(video)
            return {"id": video.id, "title": video.title,
                    "duration_seconds": video.duration_seconds,
                    "size_bytes": video.size_bytes, "sort_order": video.sort_order,
                    "created_at": video.created_at}

    async def list_videos(self, classroom_id: int, teacher_admin_id: int | None) -> list[dict]:
        async with get_db_ctx() as db:
            await _get_classroom(db, classroom_id, teacher_admin_id=teacher_admin_id)
            rows = (await db.execute(
                select(ClassroomVideo).where(ClassroomVideo.classroom_id == classroom_id)
                .order_by(ClassroomVideo.sort_order, ClassroomVideo.id)
            )).scalars().all()
            return [
                {"id": v.id, "title": v.title, "duration_seconds": v.duration_seconds,
                 "size_bytes": v.size_bytes, "sort_order": v.sort_order,
                 "created_at": v.created_at}
                for v in rows
            ]

    async def video_play_url(
        self, classroom_id: int, video_id: int, teacher_admin_id: int | None
    ) -> str:
        """管理端视频预览：签名播放地址。"""
        from app.services.course_storage import CourseStorage

        async with get_db_ctx() as db:
            await _get_classroom(db, classroom_id, teacher_admin_id=teacher_admin_id)
            video = await db.get(ClassroomVideo, video_id)
            if video is None or video.classroom_id != classroom_id:
                raise NotFoundException("课堂视频")
            return await CourseStorage.signed_url(video.storage_key)

    async def delete_video(self, classroom_id: int, video_id: int, teacher_admin_id: int | None) -> None:
        async with get_db_ctx() as db:
            await _get_classroom(db, classroom_id, teacher_admin_id=teacher_admin_id)
            video = await db.get(ClassroomVideo, video_id)
            if video is None or video.classroom_id != classroom_id:
                raise NotFoundException("课堂视频")
            await db.delete(video)
            await db.commit()

    # ── 题库 ────────────────────────────────────────────────

    async def import_questions(
        self, classroom_id: int, teacher_admin_id: int | None, data: ClassroomQuestionImport
    ) -> int:
        async with get_db_ctx() as db:
            await _get_classroom(db, classroom_id, teacher_admin_id=teacher_admin_id)
            for q in data.questions:
                self._validate_question(q)
                db.add(ClassroomQuestion(
                    classroom_id=classroom_id, type=q.type, stem=q.stem,
                    options=q.options, answer=q.answer, analysis=q.analysis,
                    score=q.score, status="draft",
                ))
            await db.commit()
            return len(data.questions)

    @staticmethod
    def _validate_question(q: ClassroomQuestionInput) -> None:
        if q.type in ("single", "multiple"):
            if not q.options or len(q.options) < 2:
                raise ValidationException(f"客观题 [{q.stem[:20]}] 至少需要 2 个选项")
            if q.answer is None or not q.answer.strip():
                raise ValidationException(f"客观题 [{q.stem[:20]}] 缺少答案")
            if q.type == "single":
                try:
                    idx = int(q.answer.strip())
                    if not 0 <= idx < len(q.options):
                        raise ValueError
                except ValueError:
                    raise ValidationException(f"单选题 [{q.stem[:20]}] 答案必须是选项序号")
            else:
                for part in q.answer.split(","):
                    try:
                        idx = int(part.strip())
                        if not 0 <= idx < len(q.options):
                            raise ValueError
                    except ValueError:
                        raise ValidationException(f"多选题 [{q.stem[:20]}] 答案必须是逗号分隔的选项序号")
        elif q.type == "judge":
            if (q.answer or "").strip() not in ("true", "false"):
                raise ValidationException(f"判断题 [{q.stem[:20]}] 答案必须是 true/false")
        elif q.type == "blank":
            if not (q.answer or "").strip():
                raise ValidationException(f"填空题 [{q.stem[:20]}] 缺少标准答案")

    async def list_questions(
        self, classroom_id: int, teacher_admin_id: int | None, status: str | None,
        page: int, page_size: int,
    ) -> dict:
        async with get_db_ctx() as db:
            await _get_classroom(db, classroom_id, teacher_admin_id=teacher_admin_id)
            base = select(ClassroomQuestion).where(
                ClassroomQuestion.classroom_id == classroom_id
            )
            if status:
                base = base.where(ClassroomQuestion.status == status)
            total = (await db.execute(
                select(func.count()).select_from(base.subquery())
            )).scalar() or 0
            rows = (await db.execute(
                base.order_by(ClassroomQuestion.id.desc())
                .offset((page - 1) * page_size).limit(page_size)
            )).scalars().all()
            return {
                "items": [
                    {"id": q.id, "type": q.type, "stem": q.stem, "options": q.options,
                     "answer": q.answer, "analysis": q.analysis, "score": q.score,
                     "status": q.status, "created_at": q.created_at}
                    for q in rows
                ],
                "total": total, "page": page, "page_size": page_size,
            }

    async def publish_questions(
        self, classroom_id: int, teacher_admin_id: int | None, question_ids: list[int]
    ) -> int:
        """老师自审发布。"""
        async with get_db_ctx() as db:
            await _get_classroom(db, classroom_id, teacher_admin_id=teacher_admin_id)
            rows = (await db.execute(
                select(ClassroomQuestion).where(
                    ClassroomQuestion.classroom_id == classroom_id,
                    ClassroomQuestion.id.in_(question_ids),
                )
            )).scalars().all()
            for q in rows:
                q.status = "published"
            await db.commit()
            return len(rows)

    async def delete_question(
        self, classroom_id: int, question_id: int, teacher_admin_id: int | None
    ) -> None:
        async with get_db_ctx() as db:
            await _get_classroom(db, classroom_id, teacher_admin_id=teacher_admin_id)
            q = await db.get(ClassroomQuestion, question_id)
            if q is None or q.classroom_id != classroom_id:
                raise NotFoundException("题目")
            await db.delete(q)
            await db.commit()

    # ── 测验 ────────────────────────────────────────────────

    async def create_quiz(
        self, classroom_id: int, teacher_admin_id: int | None, data: ClassroomQuizCreate
    ) -> dict:
        async with get_db_ctx() as db:
            classroom = await _get_classroom(db, classroom_id, teacher_admin_id=teacher_admin_id)
            if classroom.status != "active":
                raise BusinessException("课堂已停课，无法发起测验")
            existing = (await db.execute(
                select(ClassroomQuiz.id).where(
                    ClassroomQuiz.classroom_id == classroom_id,
                    ClassroomQuiz.status == "ongoing",
                ).limit(1)
            )).scalar()
            if existing:
                raise BusinessException("已有进行中的测验，请先结束")
            questions = (await db.execute(
                select(ClassroomQuestion).where(
                    ClassroomQuestion.classroom_id == classroom_id,
                    ClassroomQuestion.id.in_(data.question_ids),
                    ClassroomQuestion.status == "published",
                )
            )).scalars().all()
            if len(questions) != len(set(data.question_ids)):
                raise ValidationException("包含未发布或不存在的题目")
            ordered = sorted(set(data.question_ids), key=data.question_ids.index)
            quiz = ClassroomQuiz(
                classroom_id=classroom_id, title=data.title,
                duration_minutes=data.duration_minutes,
                question_ids=ordered, status="ongoing", started_at=_now(),
            )
            db.add(quiz)
            await db.commit()
            await db.refresh(quiz)
            return {"id": quiz.id, "title": quiz.title, "started_at": quiz.started_at}

    async def end_quiz(self, classroom_id: int, quiz_id: int, teacher_admin_id: int | None) -> None:
        async with get_db_ctx() as db:
            await _get_classroom(db, classroom_id, teacher_admin_id=teacher_admin_id)
            quiz = await db.get(ClassroomQuiz, quiz_id)
            if quiz is None or quiz.classroom_id != classroom_id:
                raise NotFoundException("测验")
            if quiz.status == "ongoing":
                quiz.status = "ended"
                quiz.ended_at = _now()
                await db.commit()

    async def list_quizzes(self, classroom_id: int, teacher_admin_id: int | None) -> list[dict]:
        async with get_db_ctx() as db:
            await _get_classroom(db, classroom_id, teacher_admin_id=teacher_admin_id)
            student_count = (await db.execute(
                select(func.count()).select_from(ClassroomMember)
                .where(ClassroomMember.classroom_id == classroom_id)
            )).scalar() or 0
            quizzes = (await db.execute(
                select(ClassroomQuiz).where(ClassroomQuiz.classroom_id == classroom_id)
                .order_by(ClassroomQuiz.id.desc())
            )).scalars().all()
            result = []
            for q in quizzes:
                # 老师视角自动收卷
                if q.status == "ongoing":
                    ends = q.started_at + timedelta(minutes=q.duration_minutes)
                    if ends <= _now():
                        q.status = "ended"
                        q.ended_at = ends
                        await db.commit()
                submitted = (await db.execute(
                    select(func.count()).select_from(ClassroomQuizSubmission)
                    .where(ClassroomQuizSubmission.quiz_id == q.id)
                )).scalar() or 0
                result.append({
                    "id": q.id, "title": q.title, "duration_minutes": q.duration_minutes,
                    "question_count": len(q.question_ids or []), "status": q.status,
                    "started_at": q.started_at, "ended_at": q.ended_at,
                    "submitted_count": int(submitted), "student_count": int(student_count),
                })
            return result

    async def quiz_progress(self, classroom_id: int, quiz_id: int, teacher_admin_id: int | None) -> dict:
        """实时进度（轮询）：已交 X / 应考 Y + 倒计时。"""
        async with get_db_ctx() as db:
            await _get_classroom(db, classroom_id, teacher_admin_id=teacher_admin_id)
            quiz = await db.get(ClassroomQuiz, quiz_id)
            if quiz is None or quiz.classroom_id != classroom_id:
                raise NotFoundException("测验")
            ends_at = quiz.started_at + timedelta(minutes=quiz.duration_minutes)
            if quiz.status == "ongoing" and ends_at <= _now():
                quiz.status = "ended"
                quiz.ended_at = ends_at
                await db.commit()
            student_count = (await db.execute(
                select(func.count()).select_from(ClassroomMember)
                .where(ClassroomMember.classroom_id == classroom_id)
            )).scalar() or 0
            submitted = (await db.execute(
                select(func.count()).select_from(ClassroomQuizSubmission)
                .where(ClassroomQuizSubmission.quiz_id == quiz_id)
            )).scalar() or 0
            return {
                "quiz_id": quiz.id, "status": quiz.status,
                "submitted_count": int(submitted), "student_count": int(student_count),
                "remaining_seconds": max(0, int((ends_at - _now()).total_seconds())),
            }

    async def list_submissions(
        self, classroom_id: int, quiz_id: int, teacher_admin_id: int | None
    ) -> list[dict]:
        """批改列表：含题目答案、每份答卷明细与附件（short 为重签 HTML）。"""
        async with get_db_ctx() as db:
            await _get_classroom(db, classroom_id, teacher_admin_id=teacher_admin_id)
            quiz = await db.get(ClassroomQuiz, quiz_id)
            if quiz is None or quiz.classroom_id != classroom_id:
                raise NotFoundException("测验")
            questions = (await db.execute(
                select(ClassroomQuestion).where(
                    ClassroomQuestion.id.in_(quiz.question_ids or [])
                )
            )).scalars().all()
            qmap = {q.id: q for q in questions}
            subs = (await db.execute(
                select(ClassroomQuizSubmission, ClassroomMember)
                .join(ClassroomMember, ClassroomMember.user_id == ClassroomQuizSubmission.user_id)
                .where(
                    ClassroomMember.classroom_id == classroom_id,
                    ClassroomQuizSubmission.quiz_id == quiz_id,
                ).order_by(ClassroomQuizSubmission.id)
            )).all()
            submission_ids = [s.id for s, _m in subs]
            attach_rows = (await db.execute(
                select(ClassroomQuizAttachment).where(
                    ClassroomQuizAttachment.submission_id.in_(submission_ids),
                    ClassroomQuizAttachment.status == "bound",
                ).order_by(ClassroomQuizAttachment.id)
            )).scalars().all() if submission_ids else []
            attach_urls = await ClassroomAttachmentService.sign_read_urls(
                [row.object_key for row in attach_rows]
            )
            attachments_by_submission: dict[int, list[dict]] = {}
            for row in attach_rows:
                attachments_by_submission.setdefault(row.submission_id, []).append({
                    "id": row.id, "question_id": row.question_id, "kind": row.kind,
                    "filename": row.filename, "content_type": row.content_type,
                    "size_bytes": row.size_bytes,
                    "url": attach_urls.get(row.object_key, ""),
                })
            short_ids = {q.id for q in questions if q.type == "short"}
            signer = make_read_signer()

            def _display_answers(submission: ClassroomQuizSubmission) -> dict:
                displayed = {}
                for key, value in (submission.answers or {}).items():
                    question_id = int(key) if str(key).isdigit() else None
                    if question_id in short_ids and value:
                        displayed[key] = resign_short_answer_html(str(value), signer)
                    else:
                        displayed[key] = value
                return displayed

            return {
                "questions": [
                    {"id": q.id, "type": q.type, "stem": q.stem, "options": q.options,
                     "answer": q.answer, "score": q.score, "analysis": q.analysis}
                    for q in questions if q.id in (quiz.question_ids or [])
                ],
                "submissions": [
                    {"id": s.id, "user_id": s.user_id, "student_name": m.real_name_snapshot,
                     "answers": _display_answers(s), "auto_score": s.auto_score,
                     "manual_score": s.manual_score, "total_score": s.total_score,
                     "status": s.status, "submitted_at": s.submitted_at,
                     "manual_scores": s.manual_scores or {},
                     "attachments": attachments_by_submission.get(s.id, [])}
                    for s, m in subs
                ],
            }

    async def review_submission(
        self, classroom_id: int, quiz_id: int, submission_id: int,
        teacher_admin_id: int | None, data: ClassroomSubmissionReview,
    ) -> None:
        """批改放行：manual_scores 覆盖每题得分（简答必填、可改判填空），approve=True 放行。"""
        async with get_db_ctx() as db:
            await _get_classroom(db, classroom_id, teacher_admin_id=teacher_admin_id)
            quiz = await db.get(ClassroomQuiz, quiz_id)
            if quiz is None or quiz.classroom_id != classroom_id:
                raise NotFoundException("测验")
            sub = await db.get(ClassroomQuizSubmission, submission_id)
            if sub is None or sub.quiz_id != quiz_id:
                raise NotFoundException("答卷")
            questions = (await db.execute(
                select(ClassroomQuestion).where(
                    ClassroomQuestion.id.in_(quiz.question_ids or [])
                )
            )).scalars().all()
            qmap = {q.id: q for q in questions}
            manual: dict[str, int] = dict(sub.manual_scores or {})
            for qid_str, score in data.manual_scores.items():
                try:
                    qid = int(qid_str)
                except ValueError:
                    raise ValidationException("题目 ID 无效")
                if qid not in qmap:
                    raise ValidationException("题目不属于该测验")
                if not 0 <= score <= qmap[qid].score:
                    raise ValidationException("给分超出题目分值")
                manual[qid_str] = int(score)
            sub.manual_scores = manual
            # 总分 = 非覆盖题自动分 + 覆盖题手动分
            total = 0
            for q in questions:
                key = str(q.id)
                if key in manual:
                    total += manual[key]
                else:
                    total += _grade_answer(q, (sub.answers or {}).get(key))
            sub.manual_score = sum(manual.values())
            sub.total_score = total
            if data.approve:
                sub.status = "approved"
                sub.approved_at = _now()
            await db.commit()
