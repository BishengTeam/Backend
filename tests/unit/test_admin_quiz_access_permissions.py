from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.admin import quiz as quiz_api
from app.policy.permissions import ROLE_PERMISSIONS
from app.schemas.admin_quiz_contract import (
    AdminQuizLibraryAccessModeConvert,
    AdminQuizLibraryUpdate,
)
from app.services.admin_quiz_v2 import AdminQuizV2Service


@pytest.mark.asyncio
async def test_quiz_admin_can_change_library_access_configuration(
    monkeypatch,
) -> None:
    """Access configuration is part of the frozen quiz-admin boundary."""

    updated = SimpleNamespace(id=19, access_mode="course_entitlement")
    update_library = AsyncMock(return_value=updated)
    monkeypatch.setattr(AdminQuizV2Service, "update_library", update_library)

    response = await quiz_api.update_library(
        body=AdminQuizLibraryUpdate(
            lock_version=4,
            access_mode="course_entitlement",
        ),
        library_id=19,
        admin=SimpleNamespace(id=7, role="quiz_admin"),
    )

    assert response.data is updated
    update_library.assert_awaited_once()
    assert update_library.await_args.kwargs == {"admin_id": 7}
    assert update_library.await_args.args[0] == 19
    assert update_library.await_args.args[1].access_mode == "course_entitlement"


def test_library_access_change_uses_the_frozen_library_permission() -> None:
    assert "quiz_library_manage" in ROLE_PERMISSIONS["quiz_admin"]
    route_source = inspect.getsource(quiz_api.update_library)
    service_source = inspect.getsource(AdminQuizV2Service.update_library)
    assert 'require_permission("quiz_library_manage")' in route_source
    assert "admin.role" not in route_source
    assert 'permission="quiz_library_manage"' in service_source
    assert "quiz_access_change" not in service_source


@pytest.mark.asyncio
async def test_access_mode_conversion_calls_dedicated_service(monkeypatch) -> None:
    converted = SimpleNamespace(
        library=SimpleNamespace(id=19, access_mode="course_entitlement"),
        sessions_affected=2,
    )
    convert_access_mode = AsyncMock(return_value=converted)
    monkeypatch.setattr(
        AdminQuizV2Service,
        "convert_library_access_mode",
        convert_access_mode,
    )
    body = AdminQuizLibraryAccessModeConvert(
        lock_version=4,
        target_mode="course_entitlement",
    )

    response = await quiz_api.convert_library_access_mode(
        body=body,
        library_id=19,
        admin=SimpleNamespace(id=7, role="super_admin"),
    )

    assert response.data is converted
    convert_access_mode.assert_awaited_once_with(
        19,
        body,
        admin_id=7,
    )


def test_access_mode_conversion_is_super_admin_reauthenticated() -> None:
    route_source = inspect.getsource(quiz_api.convert_library_access_mode)
    route_path = quiz_api.router.routes
    assert 'Depends(require_reauthenticated_super_admin)' in route_source
    assert "require_permission" not in route_source
    assert any(
        getattr(route, "path", None)
        == "/quiz/libraries/{library_id}/convert-access-mode"
        for route in route_path
    )
