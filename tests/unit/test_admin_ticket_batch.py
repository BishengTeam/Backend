"""批量改工单状态：路由鉴权/参数校验 + 事务内 updated/skipped 计数。"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.adapter.database import Base
from app.api.admin import tickets as tickets_api
from app.domain.content.src.index import Ticket
from app.middleware import auth
from app.middleware.error_handler import app_exception_handler, validation_exception_handler
from app.port.exceptions import AppException
from app.schemas.admin_ticket import AdminTicketBatchStatusResult
from app.services import admin_ticket as service_module
from app.services.admin_ticket import AdminTicketService


@pytest.fixture
async def ticket_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda db: Base.metadata.create_all(db, tables=[Ticket.__table__])
            )
        factory = async_sessionmaker(engine, expire_on_commit=False)

        @asynccontextmanager
        async def context():
            async with factory() as db:
                yield db

        monkeypatch.setattr(service_module, "get_db_ctx", context)
        yield factory
    finally:
        await engine.dispose()


async def test_batch_update_counts_updated_and_skipped(ticket_db):
    async with ticket_db() as db:
        db.add_all([
            Ticket(id=1, user_id=1, content="a", status="waiting_manual"),
            Ticket(id=2, user_id=1, content="b", status="resolved"),
            Ticket(id=3, user_id=1, content="c", status="processing"),
        ])
        await db.commit()

    # 1 需要改；2 已是目标状态；3 需要改；999 不存在；重复的 1 去重
    result = await AdminTicketService().batch_update_status([1, 2, 3, 999, 1], "resolved")

    assert result.updated == 2
    assert result.skipped == 2
    async with ticket_db() as db:
        rows = {
            ticket.id: ticket.status
            for ticket in (await db.execute(select(Ticket))).scalars().all()
        }
    assert rows == {1: "resolved", 2: "resolved", 3: "resolved"}


def _app(*, role: str, permissions: list[str] | None) -> FastAPI:
    principal = SimpleNamespace(
        id=5,
        role=role,
        permissions=permissions,
        is_active=True,
        auth_version=1,
        must_change_password=False,
    )
    app = FastAPI()
    app.include_router(tickets_api.router, prefix="/admin")
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.dependency_overrides[auth.require_normal_admin] = lambda: principal
    return app


async def _post(app: FastAPI, body: dict):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/admin/tickets/batch-status", json=body)


async def test_batch_status_requires_user_list_permission(monkeypatch):
    service = AsyncMock()
    monkeypatch.setattr(tickets_api, "AdminTicketService", lambda: service)
    app = _app(role="course_admin", permissions=None)

    response = await _post(app, {"ids": [1], "status": "resolved"})

    assert response.status_code == 403
    assert response.json()["message"] == "缺少权限: user:list"
    service.batch_update_status.assert_not_awaited()


async def test_batch_status_rejects_empty_ids(monkeypatch):
    service = AsyncMock()
    monkeypatch.setattr(tickets_api, "AdminTicketService", lambda: service)
    app = _app(role="super_admin", permissions=None)

    response = await _post(app, {"ids": [], "status": "resolved"})

    assert response.status_code == 422
    service.batch_update_status.assert_not_awaited()


async def test_batch_status_rejects_unknown_status(monkeypatch):
    service = AsyncMock()
    monkeypatch.setattr(tickets_api, "AdminTicketService", lambda: service)
    app = _app(role="super_admin", permissions=None)

    response = await _post(app, {"ids": [1], "status": "closed"})

    assert response.status_code == 422
    service.batch_update_status.assert_not_awaited()


async def test_batch_status_returns_counts(monkeypatch):
    service = AsyncMock()
    service.batch_update_status = AsyncMock(
        return_value=AdminTicketBatchStatusResult(updated=3, skipped=1)
    )
    monkeypatch.setattr(tickets_api, "AdminTicketService", lambda: service)
    app = _app(role="cert_admin", permissions=["user:list"])

    response = await _post(app, {"ids": [1, 2, 3, 4], "status": "processing"})

    assert response.status_code == 200
    assert response.json()["data"] == {"updated": 3, "skipped": 1}
    assert response.json()["message"] == "已更新 3 条，跳过 1 条"
    service.batch_update_status.assert_awaited_once_with([1, 2, 3, 4], "processing")
