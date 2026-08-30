"""Regression tests for the /api/zones home aggregation.

The endpoint previously ran ``CourseListResponse.model_validate`` directly on
ORM rows. That schema intentionally has no ``from_attributes`` support —
courses carry a private OSS storage key instead of a cover URL and need
fen→yuan conversion — so a single active course raised ValidationError and
turned the whole homepage into a 500. ZoneService must therefore delegate
course serialization to CourseService.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

import app.services.zone as zone_module
from app.domain.certification.src.index import Course
from app.domain.content.src.index import Zone
from app.domain.content.src.model.banner import Banner
from app.schemas.course import CourseListResponse
from app.services.course import CourseService
from app.services.zone import ZoneService


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "_FakeResult":
        return self

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    """Minimal async session dispatching rows by the queried ORM entity.

    The zone-card loop issues one Zone query per zone_type in declaration
    order, so those queries are matched by their deterministic position.
    """

    def __init__(self, rows_by_entity: dict[type, list[Any]]) -> None:
        self._rows_by_entity = rows_by_entity
        self._zone_query_index = 0

    async def execute(self, stmt: Any) -> _FakeResult:
        entity = stmt.column_descriptions[0]["entity"]
        if entity is Zone:
            ztype = zone_module.ALL_ZONE_TYPES[self._zone_query_index]
            self._zone_query_index += 1
            rows = [
                row
                for row in self._rows_by_entity.get(Zone, [])
                if row.zone_type == ztype
            ]
            return _FakeResult(rows)
        return _FakeResult(self._rows_by_entity.get(entity, []))


def _patch_db(monkeypatch: pytest.MonkeyPatch, session: _FakeSession) -> None:
    @asynccontextmanager
    async def _ctx():
        yield session

    monkeypatch.setattr(zone_module, "get_db_ctx", _ctx)


@pytest.mark.asyncio
async def test_active_course_does_not_break_home_aggregation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One active course must produce a study section, not a 500."""

    course_row = SimpleNamespace(id=7, title="H3C 认证课程")
    banner_row = SimpleNamespace(id=1, image_url="banner.jpg", jump_link=None, sort=0)
    zone_row = SimpleNamespace(
        id=1,
        zone_type="study",
        title="学习专区",
        cover_url=None,
        description=None,
        sort_order=0,
    )
    session = _FakeSession(
        {Banner: [banner_row], Zone: [zone_row], Course: [course_row]}
    )
    _patch_db(monkeypatch, session)

    expected = CourseListResponse(
        id=7,
        title="H3C 认证课程",
        category="网络工程",
        cover_url="https://oss.example.com/signed-cover.jpg",
        price=120000,
        price_yuan="1200.00",
        teacher_name="王老师",
    )

    course_service = CourseService()
    serialized: list[Any] = []

    async def _fake_course_list_response(row: Any) -> CourseListResponse:
        serialized.append(row)
        return expected

    course_service.course_list_response = _fake_course_list_response

    result = await ZoneService(course_service=course_service).get_home_aggregation()

    assert serialized == [course_row]
    assert [banner.image_url for banner in result.banners] == ["banner.jpg"]
    assert result.zones["study"].items[0].title == "学习专区"
    assert result.zones["study"].courses == [expected]


def test_courses_are_excluded_from_generic_entity_validation() -> None:
    """CourseListResponse must never be fed ORM rows via model_validate."""

    assert "courses" not in zone_module._ENTITY_QUERIES


def test_entity_schemas_support_from_attributes() -> None:
    """Schemas still driven by the generic loop must accept ORM rows."""

    for field_name, (_model, schema, _flag) in zone_module._ENTITY_QUERIES.items():
        assert schema.model_config.get("from_attributes") is True, field_name
