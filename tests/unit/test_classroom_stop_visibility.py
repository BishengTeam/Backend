"""课堂停课后只读可见性：列表、详情、读/写路径边界。"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest


def _utc(day: int) -> datetime:
    return datetime(2026, 9, day, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_stop_ends_ongoing_quizzes(monkeypatch):
    """停课必须立即结束所有进行中的测验，冻结一切写操作。"""
    from app.services import classroom_admin

    classroom = classroom_admin.Classroom(
        name="晚间课堂", teacher_admin_id=5, status="active"
    )
    classroom.id = 42
    classroom.join_code = "123456"
    classroom.join_code_expires_at = _utc(3)
    ongoing = classroom_admin.ClassroomQuiz(
        classroom_id=42, title="随堂练习", duration_minutes=10,
        status="ongoing", started_at=_utc(2),
    )
    committed = []

    class QuizResult:
        def scalars(self):
            return SimpleNamespace(all=lambda: [ongoing])

    class Db:
        async def execute(self, query):
            assert "classroom_quiz" in str(query)
            return QuizResult()

        async def commit(self):
            committed.append(True)

    @asynccontextmanager
    async def get_db_ctx():
        yield Db()

    async def get_classroom(_db, classroom_id, *, teacher_admin_id):
        assert classroom_id == 42
        assert teacher_admin_id == 5
        return classroom

    monkeypatch.setattr(classroom_admin, "get_db_ctx", get_db_ctx)
    monkeypatch.setattr(classroom_admin, "_get_classroom", get_classroom)

    await classroom_admin.ClassroomAdminService().stop(42, teacher_admin_id=5)

    assert classroom.status == "stopped"
    assert classroom.stopped_at is not None
    assert classroom.join_code is None
    assert classroom.join_code_expires_at is None
    assert ongoing.status == "ended"
    assert ongoing.ended_at == classroom.stopped_at
    assert committed == [True]


@pytest.mark.asyncio
async def test_my_classrooms_keeps_stopped_and_orders_active_first(monkeypatch):
    """我的课堂列表必须包含停课课堂，且 active 在前、stopped 在后。"""
    from app.services import classroom as service

    active = service.Classroom(name="在上课", teacher_admin_id=5, status="active")
    active.id = 1
    stopped = service.Classroom(name="已结课", teacher_admin_id=5, status="stopped")
    stopped.id = 2
    member_active = service.ClassroomMember(
        classroom_id=1, user_id=9, real_name_snapshot="张三"
    )
    member_active.id = 11
    member_stopped = service.ClassroomMember(
        classroom_id=2, user_id=9, real_name_snapshot="张三"
    )
    member_stopped.id = 12
    captured = {}
    video_queues = iter([[101, 102], []])

    class Rows:
        def all(self):
            return [(active, member_active), (stopped, member_stopped)]

    class ScalarList:
        def __init__(self, values):
            self._values = values

        def scalars(self):
            return SimpleNamespace(all=lambda: self._values)

    class Db:
        async def execute(self, query):
            sql = str(query)
            if "FROM classroom_video" in sql:
                return ScalarList(next(video_queues))
            if "FROM classroom_quiz " in sql or sql.rstrip().endswith(
                "FROM classroom_quiz"
            ):
                return SimpleNamespace(scalar_one_or_none=lambda: None)
            captured["sql"] = sql
            return Rows()

    @asynccontextmanager
    async def get_db_ctx():
        yield Db()

    monkeypatch.setattr(service, "get_db_ctx", get_db_ctx)

    items = await service.ClassroomService().my_classrooms(9)

    assert [item["id"] for item in items] == [1, 2]
    assert [item["status"] for item in items] == ["active", "stopped"]
    assert items[0]["video_count"] == 2
    assert items[0]["ongoing_quiz_id"] is None
    assert "ORDER BY CASE" in captured["sql"].upper()
    where_part = captured["sql"].upper().split("WHERE", 1)[1].split("ORDER BY")[0]
    assert "STATUS" not in where_part


@pytest.mark.asyncio
async def test_member_classroom_stopped_read_write_matrix():
    """stopped 课堂：allow_stopped 只放行读路径；写路径与不存在课堂一律拒绝。"""
    from app.domain.classroom.src.index import Classroom, ClassroomMember
    from app.port.exceptions import NotFoundException
    from app.services.classroom import ClassroomService

    stopped = Classroom(name="已结课", teacher_admin_id=5, status="stopped")
    stopped.id = 2
    active = Classroom(name="在上课", teacher_admin_id=5, status="active")
    active.id = 1
    member = ClassroomMember(
        classroom_id=2, user_id=9, real_name_snapshot="张三"
    )
    member.id = 7

    class Db:
        async def get(self, model, classroom_id):
            if classroom_id == 1:
                return active
            if classroom_id == 2:
                return stopped
            return None

        async def execute(self, _query):
            return SimpleNamespace(scalar_one_or_none=lambda: member)

    db = Db()
    got = await ClassroomService._member_classroom(db, 9, 2, allow_stopped=True)
    assert got[0] is stopped
    assert got[1] is member
    assert await ClassroomService._member_classroom(db, 9, 1) == (active, member)

    with pytest.raises(NotFoundException, match="停课"):
        await ClassroomService._member_classroom(db, 9, 2)
    with pytest.raises(NotFoundException):
        await ClassroomService._member_classroom(db, 9, 99, allow_stopped=True)


@pytest.mark.asyncio
async def test_member_quiz_stopped_blocks_write_paths_only():
    """取卷/交卷走默认路径必须拒绝停课课堂；成绩/回看走 allow_stopped。"""
    from app.domain.classroom.src.index import (
        Classroom,
        ClassroomMember,
        ClassroomQuiz,
    )
    from app.port.exceptions import NotFoundException
    from app.services.classroom import ClassroomService

    stopped = Classroom(name="已结课", teacher_admin_id=5, status="stopped")
    stopped.id = 2
    quiz = ClassroomQuiz(
        classroom_id=2, title="随堂练习", duration_minutes=10,
        status="ended", started_at=_utc(2),
    )
    quiz.id = 7
    member = ClassroomMember(
        classroom_id=2, user_id=9, real_name_snapshot="张三"
    )
    member.id = 8

    class Db:
        async def get(self, model, pk):
            if model is ClassroomQuiz and pk == 7:
                return quiz
            if model is Classroom and pk == 2:
                return stopped
            return None

        async def execute(self, _query):
            return SimpleNamespace(scalar_one_or_none=lambda: member.id)

    db = Db()
    service = ClassroomService()
    with pytest.raises(NotFoundException, match="停课"):
        await service._member_quiz(db, 9, 7)
    assert await service._member_quiz(
        db, 9, 7, allow_stopped=True
    ) is quiz


@pytest.mark.asyncio
async def test_attachment_paths_reject_stopped_classroom():
    """附件上传/草稿/删除在停课课堂必须被课堂状态兜底拒绝。"""
    from app.domain.classroom.src.index import (
        Classroom,
        ClassroomMember,
        ClassroomQuiz,
    )
    from app.port.exceptions import BusinessException
    from app.services.classroom_attachment import ClassroomAttachmentService

    stopped = Classroom(name="已结课", teacher_admin_id=5, status="stopped")
    stopped.id = 2
    quiz = ClassroomQuiz(
        classroom_id=2, title="随堂练习", duration_minutes=10,
        status="ongoing", started_at=_utc(3),
    )
    quiz.id = 7
    member = ClassroomMember(
        classroom_id=2, user_id=9, real_name_snapshot="张三"
    )
    member.id = 9

    class Db:
        async def get(self, model, pk):
            if model is ClassroomQuiz and pk == 7:
                return quiz
            if model is Classroom and pk == 2:
                return stopped
            return None

        async def execute(self, _query):
            return SimpleNamespace(scalar_one_or_none=lambda: member.id)

    db = Db()
    with pytest.raises(BusinessException, match="停课"):
        await ClassroomAttachmentService._uploadable_quiz_question(db, 9, 7, 3)
    with pytest.raises(BusinessException, match="停课"):
        await ClassroomAttachmentService._member_quiz(db, 9, 7)


@pytest.mark.asyncio
async def test_detail_reports_submission_status(monkeypatch):
    """详情必须回传每题批改状态，前端据此显示待批改/查看结果。"""
    from app.domain.classroom.src.index import Classroom, ClassroomMember, ClassroomQuiz
    from app.services import classroom as service

    classroom = Classroom(name="晚间课堂", teacher_admin_id=5, status="stopped")
    classroom.id = 2
    member = ClassroomMember(
        classroom_id=2, user_id=9, real_name_snapshot="张三"
    )
    member.id = 10
    graded = ClassroomQuiz(
        classroom_id=2, title="第一练", duration_minutes=10,
        status="ended", started_at=_utc(1),
    )
    graded.id = 71
    waiting = ClassroomQuiz(
        classroom_id=2, title="第二练", duration_minutes=10,
        status="ended", started_at=_utc(2),
    )
    waiting.id = 72
    submission_results = iter([None, "approved"])  # 依次对应 waiting / graded

    async def member_classroom(_db, _user_id, _classroom_id, *, allow_stopped=False):
        assert allow_stopped is True
        return classroom, member

    class ListResult:
        def __init__(self, values):
            self._values = values

        def scalars(self):
            return SimpleNamespace(all=lambda: self._values)

    class Db:
        async def execute(self, query):
            sql = str(query)
            if "FROM classroom_video" in sql:
                return ListResult([])
            if "FROM classroom_quiz_submission" in sql:
                return SimpleNamespace(
                    scalar_one_or_none=lambda: next(submission_results)
                )
            return ListResult([waiting, graded])  # 列表按 id 倒序

    monkeypatch.setattr(
        service.ClassroomService, "_member_classroom", staticmethod(member_classroom)
    )

    @asynccontextmanager
    async def get_db_ctx():
        yield Db()

    monkeypatch.setattr(service, "get_db_ctx", get_db_ctx)

    detail = await service.ClassroomService().detail(9, 2)

    assert detail["status"] == "stopped"
    assert [q["id"] for q in detail["quizzes"]] == [72, 71]
    assert detail["quizzes"][0]["submission_status"] is None
    assert detail["quizzes"][0]["submitted"] is False
    assert detail["quizzes"][1]["submission_status"] == "approved"
    assert detail["quizzes"][1]["submitted"] is True
