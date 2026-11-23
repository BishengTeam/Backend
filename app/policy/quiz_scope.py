"""题库数据范围（独立题库 / 课程题库）。

``quiz:manage`` 与 ``quiz:course`` 展开同一批细粒度权限，区别只在可操作的
题库集合：前者管 ``access_mode != course_entitlement``，后者管
``access_mode == course_entitlement``，两者都有（或超管）则不限。

服务层通过 ``current_quiz_library_scope()`` 取当前管理员范围；没有管理员
上下文（脚本 / 直接调用服务 / 单测）时返回 ``all``，保持既有行为。
"""

from __future__ import annotations

from contextvars import ContextVar
from enum import StrEnum

from sqlalchemy import false

from app.policy.permissions import resolve_granted_permissions
from app.port.exceptions import ForbiddenException

COURSE_ACCESS_MODE = "course_entitlement"
INDEPENDENT_ACCESS_MODES = ("free", "paid", "activation_code", "access_mode_pending")

# 当前请求的管理员对象（鉴权依赖写入，供服务层解析题库数据范围）。
current_admin_var: ContextVar[object | None] = ContextVar("current_admin", default=None)


class QuizLibraryScope(StrEnum):
    ALL = "all"
    INDEPENDENT = "independent"
    COURSE = "course"
    NONE = "none"


def scope_from_grants(grants) -> QuizLibraryScope:
    if "*" in grants:
        return QuizLibraryScope.ALL
    has_independent = "quiz:manage" in grants
    has_course = "quiz:course" in grants
    if has_independent and has_course:
        return QuizLibraryScope.ALL
    if has_course:
        return QuizLibraryScope.COURSE
    if has_independent:
        return QuizLibraryScope.INDEPENDENT
    return QuizLibraryScope.NONE


def resolve_quiz_library_scope(admin) -> QuizLibraryScope:
    if getattr(admin, "role", None) == "super_admin":
        return QuizLibraryScope.ALL
    return scope_from_grants(resolve_granted_permissions(admin))


def current_quiz_library_scope() -> QuizLibraryScope:
    admin = current_admin_var.get()
    if admin is None:
        return QuizLibraryScope.ALL
    return resolve_quiz_library_scope(admin)


def current_admin_display_name(admin_id: int) -> str | None:
    """当前请求管理员与目标一致时直接返回显示名（避免额外查询）。"""

    admin = current_admin_var.get()
    if admin is not None and getattr(admin, "id", None) == admin_id:
        return getattr(admin, "display_name", None)
    return None


def scope_allows(scope: QuizLibraryScope, access_mode: str | None) -> bool:
    if scope is QuizLibraryScope.ALL:
        return True
    if scope is QuizLibraryScope.INDEPENDENT:
        return access_mode != COURSE_ACCESS_MODE
    if scope is QuizLibraryScope.COURSE:
        return access_mode == COURSE_ACCESS_MODE
    return False


def library_scope_clause(scope: QuizLibraryScope, library_model):
    """返回可直接拼进 SQLAlchemy where 的条件；``all`` 返回 None。"""

    if scope is QuizLibraryScope.ALL:
        return None
    if scope is QuizLibraryScope.INDEPENDENT:
        return library_model.access_mode != COURSE_ACCESS_MODE
    if scope is QuizLibraryScope.COURSE:
        return library_model.access_mode == COURSE_ACCESS_MODE
    return false()


def ensure_library_in_scope(scope: QuizLibraryScope, library) -> None:
    if not scope_allows(scope, getattr(library, "access_mode", None)):
        raise ForbiddenException("缺少该题库的管理权限")
