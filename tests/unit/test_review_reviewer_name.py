"""审核记录返回审核人显示名（缺失时回退 None），前端再用 #id 兜底。"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services import review as review_module
from app.services.review import ReviewService


def _review(review_id: int, reviewer_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=review_id,
        target_type="identity",
        target_id=100 + review_id,
        reviewer_id=reviewer_id,
        action="approve",
        comment=None,
        created_at=datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc),
    )


class _Result:
    def __init__(self, *, scalar: int | None = None, items: list | None = None) -> None:
        self._scalar = scalar
        self._items = items or []

    def scalar(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return self._items


class _FakeDB:
    def __init__(self, results: list[_Result]) -> None:
        self._results = results

    async def execute(self, _statement):
        return self._results.pop(0)


@pytest.mark.asyncio
async def test_list_reviews_maps_reviewer_display_name(monkeypatch):
    reviews = [_review(2, 7), _review(1, 9)]
    db = _FakeDB([
        _Result(scalar=2),
        _Result(items=reviews),
        _Result(items=[(7, "认证运营")]),
    ])

    @asynccontextmanager
    async def context():
        yield db

    monkeypatch.setattr(review_module, "get_db_ctx", context)

    page = await ReviewService().list_reviews(None, page=1, page_size=20)

    by_id = {item.id: item for item in page.items}
    assert by_id[2].reviewer_name == "认证运营"
    # 管理员已被删除 / 查不到时保持 None，前端回退 #id
    assert by_id[1].reviewer_name is None
    assert page.total == 2
