"""赛事绑定练习题库（quiz_library_id）的单元回归。

覆盖：
1. 详情接口：已发布且开启 V2 用户入口的绑定题库透出 brief；
   未绑定/题库不存在/未发布/未开入口一律不透出（小程序入口隐藏）。
2. 管理端：update 携带 quiz_library_id 时校验目标题库
   （不存在或 archived/deleted 拒绝），合法值落库；None 解绑。
"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

import app.services.admin_competition as admin_competition_module
import app.services.competition as competition_module
from app.domain.certification.src.index import Competition, CompetitionTrack
from app.domain.community.src.index import QuizLibrary
from app.port.exceptions import BusinessException
from app.schemas.admin_competition import AdminCompetitionUpdate
from app.schemas.competition import CompetitionDetail
from app.services.admin_competition import AdminCompetitionService
from app.services.competition import CompetitionService

UTC = timezone.utc


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows

    def scalars(self) -> Any:
        return SimpleNamespace(all=lambda: self._rows)


class _FakeSession:
    """详情/更新链路只需 db.get(Competition|QuizLibrary) 与赛道聚合查询。"""

    def __init__(
        self,
        competitions: list[Any] | None = None,
        tracks: list[Any] | None = None,
        libraries: list[Any] | None = None,
    ) -> None:
        self.competitions = competitions or []
        self.tracks = tracks or []
        self.libraries = libraries or []

    @asynccontextmanager
    async def begin(self):
        yield

    async def get(self, entity: type, pk: Any) -> Any:
        if entity is Competition:
            return next(
                (r for r in self.competitions if getattr(r, "id", None) == pk), None
            )
        if entity is QuizLibrary:
            return next(
                (r for r in self.libraries if getattr(r, "id", None) == pk), None
            )
        return None

    async def execute(self, stmt: Any) -> _FakeResult:
        entities = [d.get("entity") for d in stmt.column_descriptions]
        if entities and entities[0] is CompetitionTrack:
            return _FakeResult([(t, 0) for t in self.tracks])
        if entities and entities[0] is QuizLibrary:
            # 管理端列表批量取名（简化：假会话直接返回全部假题库）
            return _FakeResult(list(self.libraries))
        return _FakeResult([])

    async def flush(self) -> None:
        return None


def _patch_db(monkeypatch: pytest.MonkeyPatch, module: Any, session: _FakeSession) -> None:
    @asynccontextmanager
    async def _ctx():
        yield session

    monkeypatch.setattr(module, "get_db_ctx", _ctx)


def _make_competition(**overrides: Any) -> SimpleNamespace:
    now = datetime.now(UTC)
    defaults = dict(
        id=1,
        name="测试比赛",
        description="desc",
        cover_url=None,
        poster_url=None,
        attachments=[],
        start_time=now - timedelta(days=1),
        end_time=now + timedelta(days=2),
        registration_deadline=now + timedelta(days=1),
        is_active=True,
        show_schedule=True,
        show_signup=True,
        quiz_library_id=None,
        custom_fields=[],
        created_at=now - timedelta(days=3),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_track(**overrides: Any) -> SimpleNamespace:
    defaults = dict(
        id=4,
        competition_id=1,
        name="网络赛道",
        max_participants=0,
        sort_order=0,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_library(**overrides: Any) -> SimpleNamespace:
    defaults = dict(
        id=9,
        name="SCSA-T 练习题库",
        cover_url="/api/media/quiz.jpg",
        access_mode="free",
        vendor_tag="none",
        status="published",
        v2_enabled=True,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ── 详情接口 ──────────────────────────────────────────────────


async def test_detail_returns_bound_quiz_library(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    competition = _make_competition(quiz_library_id=9)
    library = _make_library()
    session = _FakeSession(
        competitions=[competition], tracks=[_make_track()], libraries=[library]
    )
    _patch_db(monkeypatch, competition_module, session)

    detail = await CompetitionService().get_event_detail(1)

    assert isinstance(detail, CompetitionDetail)
    assert detail.quiz_library is not None
    assert detail.quiz_library.id == 9
    assert detail.quiz_library.name == "SCSA-T 练习题库"
    assert detail.quiz_library.access_mode == "free"


async def test_detail_hides_unpublished_or_hidden_library(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未发布 / 未开 V2 入口 / 题库已被删 / 未绑定 → 一律不透出。"""
    cases = [
        _make_library(status="draft"),
        _make_library(status="suspended"),
        _make_library(v2_enabled=False),
    ]
    for library in cases:
        competition = _make_competition(quiz_library_id=9)
        session = _FakeSession(
            competitions=[competition], tracks=[_make_track()], libraries=[library]
        )
        _patch_db(monkeypatch, competition_module, session)
        detail = await CompetitionService().get_event_detail(1)
        assert detail.quiz_library is None, f"status={library.status} 不应透出"

    # 指向不存在的题库（如已被物理清除）同样不透出
    competition = _make_competition(quiz_library_id=9)
    session = _FakeSession(competitions=[competition], tracks=[_make_track()])
    _patch_db(monkeypatch, competition_module, session)
    detail = await CompetitionService().get_event_detail(1)
    assert detail.quiz_library is None

    # 未绑定：字段为 None
    session = _FakeSession(competitions=[_make_competition()], tracks=[_make_track()])
    _patch_db(monkeypatch, competition_module, session)
    detail = await CompetitionService().get_event_detail(1)
    assert detail.quiz_library is None


# ── 管理端更新链路 ────────────────────────────────────────────


async def test_admin_update_binds_quiz_library(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    competition = _make_competition()
    library = _make_library()
    session = _FakeSession(
        competitions=[competition], tracks=[_make_track()], libraries=[library]
    )
    _patch_db(monkeypatch, admin_competition_module, session)

    item = await AdminCompetitionService().update(
        1, AdminCompetitionUpdate(quiz_library_id=9)
    )

    assert competition.quiz_library_id == 9
    assert item.quiz_library_id == 9
    assert item.quiz_library_name == "SCSA-T 练习题库"


async def test_admin_update_rejects_invalid_quiz_library(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    competition = _make_competition()
    archived = _make_library(status="archived")
    session = _FakeSession(
        competitions=[competition], tracks=[_make_track()], libraries=[archived]
    )
    _patch_db(monkeypatch, admin_competition_module, session)

    with pytest.raises(BusinessException, match="题库不存在或不可绑定"):
        await AdminCompetitionService().update(
            1, AdminCompetitionUpdate(quiz_library_id=9)
        )
    assert competition.quiz_library_id is None


async def test_admin_update_unbinds_quiz_library(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    competition = _make_competition(quiz_library_id=9)
    library = _make_library()
    session = _FakeSession(
        competitions=[competition], tracks=[_make_track()], libraries=[library]
    )
    _patch_db(monkeypatch, admin_competition_module, session)

    item = await AdminCompetitionService().update(
        1, AdminCompetitionUpdate(quiz_library_id=None)
    )

    assert competition.quiz_library_id is None
    assert item.quiz_library_id is None
    assert item.quiz_library_name is None
