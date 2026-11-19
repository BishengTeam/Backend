"""为存量已付款且审核通过的深信服订单补发报名码。"""
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
from app.models.offline_payment import OfflinePaymentAudit
from app.services.sangfor_registration_code import (
    CODE_FIELD,
    ensure_registration_code,
)

APPROVED_STATES = ("approved", "final_approved")


async def backfill(apply: bool) -> int:
    async with get_db_ctx() as db:
        refunded = select(OfflinePaymentAudit.id).where(
            OfflinePaymentAudit.order_id == Order.id,
            OfflinePaymentAudit.action == "admin.generic_refund_approved",
        ).exists()
        orders = (await db.scalars(
            select(Order).where(
                Order.order_kind == "certification",
                Order.extra_data["business_type"].as_string() == "sangfor",
                Order.status.in_(("paid", "completed")),
                Order.extra_data["registration_state"].as_string().in_(APPROVED_STATES),
                ~refunded,
                Order.extra_data[CODE_FIELD].as_string().is_(None),
            )
        )).all()

        for order in orders:
            if not apply:
                print(f"WOULD_ISSUE order_id={order.id} registration_no={order.out_trade_no}")
                continue
            code = await ensure_registration_code(db, order)
            print(f"ISSUED order_id={order.id} code={code}")
        if apply and orders:
            await db.commit()
    print(f"total={len(orders)} applied={bool(orders) and apply}")
    return len(orders)


def main() -> int:
    parser = argparse.ArgumentParser(description="backfill Sangfor registration codes")
    parser.add_argument("--apply", action="store_true", help="write codes (default dry-run)")
    args = parser.parse_args()
    asyncio.run(backfill(args.apply))
    return 0


if __name__ == "__main__":
    sys.exit(main())
