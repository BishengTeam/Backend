"""为历史深信服报名补充 batch_snapshot.registration_notice。"""
import argparse
import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlalchemy import select

from app.adapter.database import get_db_ctx
from app.domain.order.src.index import Order
from app.domain.plan.src.index import Plan


async def backfill(apply: bool) -> int:
    updated = 0
    async with get_db_ctx() as db:
        orders = (await db.scalars(
            select(Order).where(
                Order.order_kind == "certification",
                Order.extra_data["business_type"].as_string() == "sangfor",
            )
        )).all()
        plans = {
            plan.id: plan
            for plan in (await db.scalars(select(Plan))).all()
        }
        for order in orders:
            extra = order.extra_data or {}
            snapshot = extra.get("batch_snapshot") or {}
            if "registration_notice" in snapshot:
                continue
            plan = plans.get(order.plan_id)
            notice = (plan.registration_notice if plan else None) or ""
            if not notice:
                continue
            updated += 1
            if not apply:
                print(f"WOULD_UPDATE order_id={order.id}")
                continue
            order.extra_data = {
                **extra,
                "batch_snapshot": {**snapshot, "registration_notice": notice},
            }
            print(f"UPDATED order_id={order.id}")
        if apply and updated:
            await db.commit()
    print(f"total={updated} applied={bool(updated) and apply}")
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description="backfill sangfor snapshot notice")
    parser.add_argument("--apply", action="store_true", help="write changes (default dry-run)")
    args = parser.parse_args()
    asyncio.run(backfill(args.apply))
    return 0


if __name__ == "__main__":
    sys.exit(main())
