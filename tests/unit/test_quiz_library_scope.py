"""题库数据范围：独立题库 / 课程题库 / 全部。"""

from types import SimpleNamespace

import pytest

from app.policy.permissions import resolve_admin_permissions
from app.policy.quiz_scope import (
    QuizLibraryScope,
    current_quiz_library_scope,
    ensure_library_in_scope,
    library_scope_clause,
    resolve_quiz_library_scope,
    scope_allows,
    scope_from_grants,
)
from app.port.exceptions import ForbiddenException


def _admin(role="quiz_admin", permissions=None):
    return SimpleNamespace(role=role, permissions=permissions)


def test_scope_from_grants_matrix():
    assert scope_from_grants(["*"]) is QuizLibraryScope.ALL
    assert scope_from_grants(["quiz:manage"]) is QuizLibraryScope.INDEPENDENT
    assert scope_from_grants(["quiz:course"]) is QuizLibraryScope.COURSE
    assert scope_from_grants(["quiz:manage", "quiz:course"]) is QuizLibraryScope.ALL
    assert scope_from_grants([]) is QuizLibraryScope.NONE


def test_scope_resolution_uses_role_and_grants():
    assert resolve_quiz_library_scope(_admin(role="super_admin")) is QuizLibraryScope.ALL
    assert resolve_quiz_library_scope(_admin(permissions=["quiz:course"])) is QuizLibraryScope.COURSE
    assert resolve_quiz_library_scope(_admin(permissions=None)) is QuizLibraryScope.INDEPENDENT
    # 无管理员上下文（脚本/直接调用服务）默认 all，保持既有行为
    assert current_quiz_library_scope() is QuizLibraryScope.ALL


def test_scope_allows_access_modes():
    assert scope_allows(QuizLibraryScope.COURSE, "course_entitlement")
    assert not scope_allows(QuizLibraryScope.COURSE, "free")
    assert scope_allows(QuizLibraryScope.INDEPENDENT, "free")
    assert scope_allows(QuizLibraryScope.INDEPENDENT, "access_mode_pending")
    assert not scope_allows(QuizLibraryScope.INDEPENDENT, "course_entitlement")
    assert scope_allows(QuizLibraryScope.ALL, "course_entitlement")
    assert not scope_allows(QuizLibraryScope.NONE, "free")


def test_ensure_library_in_scope_rejects_cross_scope():
    course_library = SimpleNamespace(access_mode="course_entitlement")
    independent_library = SimpleNamespace(access_mode="free")
    with pytest.raises(ForbiddenException):
        ensure_library_in_scope(QuizLibraryScope.INDEPENDENT, course_library)
    with pytest.raises(ForbiddenException):
        ensure_library_in_scope(QuizLibraryScope.COURSE, independent_library)
    ensure_library_in_scope(QuizLibraryScope.ALL, course_library)


def test_library_scope_clause_shape():
    from sqlalchemy.dialects import postgresql

    from app.domain.community.src.index import QuizLibrary

    def sql(clause):
        return str(
            clause.compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )

    assert library_scope_clause(QuizLibraryScope.ALL, QuizLibrary) is None
    assert "'course_entitlement'" in sql(
        library_scope_clause(QuizLibraryScope.COURSE, QuizLibrary)
    )
    assert "!=" in sql(
        library_scope_clause(QuizLibraryScope.INDEPENDENT, QuizLibrary)
    )
    # 两种授权都持有 = 全部题库；细粒度存量仍归一化到独立题库
    both = _admin(permissions=["quiz:manage", "quiz:course"])
    assert resolve_quiz_library_scope(both) is QuizLibraryScope.ALL
    legacy = _admin(permissions=["quiz:list", "quiz:write"])
    assert resolve_quiz_library_scope(legacy) is QuizLibraryScope.INDEPENDENT
    assert "quiz:list" in resolve_admin_permissions(legacy)
