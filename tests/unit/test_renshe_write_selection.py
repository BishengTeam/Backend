from types import SimpleNamespace

import pytest

from app.port.exceptions import ConflictException
from app.services.renshe_batch_project import (
    resolve_upload_project_id,
    select_write_application,
)


def _app(project_id):
    return SimpleNamespace(batch_project_id=project_id)


def test_legacy_application_is_adopted_only_when_project_is_auto_resolved():
    legacy = _app(None)
    selected, adopt = select_write_application(
        [legacy], explicit_project_id=None, resolved_project_id=7
    )
    assert selected is legacy and adopt is True

    with pytest.raises(ConflictException, match="未关联项目"):
        select_write_application(
            [legacy], explicit_project_id=7, resolved_project_id=7
        )


def test_existing_project_application_is_reused_or_rejected():
    existing = _app(7)
    selected, adopt = select_write_application(
        [existing], explicit_project_id=7, resolved_project_id=7
    )
    assert selected is existing and adopt is False

    with pytest.raises(ConflictException, match="其他项目"):
        select_write_application(
            [existing], explicit_project_id=None, resolved_project_id=8
        )


def test_multiple_active_applications_are_blocked():
    with pytest.raises(ConflictException, match="多条活跃报名"):
        select_write_application(
            [_app(None), _app(7)], explicit_project_id=None, resolved_project_id=7
        )


def test_upload_project_prefers_registered_project():
    assert resolve_upload_project_id([_app(7)], None) == 7
    assert resolve_upload_project_id([], 7) == 7
    with pytest.raises(ConflictException, match="不一致"):
        resolve_upload_project_id([_app(7)], 8)
