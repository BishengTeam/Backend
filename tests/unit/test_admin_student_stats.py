"""学生练习+考试汇总：口径、姓名来源与 CSV 导出。"""

from contextlib import asynccontextmanager
from csv import reader
from datetime import datetime, timezone
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas.admin_quiz_contract import AdminQuizStudentStatsQuery
from app.services import admin_quiz as service_module
from app.services.admin_quiz import AdminQuizService


class _Result:
    def __init__(self, rows=None, scalars=None):
        self._rows = rows or []
        self._scalars = scalars or []

    def all(self):
        return self._rows

    def scalars(self):
        return self._scalars


class _SequenceDB:
    """execute 按入队顺序返回：练习聚合 → 考试聚合 → 用户 → 资料 → 实名。"""

    def __init__(self, results: list[_Result]):
        self._results = results
        self.statements: list[object] = []

    async def execute(self, statement):
        self.statements.append(statement)
        return self._results.pop(0)


def _run_with(monkeypatch, db: _SequenceDB) -> None:
    @asynccontextmanager
    async def _context():
        yield db

    monkeypatch.setattr(service_module, "get_db_ctx", _context)


def _query(**overrides) -> AdminQuizStudentStatsQuery:
    payload = {"library_id": 11}
    payload.update(overrides)
    return AdminQuizStudentStatsQuery(**payload)


def test_date_range_must_be_paired() -> None:
    with pytest.raises(ValidationError):
        AdminQuizStudentStatsQuery.model_validate(
            {"library_id": 11, "date_from": "2026-11-01"}
        )
    with pytest.raises(ValidationError):
        AdminQuizStudentStatsQuery.model_validate(
            {
                "library_id": 11,
                "date_from": "2025-01-01",
                "date_to": "2026-11-01",
            }
        )


@pytest.mark.asyncio
async def test_csv_export_merges_practice_and_exam_with_name_sources(
    monkeypatch,
) -> None:
    practice_rows = [
        # user_id, total, distinct questions, first attempts, first correct, days, last
        (1, 30, 12, 12, 5, 6, datetime(2026, 11, 18, 9, 0, tzinfo=timezone.utc)),
        (2, 4, 4, 4, 4, 1, datetime(2026, 11, 19, 8, 0, tzinfo=timezone.utc)),
    ]
    exam_rows = [
        # user_id, count, avg, high, low, pass count, last
        (1, 2, Decimal("50.0"), Decimal("70.0"), Decimal("30.0"), 1,
         datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)),
        (3, 0, None, None, None, 0, None),
    ]
    users = [
        SimpleNamespace(id=1, phone="13800000001"),
        SimpleNamespace(id=2, phone=None),
        SimpleNamespace(id=3, phone="13900000003"),
    ]
    profiles = [
        SimpleNamespace(user_id=1, declared_name="张三", phone=None),
        SimpleNamespace(user_id=2, declared_name=None, phone=None),
    ]
    identities = [
        SimpleNamespace(user_id=2, real_name="李四", status="verified"),
        SimpleNamespace(user_id=3, real_name="王五", status="pending"),
    ]
    db = _SequenceDB(
        [
            _Result(rows=practice_rows),
            _Result(rows=exam_rows),
            _Result(scalars=users),
            _Result(scalars=profiles),
            _Result(scalars=identities),
        ]
    )
    _run_with(monkeypatch, db)

    content = await AdminQuizService().export_student_stats_csv(_query())
    rows = list(reader(StringIO(content.lstrip("\ufeff"))))
    body = rows[1:]

    header = rows[0]
    assert header[:6] == ["序号", "姓名", "姓名来源", "用户ID", "手机号", "累计作答"]
    assert "及格比" in header
    assert "最近考试时间" in header

    by_id = {row[3]: row for row in body}
    assert len(body) == 3

    # 排序：累计作答降序（30 → 4 → 0）
    assert [row[3] for row in body] == ["1", "2", "3"]

    first = by_id["1"]
    assert first[1] == "张三"
    assert first[2] == "自填"
    assert first[4] == "13800000001"
    assert first[5] == "30"
    assert first[6] == "12"
    assert first[7] == "5"
    assert first[8] == "41.7"
    assert first[12] == "2"
    assert first[13] == "50.0"
    assert first[14] == "70.0"
    assert first[15] == "30.0"
    assert first[16] == "1"
    assert first[17] == "50.0"

    second = by_id["2"]
    assert second[1] == "李四"
    assert second[2] == "实名"
    assert second[8] == "100.0"
    assert second[17] == ""

    third = by_id["3"]
    assert third[1] == ""
    assert third[2] == "未填写"
    assert third[5] == "0"
    assert third[12] == "0"
    assert third[17] == ""


@pytest.mark.asyncio
async def test_export_ignores_unverified_realname(monkeypatch) -> None:
    identities = [
        SimpleNamespace(user_id=1, real_name="王五", status="pending"),
    ]
    db = _SequenceDB(
        [
            _Result(rows=[(1, 2, 2, 2, 1, 1, None)]),
            _Result(rows=[]),
            _Result(scalars=[SimpleNamespace(id=1, phone=None)]),
            _Result(scalars=[SimpleNamespace(user_id=1, declared_name=None, phone=None)]),
            _Result(scalars=identities),
        ]
    )
    _run_with(monkeypatch, db)

    content = await AdminQuizService().export_student_stats_csv(_query())
    rows = list(reader(StringIO(content.lstrip("\ufeff"))))
    assert rows[1][1] == ""
    assert rows[1][2] == "未填写"


@pytest.mark.asyncio
async def test_paginated_endpoint_keeps_display_phone_masked(monkeypatch) -> None:
    db = _SequenceDB(
        [
            _Result(rows=[(7, 9, 5, 5, 3, 2, None)]),
            _Result(rows=[]),
            _Result(scalars=[SimpleNamespace(id=7, phone="13800000007")]),
            _Result(scalars=[SimpleNamespace(user_id=7, declared_name="赵六", phone=None)]),
            _Result(scalars=[]),
        ]
    )
    _run_with(monkeypatch, db)

    page = await AdminQuizService().list_student_stats(_query(page=1, page_size=20))

    assert page.total == 1
    item = page.items[0]
    assert item.phone_masked == "138****0007"
    assert item.name == "赵六"
    assert item.practice_first_accuracy == Decimal("60.0")
    assert item.exam_pass_rate is None
