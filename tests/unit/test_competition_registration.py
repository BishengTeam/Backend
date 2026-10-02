"""竞赛报名窗口与后台配置联动的回归测试。

线上问题（2026-10-02）：
1. 用户端/管理端列表接口漏传 ``custom_fields``，导致小程序详情不显示
   后台配置的自定义报名字段，且后台编辑一次就会把配置覆盖为空。
2. 管理端更新赛事时「赛道全量替换」会把 ``competition_reg.track_id``
   置空，报名去重失效、报名列表按赛道过滤丢失数据。
3. 报名截止/赛事结束后必须拒绝报名。
"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
import csv
import io

import app.services.admin_competition as admin_competition_module
import app.services.competition as competition_module
from app.domain.certification.src.index import (
    Competition,
    CompetitionReg,
    CompetitionTrack,
)
from app.port.exceptions import BusinessException
from app.schemas.admin_competition import (
    AdminCompetitionTrackInput,
    AdminCompetitionUpdate,
)
from app.schemas.competition import (
    CompetitionRegistrationUpdateRequest,
    CompetitionSignupRequest,
)
from app.services.admin_competition import AdminCompetitionService
from app.services.competition import CompetitionService
from app.port.exceptions import NotFoundException


UTC = timezone.utc


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "_FakeResult":
        return self

    def all(self) -> list[Any]:
        return self._rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalar(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if len(self._rows) == 1 else None


class _FakeSession:
    """按查询实体分发的最小化 async session。"""

    def __init__(
        self,
        track_join_rows: list[Any] | None = None,
        reg_join_rows: list[Any] | None = None,
        regs: list[Any] | None = None,
        competitions: list[Any] | None = None,
        tracks: list[Any] | None = None,
        track_count_rows: list[Any] | None = None,
    ) -> None:
        self.track_join_rows = track_join_rows or []
        self.reg_join_rows = reg_join_rows or []
        self.regs = regs or []
        self.competitions = competitions or []
        self.tracks = tracks or []
        self.track_count_rows = track_count_rows
        self.added: list[Any] = []
        self.deleted: list[Any] = []
        self.committed = False

    @asynccontextmanager
    async def begin(self):
        yield

    async def get(self, entity: type, pk: Any) -> Any:
        rows = self.competitions if entity is Competition else []
        return next((r for r in rows if getattr(r, "id", None) == pk), None)

    async def execute(self, stmt: Any) -> _FakeResult:
        entities = [d.get("entity") for d in stmt.column_descriptions]
        if CompetitionReg in entities and CompetitionTrack in entities:
            # (报名, 赛道, 赛事) 三表联查
            return _FakeResult(self.reg_join_rows)
        if Competition in entities and CompetitionTrack in entities:
            return _FakeResult(self.track_join_rows)
        if len(entities) > 1 and entities[0] is CompetitionTrack:
            # _track_briefs 的 (track, enrolled) 聚合查询
            if self.track_count_rows is not None:
                return _FakeResult(self.track_count_rows)
            return _FakeResult([(t, 0) for t in self.tracks])
        if entities and entities[0] is CompetitionTrack:
            return _FakeResult(self.tracks)
        if entities and entities[0] is CompetitionReg:
            return _FakeResult(self.regs)
        if entities and entities[0] is Competition:
            return _FakeResult(self.competitions)
        return _FakeResult([])

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def delete(self, obj: Any) -> None:
        self.deleted.append(obj)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True

    async def refresh(self, obj: Any) -> None:
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
        start_time=now - timedelta(days=1),
        end_time=now + timedelta(days=2),
        registration_deadline=now + timedelta(days=1),
        is_active=True,
        custom_fields=[
            {"key": "student_id", "label": "学号", "type": "text",
             "required": True, "max_length": 20, "sort_order": 0},
        ],
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


def _make_reg(**overrides: Any) -> SimpleNamespace:
    defaults = dict(
        id=10,
        user_id=1,
        competition_name="测试比赛",
        school="旧学校",
        track="网络赛道",
        track_id=4,
        real_name="旧姓名",
        phone="13800000000",
        custom_field_values={"student_id": "OLD001"},
        created_at=datetime.now(UTC) - timedelta(days=1),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_list_events_returns_custom_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """用户端赛事列表必须返回 custom_fields，小程序详情据此渲染动态表单。"""

    competition = _make_competition()
    session = _FakeSession(competitions=[competition], tracks=[_make_track()])
    _patch_db(monkeypatch, competition_module, session)

    result = await CompetitionService().list_events(active_only=True)

    assert len(result) == 1
    assert result[0].custom_fields == competition.custom_fields


@pytest.mark.asyncio
async def test_admin_list_competitions_returns_custom_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """管理端列表必须返回 custom_fields，否则编辑弹窗回显为空、保存即清空。"""

    competition = _make_competition()
    session = _FakeSession(competitions=[competition], tracks=[_make_track()])
    _patch_db(monkeypatch, admin_competition_module, session)

    result = await AdminCompetitionService().list_competitions(None, 1, 20)

    assert result.items[0].custom_fields == competition.custom_fields


@pytest.mark.asyncio
async def test_update_keeps_track_id_for_existing_tracks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """携带 id 的赛道应原地更新，不能删除重建导致 competition_reg.track_id 置空。"""

    competition = _make_competition()
    track = _make_track()
    session = _FakeSession(competitions=[competition], tracks=[track])
    _patch_db(monkeypatch, admin_competition_module, session)

    payload = AdminCompetitionUpdate(
        name="测试比赛",
        tracks=[
            AdminCompetitionTrackInput(id=4, name="网络赛道（新）", max_participants=10, sort_order=0),
            AdminCompetitionTrackInput(name="安全赛道", max_participants=0, sort_order=1),
        ],
    )
    await AdminCompetitionService().update(1, payload)

    # 原赛道保留对象身份（未被删除重建），仅更新属性
    assert session.deleted == []
    assert track.name == "网络赛道（新）"
    assert track.max_participants == 10
    # 新增赛道无 id
    added_tracks = [t for t in session.added if t.__class__.__name__ == "CompetitionTrack"]
    assert len(added_tracks) == 1
    assert added_tracks[0].name == "安全赛道"


@pytest.mark.asyncio
async def test_update_deletes_removed_tracks(monkeypatch: pytest.MonkeyPatch) -> None:
    """输入中缺失的 id 才允许删除。"""

    competition = _make_competition()
    track = _make_track()
    session = _FakeSession(competitions=[competition], tracks=[track])
    _patch_db(monkeypatch, admin_competition_module, session)

    payload = AdminCompetitionUpdate(
        tracks=[AdminCompetitionTrackInput(name="全新赛道", max_participants=0, sort_order=0)],
    )
    await AdminCompetitionService().update(1, payload)

    assert session.deleted == [track]


@pytest.mark.asyncio
async def test_signup_rejects_past_registration_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """报名截止后必须拒绝报名。"""

    competition = _make_competition(
        registration_deadline=datetime.now(UTC) - timedelta(hours=1),
    )
    track = _make_track()
    session = _FakeSession(
        track_join_rows=[(track, competition)], tracks=[track],
    )
    _patch_db(monkeypatch, competition_module, session)

    with pytest.raises(BusinessException, match="报名已截止"):
        await CompetitionService().signup(
            1,
            CompetitionSignupRequest(
                track_id=4, school="测试学校", real_name="张三", phone="13800138000",
            ),
        )
    assert session.added == []


@pytest.mark.asyncio
async def test_signup_rejects_after_event_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """赛事结束后必须拒绝报名。"""

    competition = _make_competition(
        end_time=datetime.now(UTC) - timedelta(hours=1),
        registration_deadline=None,
    )
    track = _make_track()
    session = _FakeSession(
        track_join_rows=[(track, competition)], tracks=[track],
    )
    _patch_db(monkeypatch, competition_module, session)

    with pytest.raises(BusinessException, match="赛事已结束"):
        await CompetitionService().signup(
            1,
            CompetitionSignupRequest(
                track_id=4, school="测试学校", real_name="张三", phone="13800138000",
            ),
        )


@pytest.mark.asyncio
async def test_signup_succeeds_before_deadline_with_track_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """报名窗口内报名成功，且必须写入 track_id（去重与统计依赖）。"""

    competition = _make_competition()
    track = _make_track()
    session = _FakeSession(
        track_join_rows=[(track, competition)], tracks=[track],
    )
    _patch_db(monkeypatch, competition_module, session)

    reg = await CompetitionService().signup(
        1,
        CompetitionSignupRequest(
            track_id=4, school="测试学校", real_name="张三", phone="13800138000",
            custom_field_values={"student_id": "20260001"},
        ),
    )

    assert reg.track_id == 4
    assert session.committed is True


@pytest.mark.asyncio
async def test_signup_tolerates_naive_datetimes(monkeypatch: pytest.MonkeyPatch) -> None:
    """驱动返回 naive 时间时不能 500，应按 UTC 归一后正常拦截。"""

    competition = _make_competition(
        registration_deadline=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1),
    )
    track = _make_track()
    session = _FakeSession(
        track_join_rows=[(track, competition)], tracks=[track],
    )
    _patch_db(monkeypatch, competition_module, session)

    with pytest.raises(BusinessException, match="报名已截止"):
        await CompetitionService().signup(
            1,
            CompetitionSignupRequest(
                track_id=4, school="测试学校", real_name="张三", phone="13800138000",
            ),
        )


@pytest.mark.asyncio
async def test_my_registrations_marks_editable_by_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """报名截止前 editable=True，截止后 False。"""

    comp_open = _make_competition()
    comp_closed = _make_competition(
        registration_deadline=datetime.now(UTC) - timedelta(hours=1),
    )
    session = _FakeSession(
        reg_join_rows=[
            (_make_reg(id=10), _make_track(), comp_open),
            (_make_reg(id=11), _make_track(), comp_closed),
        ]
    )
    _patch_db(monkeypatch, competition_module, session)

    result = await CompetitionService().my_registrations(1)

    assert [r.id for r in result] == [10, 11]
    assert result[0].editable is True
    assert result[1].editable is False
    assert result[0].competition_id == 1
    assert result[0].custom_fields == comp_open.custom_fields


@pytest.mark.asyncio
async def test_update_registration_rejects_other_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """他人报名 ID 不可修改，且不能据此探测记录是否存在。"""

    session = _FakeSession(
        reg_join_rows=[(_make_reg(user_id=2), _make_track(), _make_competition())]
    )
    _patch_db(monkeypatch, competition_module, session)

    with pytest.raises(NotFoundException):
        await CompetitionService().update_registration(
            1,
            10,
            CompetitionRegistrationUpdateRequest(
                school="新学校", real_name="张三", phone="13800138000",
            ),
        )


@pytest.mark.asyncio
async def test_update_registration_rejects_after_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """报名截止后不可修改。"""

    competition = _make_competition(
        registration_deadline=datetime.now(UTC) - timedelta(hours=1),
    )
    session = _FakeSession(
        reg_join_rows=[(_make_reg(), _make_track(), competition)]
    )
    _patch_db(monkeypatch, competition_module, session)

    with pytest.raises(BusinessException, match="报名已截止，无法修改"):
        await CompetitionService().update_registration(
            1,
            10,
            CompetitionRegistrationUpdateRequest(
                school="新学校", real_name="张三", phone="13800138000",
            ),
        )


@pytest.mark.asyncio
async def test_update_registration_updates_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """截止前本人可改学校/姓名/手机/自定义字段，赛道不变。"""

    reg = _make_reg()
    track = _make_track()
    session = _FakeSession(reg_join_rows=[(reg, track, _make_competition())])
    _patch_db(monkeypatch, competition_module, session)

    result = await CompetitionService().update_registration(
        1,
        10,
        CompetitionRegistrationUpdateRequest(
            school="新学校",
            real_name="新姓名",
            phone="13900139000",
            custom_field_values={"student_id": "20260002"},
        ),
    )

    assert result.school == "新学校"
    assert result.real_name == "新姓名"
    assert result.phone == "13900139000"
    assert result.custom_field_values == {"student_id": "20260002"}
    assert result.track_id == 4  # 赛道不可更换
    assert session.committed is True


@pytest.mark.asyncio
async def test_admin_export_csv_dynamic_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """导出列 = 基础列 + 赛事自定义字段动态列。"""

    competition = _make_competition(
        custom_fields=[
            {"key": "student_id", "label": "学号", "type": "text",
             "required": True, "max_length": 20, "sort_order": 0},
            {"key": "skills", "label": "技能标签", "type": "checkbox",
             "required": False, "options": ["前端", "安全"], "sort_order": 1},
        ],
    )
    regs = [
        _make_reg(
            id=10, school="学校A", real_name="张三", phone="13800138000",
            custom_field_values={"student_id": "20260001", "skills": ["前端", "安全"]},
        ),
        _make_reg(
            id=11, school="学校B", real_name="李四", phone="13900139000",
            custom_field_values={"student_id": "20260002"},
        ),
    ]
    session = _FakeSession(competitions=[competition], regs=regs)
    _patch_db(monkeypatch, admin_competition_module, session)

    content = await AdminCompetitionService().export_registrations_csv(1, None)
    # BOM 是给 Excel 的，测试解析时剥掉
    rows = list(
        csv.reader(io.StringIO(content.lstrip("\ufeff"), newline=""), skipinitialspace=True)
    )

    assert rows[0][:5] == ["报名ID", "姓名", "学校", "手机号", "赛道"]
    assert rows[0][5:8] == ["学号", "技能标签", "报名时间"]
    assert rows[1] == [
        "10", "张三", "学校A", "13800138000", "网络赛道",
        "20260001", "前端,安全", regs[0].created_at.strftime("%Y-%m-%d %H:%M:%S"),
    ]
    assert rows[2][5] == "20260002"
    assert rows[2][6] == ""  # 技能标签未填
