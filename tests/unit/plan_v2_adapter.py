"""Plan v2 适配层：桥接旧测试调用到新批次 API。

旧 PlanService 方法签名已变更，此模块提供兼容入口。
使用前提：测试 fixture 已 monkeypatch app.services.plan.get_db_ctx。
"""
from types import SimpleNamespace


class PlanServiceAdapter:
    """桥接旧测试的 PlanService 调用到 v2 API。"""

    async def list_plans(self, code: str):
        from app.services.plan import get_db_ctx as _ctx
        from app.services.plan import PlanService
        async with _ctx() as db:
            return await PlanService.list_published_batches_for_product(db, code)

    async def create_plan(self, code: str, payload, admin_id=None):
        from app.services.plan import get_db_ctx as _ctx
        from app.services.plan import PlanService
        from app.schemas.plan import BatchCreate, PlanProductCreate, TimeSegmentCreate
        from app.models.cert_product import CertProduct
        from sqlalchemy import select
        from datetime import time as _time
        async with _ctx() as db:
            cert = await db.scalar(select(CertProduct).where(CertProduct.code == code))
            data = BatchCreate(
                name=payload.name,
                apply_start=getattr(payload, "apply_start", None),
                apply_end=getattr(payload, "apply_end", None),
                exam_date=getattr(payload, "exam_date", None),
                exam_location=getattr(payload, "exam_location", None),
                description=getattr(payload, "description", None),
                contact_name=getattr(payload, "contact_name", None),
                contact_phone=getattr(payload, "contact_phone", None),
                sort_order=getattr(payload, "sort_order", 0),
            )
            if cert is not None:
                data.products = [PlanProductCreate(product_id=cert.id, capacity=getattr(payload, "capacity", 0) or 0)]
            data.time_segments = [TimeSegmentCreate(label="上午场", start_time=_time(9,0), end_time=_time(12,0))]
            return await PlanService.create_batch(db, data)

    async def update_plan(self, plan_id, payload, code=None, admin_id=None):
        from app.services.plan import get_db_ctx as _ctx
        from app.services.plan import PlanService
        from app.schemas.plan import BatchUpdate
        async with _ctx() as db:
            update_data = {}
            for field in ("name", "apply_start", "apply_end", "exam_date", "exam_location",
                          "description", "contact_name", "contact_phone", "sort_order"):
                val = getattr(payload, field, None)
                if val is not None:
                    update_data[field] = val
            return await PlanService.update_batch(db, plan_id, BatchUpdate(**update_data))

    async def publish_plan(self, plan_id, code=None, admin_id=None):
        from app.services.plan import get_db_ctx as _ctx
        from app.services.plan import PlanService
        async with _ctx() as db:
            return await PlanService.publish(db, plan_id)

    async def close_registration(self, plan_id, code=None):
        from app.services.plan import get_db_ctx as _ctx
        from app.services.plan import PlanService
        async with _ctx() as db:
            return await PlanService.close_registration(db, plan_id)

    async def list_renshe_plans(self, public=False):
        from app.services.plan import get_db_ctx as _ctx
        from app.domain.plan.src.index import Plan
        from sqlalchemy import select as _s
        async with _ctx() as db:
            stmt = _s(Plan).where(Plan.status == "published") if public else _s(Plan)
            plans = (await db.execute(stmt.order_by(Plan.id))).scalars().all()
            return [SimpleNamespace(id=p.id, name=p.name, status=p.status) for p in plans]
