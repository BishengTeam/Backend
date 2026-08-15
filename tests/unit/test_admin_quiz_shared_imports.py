from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import inspect
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from app.api.admin import quiz as quiz_api
from app.port.config import settings
from app.services import admin_quiz as service_module
from app.services.admin_quiz import AdminQuizService


class _DB:
    """Minimal CRUD fake; omitting execute also disables audit-row staging."""

    def __init__(self, job):
        self.job = job
        self.commits = 0
        self.refreshes = 0

    async def get(self, _model, entity_id):
        return self.job if entity_id == self.job.id else None

    async def commit(self):
        self.commits += 1

    async def refresh(self, _value):
        self.refreshes += 1


def _db_context(db):
    @asynccontextmanager
    async def _context():
        yield db

    return _context


@pytest.mark.asyncio
async def test_different_quiz_admin_can_view_and_retry_an_import(
    monkeypatch,
) -> None:
    creator_admin_id = 11
    acting_admin_id = 22
    job = SimpleNamespace(
        id=73,
        admin_id=creator_admin_id,
        status="failed",
        retry_count=0,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        error_message="safe failure",
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        heartbeat_at=datetime.now(timezone.utc),
        import_batch_key="shared-import-73",
    )
    db = _DB(job)
    monkeypatch.setattr(service_module, "get_db_ctx", _db_context(db))
    service = AdminQuizService()

    viewed = await service.get_import_job(job.id, admin_id=acting_admin_id)
    retried = await service.retry_import_job(job.id, admin_id=acting_admin_id)

    assert viewed is job
    assert retried is job
    assert job.admin_id == creator_admin_id
    assert job.status == "queued"
    assert db.commits == 2
    assert db.refreshes == 1


@pytest.mark.asyncio
async def test_local_import_link_is_bound_to_the_actual_accessor(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "QUIZ_IMPORT_STORAGE_TYPE", "local")
    creator_admin_id = 11
    acting_admin_id = 22
    job = SimpleNamespace(
        id=73,
        admin_id=creator_admin_id,
        source_type="json",
        source_object_key="quiz-imports/shared-import-73.json",
        report_object_key="quiz-imports/shared-import-73-errors.json",
    )

    url = await AdminQuizService()._signed_import_url(
        job,
        object_kind="report",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=3),
        accessor_admin_id=acting_admin_id,
    )

    query = parse_qs(urlsplit(url).query)
    assert query["admin_id"] == [str(acting_admin_id)]
    assert query["admin_id"] != [str(creator_admin_id)]


def test_all_import_routes_share_the_role_wide_task_pool() -> None:
    service_methods = (
        AdminQuizService.list_import_jobs,
        AdminQuizService.get_import_job,
        AdminQuizService.list_import_errors,
        AdminQuizService.get_import_category_impact,
        AdminQuizService.confirm_import_categories,
        AdminQuizService.cancel_import_job,
        AdminQuizService.get_import_report_url,
        AdminQuizService.get_import_source_url,
        AdminQuizService.retry_import_job,
    )
    for method in service_methods:
        source = inspect.getsource(method)
        assert "is_super_admin" not in source
        assert "job.admin_id != admin_id" not in source
        assert "QuizImportJob.admin_id == admin_id" not in source

    route_methods = (
        quiz_api.list_import_jobs,
        quiz_api.get_import_job,
        quiz_api.list_import_errors,
        quiz_api.get_import_category_impact,
        quiz_api.confirm_import_categories,
        quiz_api.cancel_import_job,
        quiz_api.get_import_report_url,
        quiz_api.get_import_source_url,
        quiz_api.retry_import_job,
    )
    assert all("is_super_admin" not in inspect.getsource(route) for route in route_methods)
