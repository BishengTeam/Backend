from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_list_students_serializes_created_at_as_joined_at(monkeypatch):
    """课堂成员表没有 joined_at 列；名单接口必须使用创建时间且不能触发 AttributeError。"""
    from app.services import classroom_admin

    joined_at = datetime(2026, 9, 2, 12, 30, tzinfo=timezone.utc)
    member = classroom_admin.ClassroomMember(
        classroom_id=42,
        user_id=99,
        real_name_snapshot="张三",
    )
    member.id = 7
    member.created_at = joined_at

    class MemberResult:
        def scalars(self):
            return SimpleNamespace(all=lambda: [member])

    async def execute(_query):
        return MemberResult()

    db = SimpleNamespace(execute=execute)

    @asynccontextmanager
    async def get_db_ctx():
        yield db

    async def get_classroom(_db, classroom_id, *, teacher_admin_id):
        assert classroom_id == 42
        assert teacher_admin_id == 5

    monkeypatch.setattr(classroom_admin, "get_db_ctx", get_db_ctx)
    monkeypatch.setattr(classroom_admin, "_get_classroom", get_classroom)

    students = await classroom_admin.ClassroomAdminService().list_students(
        42, teacher_admin_id=5
    )

    assert students == [
        {
            "id": 7,
            "user_id": 99,
            "real_name": "张三",
            "joined_at": joined_at,
        }
    ]
