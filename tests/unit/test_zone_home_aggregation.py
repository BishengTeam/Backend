"""Regression tests for the /api/zones home aggregation.

The endpoint previously ran ``CourseListResponse.model_validate`` directly on
ORM rows. That schema intentionally has no ``from_attributes`` support —
courses carry a private OSS storage key instead of a cover URL and need
fen→yuan conversion — so a single active course raised ValidationError and
turned the whole homepage into a 500. ZoneService must therefore delegate
course serialization to CourseService.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

import app.services.zone as zone_module
from app.domain.certification.src.index import Course
from app.domain.content.src.index import Zone
from app.domain.content.src.model.banner import Banner
from app.domain.content.src.model.gzh_article import GzhArticle
from app.domain.content.src.model.popup import Popup
from app.models.cert_product import CertProduct
from app.schemas.certification import CertificationResponse
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

    def first(self) -> Any | None:
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Minimal async session dispatching rows by the queried ORM entity.

        The zone-card loop issues one Zone query per content zone_type in declaration
    order, so those queries are matched by their deterministic position.
    """

    def __init__(self, rows_by_entity: dict[type, list[Any]]) -> None:
        self._rows_by_entity = rows_by_entity
        self._zone_query_index = 0

    async def execute(self, stmt: Any) -> _FakeResult:
        entity = stmt.column_descriptions[0]["entity"]
        if entity is Zone:
            ztype = zone_module.HOME_ZONE_CONTENT_TYPES[self._zone_query_index]
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


def test_competition_zone_banner_rows_are_not_consumed() -> None:
    """Competition sections use competition entities, not legacy zone banners."""

    assert "competition" in zone_module.ALL_ZONE_TYPES
    assert "competition" not in zone_module.HOME_ZONE_CONTENT_TYPES


@pytest.mark.asyncio
async def test_active_cert_product_keeps_public_zone_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CertProduct rows must map to CertificationResponse, not cause a 500."""

    cert_product = SimpleNamespace(
        id=10,
        type="h3c",
        code="H3CNE",
        name="H3CNE",
        chinese_name="H3C网络工程师",
        home_cover_url="/api/media/cert-h3cne-cover",
    )
    session = _FakeSession({CertProduct: [cert_product]})
    _patch_db(monkeypatch, session)

    result = await ZoneService().get_home_aggregation()

    assert result.zones["cert"].certifications == [
        CertificationResponse(
            id=10,
            name="H3CNE",
            chinese_name="H3C网络工程师",
            code="H3CNE",
            vendor="H3C",
            requires_xuexin=False,
            pay_first=True,
            home_cover_url="/api/media/cert-h3cne-cover",
        )
    ]


def test_entity_schemas_support_from_attributes() -> None:
    """Schemas still driven by the generic loop must accept ORM rows."""

    for field_name, (_model, schema, _flag) in zone_module._ENTITY_QUERIES.items():
        assert schema.model_config.get("from_attributes") is True, field_name


@pytest.mark.asyncio
async def test_popup_returns_configured_poster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """时间窗内的 popup 行必须以单条 PopupBrief 出现在聚合响应里。"""

    popup_row = SimpleNamespace(
        id=3, image_url="poster.jpg", jump_link="/pages/activity-zone/index", sort=0
    )
    session = _FakeSession({Popup: [popup_row]})
    _patch_db(monkeypatch, session)

    result = await ZoneService().get_home_aggregation()

    assert result.popup is not None
    assert result.popup.image_url == "poster.jpg"
    assert result.popup.jump_link == "/pages/activity-zone/index"


@pytest.mark.asyncio
async def test_popup_randomly_picked_among_eligible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """多条弹窗同时生效时必须从全部候选里随机抽取，而不是固定 sort 最小的一条。"""

    rows = [
        SimpleNamespace(id=1, image_url="a.jpg", jump_link=None, sort=0),
        SimpleNamespace(id=2, image_url="b.jpg", jump_link=None, sort=0),
    ]
    session = _FakeSession({Popup: rows})
    _patch_db(monkeypatch, session)
    monkeypatch.setattr(zone_module.random, "choice", lambda seq: seq[-1])

    result = await ZoneService().get_home_aggregation()

    assert result.popup is not None
    assert result.popup.image_url == "b.jpg"


@pytest.mark.asyncio
async def test_popup_absent_yields_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有配置弹窗时 popup 字段必须为 None，而不是报错或空对象。"""

    session = _FakeSession({})
    _patch_db(monkeypatch, session)

    result = await ZoneService().get_home_aggregation()

    assert result.popup is None


@pytest.mark.asyncio
async def test_gzh_articles_piggyback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同步落库的公众号推文必须捎带在聚合响应里（首页 ticker 数据源）。"""

    rows = [
        SimpleNamespace(
            id=2, title="新推文", url="https://mp.weixin.qq.com/s/b", thumb_url=None,
            publish_time=datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc),
        ),
        SimpleNamespace(
            id=1, title="旧推文", url="https://mp.weixin.qq.com/s/a", thumb_url=None,
            publish_time=datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc),
        ),
    ]
    session = _FakeSession({GzhArticle: rows})
    _patch_db(monkeypatch, session)

    result = await ZoneService().get_home_aggregation()

    assert result.gzh_articles is not None
    assert [a.title for a in result.gzh_articles] == ["新推文", "旧推文"]


@pytest.mark.asyncio
async def test_gzh_articles_capped_at_latest_ten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """首页资讯默认展示最新 10 条：聚合查询的 limit 必须同步为 10。"""

    captured: list[Any] = []

    class _CapturingSession(_FakeSession):
        async def execute(self, stmt: Any) -> _FakeResult:
            if stmt.column_descriptions[0]["entity"] is GzhArticle:
                captured.append(stmt)
            return await super().execute(stmt)

    _patch_db(monkeypatch, _CapturingSession({}))

    await ZoneService().get_home_aggregation()

    compiled = captured[0].compile(compile_kwargs={"literal_binds": True})
    assert "LIMIT 10" in str(compiled)


@pytest.mark.asyncio
async def test_gzh_articles_absent_yields_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """从未同步过时 gzh_articles 必须为 None，前端据此整条隐藏 ticker。"""

    session = _FakeSession({})
    _patch_db(monkeypatch, session)

    result = await ZoneService().get_home_aggregation()

    assert result.gzh_articles is None
