"""Versioned, idempotent production-only baseline seed.

Unlike ``seed_all.py`` this command has no test-data or destructive mode.  It
creates only the small configuration set required for an empty installation
and refuses to silently overwrite a conflicting operator-created value.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import select, text


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.adapter.database import async_session_factory
from app.domain.certification.src.index import Certification
from app.domain.order.src.index import PriceConfig
from app.domain.user.src.index import AdminUser


PRODUCTION_SEED_VERSION = "2026.08.21.1"

# ``crs001`` intentionally removes the old fake course dataset and rebuilds
# the domain.  Production bootstrap does not invent course categories or
# courses; it only proves that the migrated schema required by operators is
# present before the installation can advance to runtime startup.
COURSE_REQUIRED_TABLES = (
    "course",
    "quiz_course_library_binding",
    "course_category",
    "course_chapter",
    "course_upload",
    "course_enrollment",
    "user_chapter_progress",
    "quiz_library_entitlement",
    "course_audit_log",
    "course_entitlement_job",
    "course_entitlement_job_item",
)

CERTIFICATIONS = (
    {
        "name": "h3c_ne",
        "chinese_name": "H3C 网络工程师",
        "code": "H3C-NE",
        "vendor": "H3C",
        "requires_xuexin": False,
        "pay_first": True,
    },
    {
        "name": "sangfor_cse",
        "chinese_name": "深信服网络安全工程师",
        "code": "SF-CSE",
        "vendor": "深信服",
        "requires_xuexin": False,
        "pay_first": True,
    },
    {
        "name": "nisp_1",
        "chinese_name": "NISP 一级",
        "code": "NISP-1",
        "vendor": "NISP",
        "requires_xuexin": False,
        "pay_first": True,
    },
    {
        "name": "rs_zy",
        "chinese_name": "人社职业技能等级认定",
        "code": "RS-ZY",
        "vendor": "人社",
        "requires_xuexin": True,
        "pay_first": False,
    },
)

PRICE_CONFIGS = (
    ("H3C-NE", "student", 380000),
    ("H3C-NE", "normal", 480000),
    ("SF-CSE", "student", 59800),
    ("SF-CSE", "normal", 89800),
    ("NISP-1", "student", 69800),
    ("NISP-1", "normal", 69800),
    # RS-ZY registration orders take their authoritative price snapshot from
    # the batch.  These legacy price rows remain zero and must never override
    # the batch price.
    ("RS-ZY", "student", 0),
    ("RS-ZY", "normal", 0),
)


def _certification_conflicts(existing: Certification, expected: dict) -> bool:
    fields = (
        "name",
        "chinese_name",
        "code",
        "vendor",
        "requires_xuexin",
        "pay_first",
    )
    return any(getattr(existing, field) != expected[field] for field in fields)


async def _assert_course_domain_is_migrated(db) -> None:
    connection = await db.connection()
    missing = []
    for table_name in COURSE_REQUIRED_TABLES:
        exists = await connection.scalar(
            text("SELECT to_regclass(:qualified_name) IS NOT NULL"),
            {"qualified_name": f"public.{table_name}"},
        )
        if not exists:
            missing.append(table_name)
    if missing:
        raise RuntimeError(
            "production seed requires the migrated course domain; missing tables: "
            + ", ".join(missing)
        )


async def main() -> None:
    created_certifications = 0
    created_prices = 0
    async with async_session_factory() as db:
        async with db.begin():
            await _assert_course_domain_is_migrated(db)
            connection = await db.connection()
            administrator = (
                await connection.execute(
                    text(
                        "SELECT count(*) AS total, min(id) AS id "
                        "FROM admin_user WHERE role = 'super_admin' AND is_active"
                    )
                )
            ).one()
            if administrator.total != 1 or administrator.id is None:
                raise RuntimeError(
                    "production seed requires exactly one active initial super administrator"
                )
            super_admin_id = int(administrator.id)

            invalid_roles = await connection.scalar(
                text(
                    "SELECT count(*) FROM admin_user "
                    "WHERE role NOT IN ('super_admin', 'quiz_admin')"
                )
            )
            if invalid_roles:
                raise RuntimeError(
                    "production seed found an unsupported administrator role"
                )

            for expected in CERTIFICATIONS:
                existing = await db.scalar(
                    select(Certification).where(Certification.code == expected["code"])
                )
                if existing is None:
                    db.add(Certification(**expected, is_active=True))
                    created_certifications += 1
                    continue
                if _certification_conflicts(existing, expected) or not existing.is_active:
                    raise RuntimeError(
                        f"production certification conflicts with baseline: {expected['code']}"
                    )

            for product_type, user_type, price in PRICE_CONFIGS:
                rows = (
                    await db.execute(
                        select(PriceConfig).where(
                            PriceConfig.product_type == product_type,
                            PriceConfig.user_type == user_type,
                            PriceConfig.is_active.is_(True),
                        )
                    )
                ).scalars().all()
                if not rows:
                    db.add(
                        PriceConfig(
                            product_type=product_type,
                            user_type=user_type,
                            price=price,
                            is_active=True,
                        )
                    )
                    created_prices += 1
                    continue
                if len(rows) != 1 or rows[0].price != price:
                    raise RuntimeError(
                        "production price conflicts with baseline: "
                        f"{product_type}/{user_type}"
                    )

    print(
        json.dumps(
            {
                "status": "ok",
                "version": PRODUCTION_SEED_VERSION,
                "course_domain_ready": True,
                "created_certifications": created_certifications,
                "created_prices": created_prices,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError as exc:
        print(
            json.dumps(
                {
                    "status": "refused",
                    "version": PRODUCTION_SEED_VERSION,
                    "reason": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
