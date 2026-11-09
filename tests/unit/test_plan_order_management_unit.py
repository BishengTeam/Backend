"""PlanOrderManagementService v2 unit tests."""
import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

os.environ.setdefault("JWT_SECRET", "test-secret-key-min-32-chars-long")

from app.services.plan_order_management import PlanOrderManagementService


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value


class _FakeDb:
    def __init__(self, *results):
        self.results = list(results)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _Result(self.results.pop(0) if self.results else None)


def _order():
    return SimpleNamespace(
        id=21,
        order_kind="certification",
        product_type="H3C-NE",
        plan_id=10,
        candidate_name="张三",
        candidate_phone="13800000000",
        candidate_idcard="11010119900101001X",
        price=12800,
        status="paid",
        out_trade_no="trade-21",
        inventory_id=None,
        expires_at=None,
        closed_at=None,
        close_reason=None,
        created_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
        extra_data=None,
        attachments=None,
    )


class PlanOrderManagementServiceTests(unittest.IsolatedAsyncioTestCase):
    """v2: list_orders 接受 db 参数，不再走 get_db_ctx。"""

    async def test_list_orders_returns_plan_scoped_page(self):
        # v2 查询顺序: count → orders
        db = _FakeDb(1, [_order()])
        result = await PlanOrderManagementService().list_orders(
            db,
            plan_id=10,
            product_type="H3C-NE",
            status="paid",
            phone="13800000000",
            page=1,
            page_size=20,
        )
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["page"], 1)
        self.assertEqual(result["page_size"], 20)
        self.assertEqual(len(db.statements), 2)

    async def test_list_orders_without_product_filter(self):
        db = _FakeDb(0, [])
        result = await PlanOrderManagementService().list_orders(
            db,
            plan_id=10,
            page=1,
            page_size=20,
        )
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["items"], [])

    async def test_result_shape(self):
        """Result dict has items/total/page/page_size keys."""
        db = _FakeDb(0, [])
        result = await PlanOrderManagementService().list_orders(
            db, plan_id=1, page=2, page_size=50,
        )
        assert isinstance(result, dict)
        assert set(result.keys()) == {"items", "total", "page", "page_size"}


class PlanOrderManagementOpenAPITests(unittest.TestCase):
    """Verify v2 admin route declarations."""

    def test_admin_batch_order_routes_exist(self):
        from pathlib import Path
        source = Path("app/api/admin/plans.py").read_text("utf-8")
        assert "/batches/{batch_id}/orders" in source
        assert "PlanOrderManagementService" in source

    def test_list_orders_method_exists(self):
        assert hasattr(PlanOrderManagementService, "list_orders")
