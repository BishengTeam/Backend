import os
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch


os.environ.setdefault("JWT_SECRET", "test-secret-key-min-32-chars-long")

import app.services.plan_order_management as service_module
from app.port.exceptions import NotFoundException
from app.services.plan_order_management import PlanOrderManagementService


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar(self):
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
        return _Result(self.results.pop(0))


def _db_context(db):
    @asynccontextmanager
    async def context():
        yield db

    return context


def _plan():
    return SimpleNamespace(id=10, product_type="H3C-NE")


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


def _review():
    return SimpleNamespace(
        id=31,
        target_type="order",
        target_id=21,
        reviewer_id=1,
        action="approve",
        comment=None,
        created_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
    )


class PlanOrderManagementServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_orders_returns_only_plan_scoped_page(self):
        db = _FakeDb(_plan(), 1, [_order()])
        with patch.object(service_module, "get_db_ctx", _db_context(db)):
            result = await PlanOrderManagementService().list_orders(
                product_type="H3C-NE",
                plan_id=10,
                status="paid",
                phone="13800000000",
                page=1,
                page_size=20,
            )

        self.assertEqual(result.total, 1)
        self.assertEqual(result.items[0].id, 21)
        self.assertEqual(result.items[0].plan_id, 10)
        self.assertEqual(len(db.statements), 3)

    async def test_list_approvals_uses_order_reviews_for_plan(self):
        db = _FakeDb(_plan(), 1, [_review()])
        with patch.object(service_module, "get_db_ctx", _db_context(db)):
            result = await PlanOrderManagementService().list_approvals(
                product_type="H3C-NE",
                plan_id=10,
                action="approve",
                page=1,
                page_size=20,
            )

        self.assertEqual(result.total, 1)
        self.assertEqual(result.items[0].target_type, "order")
        self.assertEqual(result.items[0].target_id, 21)
        query = str(db.statements[-1])
        self.assertIn("JOIN", query)
        self.assertIn("review.target_type", query)

    async def test_product_code_mismatch_is_not_found(self):
        db = _FakeDb(None)
        with patch.object(service_module, "get_db_ctx", _db_context(db)):
            with self.assertRaises(NotFoundException):
                await PlanOrderManagementService().list_orders(
                    product_type="NISP-1",
                    plan_id=10,
                    status=None,
                    phone=None,
                    page=1,
                    page_size=20,
                )

        self.assertEqual(len(db.statements), 1)


class PlanOrderManagementOpenAPITests(unittest.TestCase):
    def test_admin_plan_order_routes_are_in_openapi(self):
        from app.main import app

        app.openapi_schema = None
        schema = app.openapi()
        base = "/admin/certifications/{code}/plans/{plan_id}"

        self.assertIn(f"{base}/orders", schema["paths"])
        self.assertIn(f"{base}/approvals", schema["paths"])
        order_parameters = {
            parameter["name"]
            for parameter in schema["paths"][f"{base}/orders"]["get"]["parameters"]
        }
        approval_parameters = {
            parameter["name"]
            for parameter in schema["paths"][f"{base}/approvals"]["get"]["parameters"]
        }
        self.assertTrue({"code", "plan_id", "status", "phone", "page", "page_size"} <= order_parameters)
        self.assertTrue({"code", "plan_id", "action", "page", "page_size"} <= approval_parameters)
