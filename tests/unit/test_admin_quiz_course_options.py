"""Quiz administrators can bind courses without course-management access."""

from contextlib import asynccontextmanager
import inspect
from types import SimpleNamespace

import pytest

from app.api.admin import quiz as quiz_api
from app.policy.permissions import ROLE_PERMISSIONS
from app.schemas.admin_quiz_contract import AdminQuizCourseOptionResponse
from app.services import admin_quiz_v2 as service_module
from app.services.admin_quiz_v2 import AdminQuizV2Service


class _Result:
    def all(self):
        return [
            SimpleNamespace(id=3, title="网络工程师课程", status="published"),
            SimpleNamespace(id=8, title="网络安全课程", status="published"),
        ]


class _DB:
    def __init__(self):
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return _Result()


@pytest.mark.asyncio
async def test_course_options_are_an_active_narrow_projection(monkeypatch) -> None:
    db = _DB()

    @asynccontextmanager
    async def _context():
        yield db

    monkeypatch.setattr(service_module, "get_db_ctx", _context)

    result = await AdminQuizV2Service().list_course_options(
        keyword="网络",
        limit=20,
    )

    assert result == [
        AdminQuizCourseOptionResponse(
            id=3, title="网络工程师课程", status="published"
        ),
        AdminQuizCourseOptionResponse(
            id=8, title="网络安全课程", status="published"
        ),
    ]
    statement = str(db.statement)
    assert "course.status" in statement
    assert "course.title" in statement
    assert "course.title" in statement


def test_course_option_route_does_not_grant_general_course_access() -> None:
    permissions = set(ROLE_PERMISSIONS["quiz_admin"])
    assert "course_quiz_bind" in permissions
    assert "course:list" not in permissions
    assert set(AdminQuizCourseOptionResponse.model_fields) == {
        "id",
        "title",
        "status",
    }
    assert 'require_permission("course_quiz_bind")' in inspect.getsource(
        quiz_api.list_course_options
    )
