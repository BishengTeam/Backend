"""Read-only pre/post-migration audit. Prints IDs and counts, never candidate PII.

Run from Backend: python -m scripts.audit_renshe_projects
No rows are written, reassigned, or removed by this command.
"""

import asyncio
import json

from sqlalchemy import inspect, text

from app.adapter.database import get_db_ctx


async def audit() -> dict:
    async with get_db_ctx() as db:
        connection = await db.connection()
        columns = await connection.run_sync(
            lambda conn: {
                column["name"] for column in inspect(conn).get_columns("plan")
            }
        )
        config_column = (
            "p.renshe_config_id" if "renshe_config_id" in columns else "NULL"
        )
        products = (
            (
                await db.execute(
                    text(
                        "SELECT id, code, chinese_name FROM cert_product WHERE type='renshe' ORDER BY id"
                    )
                )
            )
            .mappings()
            .all()
        )
        plans = (
            (
                await db.execute(
                    text(f"""
            SELECT p.id, p.product_type, p.occupation_name, p.occupation_code, p.skill_level, p.exam_type, p.application_type,
                   cp.id AS project_id, cp.type AS business_type, {config_column} AS config_id
            FROM plan p LEFT JOIN cert_product cp ON cp.code=p.product_type
            WHERE cp.type='renshe' OR p.product_type='RS-ZY'
               OR EXISTS (SELECT 1 FROM renshe_application a WHERE a.plan_id=p.id)
            ORDER BY p.id
        """)
                )
            )
            .mappings()
            .all()
        )
        bad_orders = (
            (
                await db.execute(
                    text("""
            SELECT o.id FROM "order" o JOIN renshe_application a ON a.id=o.application_id
            JOIN plan p ON p.id=a.plan_id
            WHERE o.user_id<>a.user_id OR o.plan_id IS NULL OR o.plan_id<>a.plan_id OR o.product_type<>p.product_type
        """)
                )
            )
            .scalars()
            .all()
        )
        bad_exports = (
            (
                await db.execute(
                    text("""
            SELECT i.id FROM renshe_export_item i
            JOIN renshe_application_version v ON v.id=i.version_id
            JOIN renshe_application a ON a.id=i.application_id JOIN renshe_export_job j ON j.id=i.job_id
            WHERE v.application_id<>i.application_id OR a.plan_id<>j.plan_id
        """)
                )
            )
            .scalars()
            .all()
        )
        bad_refunds = (
            (
                await db.execute(
                    text("""
            SELECT r.id FROM renshe_refund_request r JOIN "order" o ON o.id=r.order_id
            JOIN renshe_application a ON a.id=r.application_id
            WHERE o.application_id IS NULL OR o.application_id<>a.id OR o.user_id<>r.user_id OR a.user_id<>r.user_id
               OR o.plan_id IS NULL OR o.plan_id<>a.plan_id OR r.amount_cents<>o.price
        """)
                )
            )
            .scalars()
            .all()
        )
        expected = {
            "occupation_name": "信息安全管理员",
            "occupation_code": "4-04-04-02-网络与信息安全管理员-信息安全管理员-中级工",
            "skill_level": "中级工",
            "exam_type": "新考",
            "application_type": "学历型",
        }
        confirmed = [
            row["id"]
            for row in plans
            if row["product_type"] == "RS-ZY"
            and row["business_type"] == "renshe"
            and all(row[key] == value for key, value in expected.items())
        ]
        return {
            "projects": [dict(item) for item in products],
            "plan_count": len(plans),
            "confirmed_legacy_plan_ids": confirmed,
            "versioned_plan_ids": [
                row["id"] for row in plans if row["config_id"] is not None
            ],
            "manual_reconciliation_plan_ids": [
                row["id"]
                for row in plans
                if row["id"] not in confirmed and row["config_id"] is None
            ],
            "inconsistent_order_ids": list(bad_orders),
            "inconsistent_export_item_ids": list(bad_exports),
            "inconsistent_refund_ids": list(bad_refunds),
        }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(audit()), ensure_ascii=False, indent=2))
