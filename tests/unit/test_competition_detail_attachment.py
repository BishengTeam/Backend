"""赛事详情接口与在线查阅附件（PDF 转页图）的回归测试。

覆盖：
1. GET /competition/{id} 详情：海报/附件/赛道透出；未发布或不存在按 NotFound。
2. 附件 PDF 渲染：格式/大小/页数校验、页图落公开媒体、原件落私有存储
   （本地模式落 uploads/private，OSS 模式 put 私有桶）。
3. 管理端 schema/更新链路对 poster_url 与 attachments 的透传。
"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

import app.services.admin_competition as admin_competition_module
import app.services.competition as competition_module
import app.services.pdf_attachment as pdf_attachment_module
from app.domain.certification.src.index import Competition, CompetitionTrack
from app.integrations.competition_storage import CompetitionAttachmentStorage
from app.port.config import settings
from app.port.exceptions import BusinessException, NotFoundException
from app.schemas.admin_competition import AdminCompetitionUpdate
from app.schemas.competition import CompetitionAttachment, CompetitionDetail
from app.services.admin_competition import AdminCompetitionService
from app.services.competition import CompetitionService
from app.services.pdf_attachment import render_pdf_attachment

UTC = timezone.utc


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    """详情链路只需 db.get(Competition) 与 _track_briefs 聚合查询。"""

    def __init__(
        self,
        competitions: list[Any] | None = None,
        tracks: list[Any] | None = None,
    ) -> None:
        self.competitions = competitions or []
        self.tracks = tracks or []

    @asynccontextmanager
    async def begin(self):
        yield

    async def get(self, entity: type, pk: Any) -> Any:
        if entity is Competition:
            return next(
                (r for r in self.competitions if getattr(r, "id", None) == pk), None
            )
        return None

    async def execute(self, stmt: Any) -> _FakeResult:
        entities = [d.get("entity") for d in stmt.column_descriptions]
        if entities and entities[0] is CompetitionTrack:
            return _FakeResult([(t, 0) for t in self.tracks])
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
        home_cover_url=None,
        poster_url=None,
        attachments=None,
        start_time=now - timedelta(days=1),
        end_time=now + timedelta(days=2),
        registration_deadline=now + timedelta(days=1),
        is_active=True,
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


def _detail_competition(**overrides):
    now = datetime.now(UTC)
    defaults = dict(
        poster_url="/api/media/poster.jpg",
        attachments=[
            {"title": "赛事邀请函", "pages": ["/api/media/p1.jpg", "/api/media/p2.jpg"]},
        ],
        start_time=now - timedelta(days=1),
        end_time=now + timedelta(days=2),
        registration_deadline=now + timedelta(days=1),
        is_active=True,
        created_at=now - timedelta(days=3),
    )
    defaults.update(overrides)
    return _make_competition(**defaults)


# ── 详情接口 ──────────────────────────────────────────────────


async def test_get_event_detail_returns_poster_attachments_tracks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    competition = _detail_competition()
    session = _FakeSession(competitions=[competition], tracks=[_make_track()])
    _patch_db(monkeypatch, competition_module, session)

    detail = await CompetitionService().get_event_detail(1)

    assert isinstance(detail, CompetitionDetail)
    assert detail.poster_url == "/api/media/poster.jpg"
    assert detail.attachments[0].title == "赛事邀请函"
    assert detail.attachments[0].pages == ["/api/media/p1.jpg", "/api/media/p2.jpg"]
    assert detail.tracks[0].name == "网络赛道"
    assert detail.custom_fields is not None


async def test_get_event_detail_rejects_inactive_or_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inactive = _detail_competition(is_active=False)
    session = _FakeSession(competitions=[inactive])
    _patch_db(monkeypatch, competition_module, session)
    with pytest.raises(NotFoundException):
        await CompetitionService().get_event_detail(1)

    empty = _FakeSession(competitions=[])
    _patch_db(monkeypatch, competition_module, empty)
    with pytest.raises(NotFoundException):
        await CompetitionService().get_event_detail(999)


# ── 附件 PDF 渲染 ────────────────────────────────────────────


def _build_pdf(page_count: int = 1) -> bytes:
    """构造带正确 xref 的最小 PDF（pypdfium2 可解析）。"""
    objs: list[bytes] = [b"<</Type/Catalog/Pages 2 0 R>>"]
    kids = " ".join(f"{3 + i} 0 R" for i in range(page_count))
    objs.append(f"<</Type/Pages/Kids[{kids}]/Count {page_count}>>".encode())
    for _ in range(page_count):
        objs.append(b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 100]>>")

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer<</Size {len(objs) + 1}/Root 1 0 R>>\n"
        f"startxref\n{xref_pos}\n%%EOF"
    ).encode()
    return bytes(out)


def _fake_save_bytes(
    monkeypatch: pytest.MonkeyPatch, urls: list[str]
) -> None:
    def _save(content: bytes, extension: str = "") -> dict:
        urls.append(f"/api/media/fake{len(urls) + 1}.jpg")
        return {"url": urls[-1], "size": len(content)}

    monkeypatch.setattr(pdf_attachment_module.UploadService, "save_bytes", _save)


async def test_render_pdf_rejects_non_pdf() -> None:
    with pytest.raises(BusinessException, match="仅支持上传 PDF"):
        await render_pdf_attachment(b"plain text, not a pdf", "x.pdf")


async def test_render_pdf_rejects_oversize() -> None:
    with pytest.raises(BusinessException, match="20MB"):
        await render_pdf_attachment(
            b"%PDF-" + b"0" * (pdf_attachment_module.MAX_PDF_SIZE + 1), "x.pdf"
        )


async def test_render_pdf_rejects_too_many_pages() -> None:
    with pytest.raises(BusinessException, match="最多 30 页"):
        await render_pdf_attachment(
            _build_pdf(pdf_attachment_module.MAX_PDF_PAGES + 1), "x.pdf"
        )


async def test_render_pdf_writes_public_pages_and_private_original(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    urls: list[str] = []
    _fake_save_bytes(monkeypatch, urls)
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))

    result = await render_pdf_attachment(_build_pdf(page_count=2), "2026 AI安全竞赛邀请函.pdf")

    assert result["title"] == "2026 AI安全竞赛邀请函"
    assert result["page_count"] == 2
    assert result["pages"] == ["/api/media/fake1.jpg", "/api/media/fake2.jpg"]

    # 原件只落 uploads/private，绝不进公开媒体目录
    private_files = list((tmp_path / "private" / "competition-attachments").iterdir())
    assert len(private_files) == 1
    assert private_files[0].read_bytes().startswith(b"%PDF")


async def test_render_pdf_puts_original_to_private_oss(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    class _FakeBucket:
        def __init__(self) -> None:
            self.calls: list[tuple[str, bytes, dict]] = []

        def put_object(self, key: str, data: bytes, headers: dict | None = None):
            self.calls.append((key, data, headers or {}))
            return SimpleNamespace(status=200)

    bucket = _FakeBucket()
    _fake_save_bytes(monkeypatch, [])
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "RENSHE_STORAGE_TYPE", "aliyun_oss")
    monkeypatch.setattr(settings, "ALIYUN_OSS_ENDPOINT", "https://oss.example.com")
    monkeypatch.setattr(settings, "ALIYUN_OSS_BUCKET", "materials-test")
    monkeypatch.setattr(settings, "ALIYUN_OSS_ACCESS_KEY_ID", "id")
    monkeypatch.setattr(settings, "ALIYUN_OSS_ACCESS_KEY_SECRET", "secret")
    monkeypatch.setattr(CompetitionAttachmentStorage, "_bucket", lambda self: bucket)

    result = await render_pdf_attachment(_build_pdf(page_count=1), "邀请函.pdf")

    assert result["page_count"] == 1
    assert len(bucket.calls) == 1
    key, data, headers = bucket.calls[0]
    assert key.startswith("competition-attachments/") and key.endswith(".pdf")
    assert data.startswith(b"%PDF")
    assert headers == {"Content-Type": "application/pdf"}
    # OSS 模式不落本地盘
    assert not (tmp_path / "private").exists()


# ── 管理端 schema / 更新链路 ─────────────────────────────────


def test_admin_update_schema_carries_poster_and_attachments() -> None:
    payload = AdminCompetitionUpdate(
        poster_url="/api/media/poster.jpg",
        attachments=[{"title": "赛事大纲", "pages": ["/api/media/s1.jpg"]}],
    )
    data = payload.model_dump(exclude_unset=True)
    assert data["poster_url"] == "/api/media/poster.jpg"
    assert data["attachments"] == [
        {"title": "赛事大纲", "pages": ["/api/media/s1.jpg"]}
    ]
    # title 必填，缺省应被校验拦截
    with pytest.raises(Exception):
        CompetitionAttachment.model_validate({"pages": ["/api/media/x.jpg"]})


async def test_admin_update_persists_poster_and_attachments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    competition = _detail_competition(poster_url=None, attachments=[])
    session = _FakeSession(competitions=[competition], tracks=[_make_track()])
    _patch_db(monkeypatch, admin_competition_module, session)

    item = await AdminCompetitionService().update(
        1,
        AdminCompetitionUpdate(
            poster_url="/api/media/new-poster.jpg",
            attachments=[{"title": "邀请函", "pages": ["/api/media/a.jpg"]}],
        ),
    )

    assert competition.poster_url == "/api/media/new-poster.jpg"
    assert competition.attachments == [
        {"title": "邀请函", "pages": ["/api/media/a.jpg"]}
    ]
    assert item.poster_url == "/api/media/new-poster.jpg"
    assert item.attachments[0].title == "邀请函"
