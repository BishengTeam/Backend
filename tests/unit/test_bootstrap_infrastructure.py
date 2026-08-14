from __future__ import annotations

import os

import pytest

from bootstrap_app.infrastructure import (
    InfrastructureCheckError,
    assert_empty_infrastructure,
)


def _installation(tmp_path):
    os.chmod(tmp_path, 0o700)
    installation = tmp_path / "installation"
    secrets = installation / "secrets"
    secrets.mkdir(parents=True, mode=0o700)
    runtime = installation / "runtime.env"
    runtime.write_text(
        "DB_HOST=db.example\n"
        "DB_PORT=3306\n"
        "DB_USER=wemini\n"
        "DB_NAME=wemini_app\n",
        encoding="utf-8",
    )
    runtime.chmod(0o600)
    (secrets / "postgres_password").write_text("db-secret\n", encoding="utf-8")
    (secrets / "redis_url").write_text("redis://redis.example/7\n", encoding="utf-8")
    for path in secrets.iterdir():
        path.chmod(0o600)
    return installation


class _Connection:
    def __init__(self, table_count):
        self.table_count = table_count
        self.closed = False

    async def fetchval(self, query):
        if "current_database" in query:
            return "wemini_app"
        return self.table_count

    async def close(self):
        self.closed = True


class _Redis:
    def __init__(self, keys=()):
        self.keys = keys
        self.closed = False

    async def ping(self):
        return True

    async def scan_iter(self, **_kwargs):
        for key in self.keys:
            yield key

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_empty_external_postgres_3306_and_redis_are_accepted(tmp_path, monkeypatch):
    installation = _installation(tmp_path)
    connection = _Connection(0)
    redis_client = _Redis()

    async def connect(**kwargs):
        assert kwargs["port"] == 3306
        assert kwargs["password"] == "db-secret"
        return connection

    monkeypatch.setattr("bootstrap_app.infrastructure.asyncpg.connect", connect)
    monkeypatch.setattr(
        "bootstrap_app.infrastructure.Redis.from_url",
        lambda url, **_kwargs: redis_client if url == "redis://redis.example/7" else None,
    )
    assert await assert_empty_infrastructure(installation) == {
        "postgresql": "empty",
        "redis": "empty",
    }
    assert connection.closed is True
    assert redis_client.closed is True


@pytest.mark.asyncio
async def test_nonempty_postgres_is_rejected_before_redis(tmp_path, monkeypatch):
    installation = _installation(tmp_path)

    async def connect(**_kwargs):
        return _Connection(1)

    monkeypatch.setattr("bootstrap_app.infrastructure.asyncpg.connect", connect)
    with pytest.raises(InfrastructureCheckError, match="PostgreSQL target is not empty"):
        await assert_empty_infrastructure(installation)


@pytest.mark.asyncio
async def test_nonempty_redis_namespace_is_rejected(tmp_path, monkeypatch):
    installation = _installation(tmp_path)

    async def connect(**_kwargs):
        return _Connection(0)

    monkeypatch.setattr("bootstrap_app.infrastructure.asyncpg.connect", connect)
    monkeypatch.setattr(
        "bootstrap_app.infrastructure.Redis.from_url",
        lambda *_args, **_kwargs: _Redis((b"occupied",)),
    )
    with pytest.raises(InfrastructureCheckError, match="Redis target is not empty"):
        await assert_empty_infrastructure(installation)
