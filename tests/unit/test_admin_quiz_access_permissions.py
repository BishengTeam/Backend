from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.admin import quiz as quiz_api
from app.policy.permissions import ROLE_PERMISSIONS
from app.schemas.admin_quiz_contract import AdminQuizLibraryUpdate
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
