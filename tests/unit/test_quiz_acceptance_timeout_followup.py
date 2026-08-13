from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from scripts.postgres_backup import DatabaseTarget
from scripts.quiz_acceptance_runner import AcceptanceError
from scripts.quiz_acceptance_timeout_followup import check_worker_settled_exam


class _Cursor:
    def __init__(self, row) -> None:
        self.row = row
        self.query = ""
        self.params = None

    def execute(self, query, params=None) -> None:
        self.query = query
        self.params = params

    def fetchone(self):
        if "transaction_read_only" in self.query:
            return ("on",)
        if "FROM quiz_exam" in self.query:
            return self.row
        raise AssertionError(self.query)

    def close(self) -> None:
        return None


class _Connection:
    def __init__(self, row) -> None:
        self.row = row
        self.session = None
        self.closed = False

    def set_session(self, *, readonly: bool, autocommit: bool) -> None:
        self.session = (readonly, autocommit)

    def cursor(self):
        return _Cursor(self.row)

    def close(self) -> None:
        self.closed = True


def _target() -> DatabaseTarget:
    return DatabaseTarget(
        host="db.test",
        port=5432,
        user="acceptance",
        password="secret",
        database="wemini_app_acceptance",
    )


def test_worker_settlement_probe_is_read_only_and_requires_consistent_timeout() -> None:
    deadline = datetime.now(timezone.utc) - timedelta(minutes=1)
    connection = _Connection(
        ("timed_out", deadline, deadline + timedelta(seconds=2), None, None, 10, 0, 0, 10)
    )

    report = check_worker_settled_exam(
        _target(),
        exam_id=42,
        connector=lambda **_kwargs: connection,
    )

    assert report == {"exam_id": 42, "status": "timed_out", "read_only": True}
    assert connection.session == (True, True)
    assert connection.closed is True


def test_worker_settlement_probe_rejects_pending_exam() -> None:
    deadline = datetime.now(timezone.utc) - timedelta(minutes=1)
    connection = _Connection(
        ("in_progress", deadline, None, None, None, 10, None, None, None)
    )

    with pytest.raises(AcceptanceError, match="independent Worker"):
        check_worker_settled_exam(
            _target(),
            exam_id=42,
            connector=lambda **_kwargs: connection,
        )

    assert connection.closed is True
