from __future__ import annotations

import asyncpg
from redis.asyncio import Redis

from bootstrap_app.runtime import DatabaseTarget, _read_secret


class InfrastructureCheckError(RuntimeError):
    """The selected database or Redis target is unavailable or not empty."""


async def assert_empty_infrastructure(installation_dir) -> dict[str, str]:
    target = DatabaseTarget.from_installation(installation_dir)
    connection = None
    redis_client = None
    try:
        connection = await asyncpg.connect(
            host=target.host,
            port=target.port,
            user=target.user,
            password=target.password,
            database=target.database,
            timeout=10,
            command_timeout=15,
        )
        actual_database = await connection.fetchval("SELECT current_database()")
        if actual_database != target.database:
            raise InfrastructureCheckError("connected PostgreSQL database is unexpected")
        table_count = await connection.fetchval(
            """
            SELECT count(*)
            FROM pg_catalog.pg_tables
            WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
            """
        )
        if table_count != 0:
            raise InfrastructureCheckError("PostgreSQL target is not empty")

        redis_url = _read_secret(installation_dir / "secrets" / "redis_url")
        redis_client = Redis.from_url(
            redis_url,
            decode_responses=False,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        if not await redis_client.ping():
            raise InfrastructureCheckError("Redis ping failed")
        async for _key in redis_client.scan_iter(match="*", count=100):
            raise InfrastructureCheckError("Redis target is not empty")
        return {"postgresql": "empty", "redis": "empty"}
    except InfrastructureCheckError:
        raise
    except (asyncpg.PostgresError, OSError, TimeoutError) as exc:
        raise InfrastructureCheckError("PostgreSQL target check failed") from exc
    except Exception as exc:
        raise InfrastructureCheckError("Redis target check failed") from exc
    finally:
        if redis_client is not None:
            await redis_client.aclose()
        if connection is not None:
            await connection.close()
