#!/usr/bin/env python3
"""一次性补建：派生 H3C 批次缺失的 Inventory 记录。

用法:
  # 第一步：只读审计（列出缺失清单，不写任何数据）
  python scripts/backfill_h3c_inventory.py

  # 第二步：确认后执行
  python scripts/backfill_h3c_inventory.py --execute
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.adapter.database import async_session_factory
from app.domain.h3c.src.index import H3cExamBatch
from app.domain.plan.src.index import Plan, PlanProduct
from app.models.cert_product import CertProduct
from app.models.inventory import Inventory, InventoryRecord


async def find_missing(db) -> list[dict]:
    """查所有 H3cExamBatch 无对应 Inventory 的行。"""
    batches = (await db.execute(
        select(H3cExamBatch, PlanProduct, Plan, CertProduct)
        .join(PlanProduct, PlanProduct.id == H3cExamBatch.plan_product_id)
        .join(Plan, Plan.id == PlanProduct.plan_id)
        .join(CertProduct, CertProduct.id == PlanProduct.product_id)
        .order_by(H3cExamBatch.id)
    )).all()

    missing = []
    for batch, pp, plan, cert in batches:
        inv = await db.scalar(
            select(Inventory).where(
                Inventory.inventory_type == "h3c_batch",
                Inventory.ref_code == f"h3c-batch-{batch.id}",
            )
        )
        if inv is None:
            missing.append({
                "batch_id": batch.id,
                "plan_name": plan.name,
                "product_name": cert.name,
                "capacity": pp.capacity,
            })
    return missing


async def backfill(db, items: list[dict]) -> tuple[int, int]:
    """逐条补建 Inventory + InventoryRecord。返回 (created, skipped)。"""
    created = skipped = 0
    for item in items:
        capacity = item["capacity"]
        if capacity <= 0:
            print(f"  ⚠️  跳过 batch {item['batch_id']}（capacity=0，需人工指定）")
            skipped += 1
            continue

        inv = Inventory(
            inventory_type="h3c_batch",
            ref_code=f"h3c-batch-{item['batch_id']}",
            total_quota=capacity,
            available_quota=capacity,
            locked_quota=0,
            sold_quota=0,
            is_active=True,
        )
        db.add(inv)
        await db.flush()

        db.add(InventoryRecord(
            inventory_id=inv.id,
            action="initialize",
            quantity=capacity,
            after_total_quota=capacity,
            after_available_quota=capacity,
            reason="backfill_missing_inventory_20261110",
        ))
        created += 1
    await db.commit()
    return created, skipped


async def main():
    parser = argparse.ArgumentParser(description="补建派生 H3C 批次缺失的 Inventory")
    parser.add_argument("--execute", action="store_true", help="实际写入（缺省仅审计）")
    args = parser.parse_args()

    async with async_session_factory() as db:
        items = await find_missing(db)

        if not items:
            print("✅ 无缺失 Inventory，无需补建")
            return 0

        print(f"发现 {len(items)} 个缺失 Inventory 的 H3C 批次：\n")
        print(f"{'批次ID':<8} {'计划名':<24} {'产品':<16} {'容量':<6}")
        print("-" * 60)
        for item in items:
            print(f"{item['batch_id']:<8} {item['plan_name']:<24} {item['product_name']:<16} {item['capacity']:<6}")

        if not args.execute:
            print(f"\n📋 审计模式：以上 {len(items)} 条待补建。")
            print("   确认后运行: python scripts/backfill_h3c_inventory.py --execute")
            return 0

        created, skipped = await backfill(db, items)
        print(f"\n✅ 已补建 {created} 条 Inventory + InventoryRecord")
        if skipped:
            print(f"⚠️  跳过 {skipped} 条（capacity=0，需人工指定）")


if __name__ == "__main__":
    asyncio.run(main())
