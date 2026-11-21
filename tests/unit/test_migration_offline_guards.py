import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _offline_downgrade_sql() -> str:
    env = {
        **os.environ,
        "DATABASE_URL_SYNC": "postgresql://user:pass@127.0.0.1:5432/wemini_app_test",
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "--sql", "exam009:exam007"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def test_exam008_offline_downgrade_guards_batch_attachments(monkeypatch):
    monkeypatch.delenv("ALLOW_DESTRUCTIVE_EXAM_DOWNGRADE", raising=False)
    sql = _offline_downgrade_sql()

    assert "RAISE EXCEPTION" in sql
    assert "plan_id IS NOT NULL" in sql


def test_exam008_offline_downgrade_can_be_forced(monkeypatch):
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_EXAM_DOWNGRADE", "1")
    sql = _offline_downgrade_sql()

    assert "RAISE EXCEPTION" not in sql
