"""Lifecycle helpers for user-level human-resources source objects.

The certification forms keep the six currently selected source object keys on
``user_realname``/``user_student``.  A submitted application copies those
objects into version-owned keys and records the source key on
``renshe_material.source_storage_key``.  This module centralises the reference
check used before deleting a source object so replacing a draft or cleaning a
batch cannot remove an object that is still needed by another version (or by a
current user profile).

There is intentionally no object-key enumeration in the application.  Only
keys observed in a mutation are candidates for deletion, and every candidate
is checked against all database references immediately before the storage
operation.  A failed delete is safe to retry and is never allowed to make a
committed profile mutation roll back.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from sqlalchemy import or_, select

from app.domain.renshe.src.index import RensheMaterial
from app.domain.user.src.index import UserRealname, UserStudent
from app.integrations.renshe_storage import RensheObjectStorage


logger = logging.getLogger(__name__)

REALNAME_SOURCE_FIELDS = (
    "id_card_front_oss",
    "id_card_back_oss",
    "avatar_oss",
)
STUDENT_SOURCE_FIELDS = (
    "student_card_oss",
    "enrollment_pdf_oss",
    "degree_cert_oss",
)
DELETED_SOURCE_SENTINEL = "deleted"


def profile_source_keys(
    realname: UserRealname | None = None,
    student: UserStudent | None = None,
) -> set[str]:
    """Return non-empty source keys from one or both profile rows."""

    keys: set[str] = set()
    if realname is not None:
        keys.update(
            value
            for field in REALNAME_SOURCE_FIELDS
            if (value := getattr(realname, field, None))
            and isinstance(value, str)
        )
    if student is not None:
        keys.update(
            value
            for field in STUDENT_SOURCE_FIELDS
            if (value := getattr(student, field, None))
            and isinstance(value, str)
        )
    # Account closure keeps a non-null sentinel in the historical
    # ``student_card_oss`` column for backwards-compatible schemas.  It is a
    # database marker, never an addressable storage object.
    return {
        value
        for value in keys
        if value != DELETED_SOURCE_SENTINEL and not value.startswith("deleted:")
    }


async def find_unreferenced_source_keys(
    db,
    candidates: Iterable[str],
    *,
    ignored_material_ids: Iterable[int] = (),
) -> set[str]:
    """Return candidate keys with no remaining DB reference.

    ``source_storage_key`` references are retained even when a material row is
    marked deleted.  The cleanup worker clears that column only after the
    corresponding object has been removed, so counting both live and deleted
    rows makes retries idempotent.  A cleanup pass can explicitly ignore the
    material rows it is retiring while keeping every other historical
    reference in the check.
    """

    keys = {
        value
        for value in candidates
        if (
            isinstance(value, str)
            and value
            and value != DELETED_SOURCE_SENTINEL
            and not value.startswith("deleted:")
        )
    }
    if not keys:
        return set()

    profile_references: set[str] = set()
    realname_columns = [getattr(UserRealname, field) for field in REALNAME_SOURCE_FIELDS]
    student_columns = [getattr(UserStudent, field) for field in STUDENT_SOURCE_FIELDS]
    for columns in (realname_columns, student_columns):
        stmt = select(*columns).where(or_(*(column.in_(keys) for column in columns)))
        for row in (await db.execute(stmt)).all():
            profile_references.update(value for value in row if value in keys)

    ignored_ids = {int(value) for value in ignored_material_ids}
    material_stmt = select(RensheMaterial.source_storage_key).where(
        RensheMaterial.source_storage_key.in_(keys)
    )
    if ignored_ids:
        material_stmt = material_stmt.where(~RensheMaterial.id.in_(ignored_ids))
    material_references = (await db.execute(material_stmt)).scalars().all()
    referenced = profile_references | {
        value for value in material_references if value in keys
    }
    return keys - referenced


async def delete_unreferenced_source_keys(
    storage: RensheObjectStorage,
    candidates: Iterable[str],
    *,
    ignored_material_ids: Iterable[int] = (),
    raise_on_error: bool = False,
) -> set[str]:
    """Best-effort deletion after a profile/application transaction commits.

    The database reference check runs in a fresh transaction.  Storage errors
    are logged by type only.  Profile mutations keep the default best-effort
    behaviour after their transaction commits; retention cleanup passes
    ``raise_on_error=True`` so an OSS failure remains observable and retryable.
    """

    from app.adapter.database import get_db_ctx

    candidate_set = set(candidates)
    ignored_ids = tuple(ignored_material_ids)
    if not candidate_set:
        return set()
    try:
        async with get_db_ctx() as db:
            orphaned = await find_unreferenced_source_keys(
                db, candidate_set, ignored_material_ids=ignored_ids
            )
    except Exception as exc:
        logger.warning(
            "renshe source reference check failed: count=%s error_type=%s",
            len(candidate_set),
            type(exc).__name__,
        )
        if raise_on_error:
            raise
        return set()
    if not orphaned:
        return set()
    try:
        await storage.delete_many(sorted(orphaned))
    except Exception as exc:
        logger.warning(
            "renshe source object cleanup failed: count=%s error_type=%s",
            len(orphaned),
            type(exc).__name__,
        )
        if raise_on_error:
            raise
        return set()
    return orphaned
