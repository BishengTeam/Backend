"""Authoritative clock helpers.

Business boundary checks use the database clock instead of an application
container's local clock. The database is the serialization point for plan and
booking state, so using its clock also avoids making decisions with a stale
application timestamp.
"""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def db_now_utc(db: AsyncSession) -> datetime:
    """Return the database's current time normalized to timezone-aware UTC."""

    value = await db.scalar(select(func.now()))
    if not isinstance(value, datetime):
        raise RuntimeError("数据库未返回有效时间")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
