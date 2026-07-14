import os
import unittest
from pathlib import Path
from types import SimpleNamespace


os.environ.setdefault("JWT_SECRET", "test-secret-key-min-32-chars-long")

from app.domain.order.src.index import (
    INVENTORY_REFUND_ACTION,
    confirm_inventory_sale,
    refund_inventory_sale,
    release_inventory_lock,
)
from app.port.exceptions import ConflictException


REPO_ROOT = Path(__file__).resolve().parents[2]


class _MappingResult:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return self

    def one_or_none(self):
        return self.row


class _FakeDb:
    def __init__(self, *, scalar_values=(), rows=()):
        self.scalar_values = list(scalar_values)
        self.rows = list(rows)
        self.added = []
        self.execute_calls = 0

    async def scalar(self, _statement):
        return self.scalar_values.pop(0)

    async def execute(self, _statement, _params=None):
        self.execute_calls += 1
        return _MappingResult(self.rows.pop(0))

    def add(self, value):
        self.added.append(value)


def _order():
    return SimpleNamespace(id=7, inventory_id=3)


def _inventory_after_refund():
    return {
        "id": 3,
        "total_quota": 5,
        "available_quota": 5,
        "locked_quota": 0,
        "sold_quota": 0,
    }


class InventoryLifecycleTransitionTests(unittest.IsolatedAsyncioTestCase):
    async def test_refund_moves_sold_inventory_back_to_available(self):
        db = _FakeDb(
            scalar_values=[None, 11],
            rows=[_inventory_after_refund()],
        )

        changed = await refund_inventory_sale(db, _order(), reason="test_refund")

        self.assertTrue(changed)
        self.assertEqual(db.execute_calls, 1)
        self.assertEqual(len(db.added), 1)
        record = db.added[0]
        self.assertEqual(record.action, INVENTORY_REFUND_ACTION)
        self.assertEqual(record.before_available_quota, 4)
        self.assertEqual(record.before_sold_quota, 1)
        self.assertEqual(record.after_available_quota, 5)
        self.assertEqual(record.after_sold_quota, 0)

    async def test_repeated_refund_is_idempotent(self):
        db = _FakeDb(scalar_values=[12])

        changed = await refund_inventory_sale(db, _order())

        self.assertFalse(changed)
        self.assertEqual(db.execute_calls, 0)
        self.assertEqual(db.added, [])

    async def test_refund_requires_confirmed_sale(self):
        db = _FakeDb(scalar_values=[None, None])

        with self.assertRaises(ConflictException):
            await refund_inventory_sale(db, _order())

        self.assertEqual(db.execute_calls, 0)

    async def test_release_rejects_confirmed_sale(self):
        db = _FakeDb(scalar_values=[None, 11])

        with self.assertRaises(ConflictException):
            await release_inventory_lock(db, _order())

        self.assertEqual(db.execute_calls, 0)

    async def test_confirm_rejects_released_lock(self):
        db = _FakeDb(scalar_values=[None, 13])

        with self.assertRaises(ConflictException):
            await confirm_inventory_sale(db, _order())

        self.assertEqual(db.execute_calls, 0)


class InventoryLifecycleStructureTests(unittest.TestCase):
    def test_payment_and_admin_refunds_use_sold_inventory_transition(self):
        payment_source = (REPO_ROOT / "app/services/payment.py").read_text(
            encoding="utf-8"
        )
        admin_source = (REPO_ROOT / "app/services/admin_order.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("refund_inventory_sale", payment_source)
        self.assertIn('elif data.trade_state == "REFUND":', payment_source)
        self.assertIn("refund_inventory_sale", admin_source)
        self.assertNotIn("release_inventory_lock", admin_source)
        self.assertIn("with_for_update()", admin_source)
        self.assertIn('out_refund_no = f"RF{order.id}"', admin_source)

    def test_lifecycle_indexes_are_declared_in_model_and_migration(self):
        order_source = (
            REPO_ROOT / "app/domain/order/src/model/order.py"
        ).read_text(encoding="utf-8")
        inventory_source = (
            REPO_ROOT / "app/domain/order/src/model/inventory.py"
        ).read_text(encoding="utf-8")
        migration_source = (
            REPO_ROOT
            / "alembic/versions/d5f6a7b8c9d0_harden_inventory_lifecycle.py"
        ).read_text(encoding="utf-8")

        self.assertIn("uq_order_active_user_plan", order_source)
        self.assertIn("uq_inventory_record_order_action", inventory_source)
        self.assertIn("uq_order_active_user_plan", migration_source)
        self.assertIn("uq_inventory_record_order_action", migration_source)
