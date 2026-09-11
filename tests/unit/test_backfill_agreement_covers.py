from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from scripts.backfill_agreement_covers import build_parser, run


def _args(**overrides: object) -> argparse.Namespace:
    values = {"run": False, "type": None, "limit": None}
    values.update(overrides)
    return argparse.Namespace(**values)


def _row(row_id: int, version: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=row_id,
        type="user_terms",
        title=f"用户服务协议 v{version}",
        content=f"<p>v{version}</p>",
        version=version,
        status="archived",
    )


def _configure_db(monkeypatch, rowcount: int) -> SimpleNamespace:
    result = SimpleNamespace(rowcount=rowcount)
    db = SimpleNamespace(
        execute=AsyncMock(return_value=result),
        commit=AsyncMock(),
    )

    @asynccontextmanager
    async def fake_db_ctx():
        yield db

    monkeypatch.setattr("scripts.backfill_agreement_covers.get_db_ctx", fake_db_ctx)
    return db


async def test_backfill_defaults_to_dry_run(monkeypatch) -> None:
    candidates = AsyncMock(return_value=[_row(1, 1)])
    generate = AsyncMock()
    monkeypatch.setattr(
        "scripts.backfill_agreement_covers._candidates", candidates
    )
    monkeypatch.setattr(
        "scripts.backfill_agreement_covers.AgreementCoverService",
        SimpleNamespace,
    )
    monkeypatch.setattr(
        "scripts.backfill_agreement_covers.UploadService",
        SimpleNamespace(delete_file_by_url=MagicMock()),
    )

    exit_code = await run(_args())

    assert exit_code == 0
    candidates.assert_awaited_once_with(_args())
    generate.assert_not_called()


async def test_backfill_run_updates_only_missing_covers(monkeypatch) -> None:
    rows = [_row(1, 1), _row(2, 2)]
    monkeypatch.setattr(
        "scripts.backfill_agreement_covers._candidates",
        AsyncMock(return_value=rows),
    )
    db = _configure_db(monkeypatch, rowcount=1)
    generate = AsyncMock(side_effect=["/api/media/a.jpg", "/api/media/b.jpg"])
    monkeypatch.setattr(
        "scripts.backfill_agreement_covers.AgreementCoverService",
        lambda: SimpleNamespace(generate=generate),
    )
    delete = MagicMock()
    monkeypatch.setattr(
        "scripts.backfill_agreement_covers.UploadService",
        SimpleNamespace(delete_file_by_url=delete),
    )

    exit_code = await run(_args(run=True, limit=2))

    assert exit_code == 0
    assert generate.await_count == 2
    assert db.execute.await_count == 2
    assert db.commit.await_count == 2
    delete.assert_not_called()


async def test_backfill_continues_after_failure_and_reports_exit_code(monkeypatch, capsys) -> None:
    rows = [_row(1, 1), _row(2, 2)]
    monkeypatch.setattr(
        "scripts.backfill_agreement_covers._candidates",
        AsyncMock(return_value=rows),
    )
    db = _configure_db(monkeypatch, rowcount=1)
    generate = AsyncMock(side_effect=[RuntimeError("browser failed"), "/api/media/b.jpg"])
    monkeypatch.setattr(
        "scripts.backfill_agreement_covers.AgreementCoverService",
        lambda: SimpleNamespace(generate=generate),
    )
    monkeypatch.setattr(
        "scripts.backfill_agreement_covers.UploadService",
        SimpleNamespace(delete_file_by_url=MagicMock()),
    )

    exit_code = await run(_args(run=True))

    assert exit_code == 1
    assert db.commit.await_count == 1
    assert "failed=1" in capsys.readouterr().out


async def test_backfill_continues_after_database_update_failure(monkeypatch, capsys) -> None:
    rows = [_row(1, 1), _row(2, 2)]
    monkeypatch.setattr(
        "scripts.backfill_agreement_covers._candidates",
        AsyncMock(return_value=rows),
    )
    db = _configure_db(monkeypatch, rowcount=1)
    db.execute = AsyncMock(
        side_effect=[RuntimeError("database unavailable"), SimpleNamespace(rowcount=1)]
    )
    generate = AsyncMock(side_effect=["/api/media/a.jpg", "/api/media/b.jpg"])
    monkeypatch.setattr(
        "scripts.backfill_agreement_covers.AgreementCoverService",
        lambda: SimpleNamespace(generate=generate),
    )
    monkeypatch.setattr(
        "scripts.backfill_agreement_covers.UploadService",
        SimpleNamespace(delete_file_by_url=MagicMock()),
    )

    exit_code = await run(_args(run=True))

    assert exit_code == 1
    assert generate.await_count == 2
    assert db.commit.await_count == 1
    assert "failed=1" in capsys.readouterr().out


async def test_backfill_deletes_orphan_when_cover_was_set_concurrently(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        "scripts.backfill_agreement_covers._candidates",
        AsyncMock(return_value=[_row(1, 1)]),
    )
    db = _configure_db(monkeypatch, rowcount=0)
    generate = AsyncMock(return_value="/api/media/a.jpg")
    monkeypatch.setattr(
        "scripts.backfill_agreement_covers.AgreementCoverService",
        lambda: SimpleNamespace(generate=generate),
    )
    delete = MagicMock()
    monkeypatch.setattr(
        "scripts.backfill_agreement_covers.UploadService",
        SimpleNamespace(delete_file_by_url=delete),
    )

    exit_code = await run(_args(run=True))

    assert exit_code == 0
    delete.assert_called_once_with("/api/media/a.jpg")
    assert "skipped=1" in capsys.readouterr().out


def test_backfill_parser_uses_dry_run_and_known_type_choices() -> None:
    parser = build_parser()
    args = parser.parse_args(["--run", "--type", "privacy", "--limit", "5"])

    assert args.run is True
    assert args.type == "privacy"
    assert args.limit == 5
