"""Read-only report: ambiguous historical channels, without candidate PII."""
import asyncio
import json

from sqlalchemy import select

from app.adapter.database import get_db_ctx
from app.domain.order.src.index import Order


async def main():
    async with get_db_ctx() as db:
        rows = (await db.execute(select(Order.id, Order.order_kind, Order.price, Order.status).where(
            Order.payment_channel == "unknown").order_by(Order.id))).all()
        print(json.dumps([dict(row._mapping) for row in rows], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
