"""Response contract for anonymous local signed quiz-import downloads."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.api.admin.quiz import read_import_report, read_import_source
from app.schemas.admin_quiz_contract import AdminQuizImportReportResponse
from app.services.admin_quiz import AdminQuizService, LocalImportDownload


@pytest.mark.asyncio
async def test_local_error_report_is_an_attachment(monkeypatch) -> None:
    report = AdminQuizImportReportResponse(
        job_id=17,
        errors=[{"row": 2, "field": "category_path", "message": "分类不存在"}],
    )
    read = AsyncMock(return_value=report)
    monkeypatch.setattr(AdminQuizService, "read_import_report", read)

    response = await read_import_report(
        job_id=17,
        expires=2_000_000_000,
        admin_id=3,
        token="a" * 64,
    )

    assert response.media_type == "application/json; charset=utf-8"
    assert response.headers["content-disposition"] == (
        'attachment; filename="quiz-import-17-errors.json"'
    )
    assert response.headers["cache-control"] == "private, no-store"
    assert b'"category_path"' in response.body


@pytest.mark.asyncio
async def test_local_source_file_preserves_import_format(monkeypatch) -> None:
    read = AsyncMock(
        return_value=LocalImportDownload(
            data=b"questions:[]",
            media_type="application/json; charset=utf-8",
            extension="json",
        )
    )
    monkeypatch.setattr(AdminQuizService, "read_import_source", read)

    response = await read_import_source(
        job_id=18,
        expires=2_000_000_000,
        admin_id=3,
        token="b" * 64,
    )

    assert response.body == b"questions:[]"
    assert response.headers["content-disposition"] == (
        'attachment; filename="quiz-import-18.json"'
    )
    assert response.headers["cache-control"] == "private, no-store"
