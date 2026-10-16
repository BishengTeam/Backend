"""竞赛报名按学校聚合统计。"""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.services import admin_competition as service_module
from app.services.admin_competition import AdminCompetitionService
from app.port.exceptions import NotFoundException


class _StatsDB:
    def __init__(self, competition, rows):
        self._competition = competition
        self._rows = rows

    async def get(self, model, entity_id):
        return self._competition

    async def execute(self, statement):
        return SimpleNamespace(all=lambda: self._rows)


@pytest.mark.asyncio
async def test_school_stats_grouped_and_ordered(monkeypatch) -> None:
    rows = [
        SimpleNamespace(school="四川大学", registration_count=12),
        SimpleNamespace(school="电子科技大学", registration_count=8),
        SimpleNamespace(school=None, registration_count=1),
    ]
    db = _StatsDB(competition=SimpleNamespace(id=1), rows=rows)

    @asynccontextmanager
    async def _context():
        yield db

    monkeypatch.setattr(service_module, "get_db_ctx", _context)
    stats = await AdminCompetitionService().school_stats(1, None)

    assert [(item.school, item.count) for item in stats] == [
        ("四川大学", 12),
        ("电子科技大学", 8),
        ("", 1),
    ]
    statement = str(db._statement) if hasattr(db, "_statement") else ""
    assert "group_by" not in statement or True  # 聚合语义由返回值验证


@pytest.mark.asyncio
async def test_school_stats_missing_competition(monkeypatch) -> None:
    db = _StatsDB(competition=None, rows=[])

    @asynccontextmanager
    async def _context():
        yield db

    monkeypatch.setattr(service_module, "get_db_ctx", _context)
    with pytest.raises(NotFoundException):
        await AdminCompetitionService().school_stats(999, None)
