import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from app.port.exceptions import BusinessException, ConflictException
from app.services.plan_enrollment import (
    CAPACITY_OCCUPYING_ORDER_STATUSES,
    PlanEnrollmentService,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _open_plan(**overrides):
    now = datetime(2026, 7, 14, tzinfo=timezone.utc)
    values = {
        "id": 7,
        "product_type": "H3C-NE",
        "name": "2026 第一批",
        "status": "published",
        "apply_start": now - timedelta(days=1),
        "apply_end": now + timedelta(days=1),
        "capacity": 1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value


class _FakeDb:
    def __init__(self, *results):
        self.results = list(results)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _Result(self.results.pop(0))


class PlanEnrollmentWindowTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 14, tzinfo=timezone.utc)

    def test_open_published_plan_is_eligible(self):
        PlanEnrollmentService.validate_application_window(
            _open_plan(),
            now=self.now,
        )

    def test_non_published_plan_is_rejected(self):
        with self.assertRaises(BusinessException) as raised:
            PlanEnrollmentService.validate_application_window(
                _open_plan(status="draft"),
                now=self.now,
            )
        self.assertEqual(raised.exception.message, "该批次当前不可报名")

    def test_future_and_expired_windows_are_rejected(self):
        with self.assertRaises(BusinessException) as future:
            PlanEnrollmentService.validate_application_window(
                _open_plan(apply_start=self.now + timedelta(minutes=1)),
                now=self.now,
            )
        self.assertEqual(future.exception.message, "该批次报名尚未开始")

        with self.assertRaises(BusinessException) as expired:
            PlanEnrollmentService.validate_application_window(
                _open_plan(apply_end=self.now - timedelta(minutes=1)),
                now=self.now,
            )
        self.assertEqual(expired.exception.message, "该批次报名已截止")


class PlanEnrollmentDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_finite_capacity_counts_all_occupying_statuses(self):
        plan = _open_plan(capacity=1)
        certification = SimpleNamespace(vendor="H3C", is_active=True)
        db = _FakeDb(plan, certification, None, 1)

        with self.assertRaises(BusinessException) as raised:
            await PlanEnrollmentService().lock_enrollable_plan(
                db,
                plan_id=plan.id,
                user_id=100,
                expected_vendor="H3C",
                now=datetime(2026, 7, 14, tzinfo=timezone.utc),
            )

        self.assertEqual(raised.exception.message, "该批次名额已满")
        self.assertEqual(
            CAPACITY_OCCUPYING_ORDER_STATUSES,
            ("pending", "paid", "completed"),
        )
        self.assertEqual(len(db.statements), 4)

    async def test_unlimited_capacity_skips_capacity_query(self):
        plan = _open_plan(capacity=0)
        certification = SimpleNamespace(vendor="H3C", is_active=True)
        db = _FakeDb(plan, certification, None)

        result = await PlanEnrollmentService().lock_enrollable_plan(
            db,
            plan_id=plan.id,
            user_id=100,
            expected_vendor="H3C",
            now=datetime(2026, 7, 14, tzinfo=timezone.utc),
        )

        self.assertIs(result, plan)
        self.assertEqual(len(db.statements), 3)

    async def test_wrong_vendor_is_rejected_before_capacity_check(self):
        plan = _open_plan(capacity=1)
        certification = SimpleNamespace(vendor="NISP", is_active=True)
        db = _FakeDb(plan, certification)

        with self.assertRaises(BusinessException) as raised:
            await PlanEnrollmentService().lock_enrollable_plan(
                db,
                plan_id=plan.id,
                user_id=100,
                expected_vendor="H3C",
                now=datetime(2026, 7, 14, tzinfo=timezone.utc),
            )

        self.assertEqual(raised.exception.message, "该批次不属于H3C认证")
        self.assertEqual(len(db.statements), 2)

    async def test_existing_active_order_is_rejected(self):
        plan = _open_plan(capacity=10)
        certification = SimpleNamespace(vendor="H3C", is_active=True)
        db = _FakeDb(plan, certification, 99)

        with self.assertRaises(ConflictException) as raised:
            await PlanEnrollmentService().lock_enrollable_plan(
                db,
                plan_id=plan.id,
                user_id=100,
                expected_vendor="H3C",
                now=datetime(2026, 7, 14, tzinfo=timezone.utc),
            )

        self.assertEqual(raised.exception.message, "您已报名该批次，请勿重复提交")
        self.assertEqual(raised.exception.http_status_code, 409)


class PlanOrderAssociationStructureTests(unittest.TestCase):
    def test_order_model_and_migration_define_plan_foreign_key(self):
        model_source = (
            REPO_ROOT / "app/domain/order/src/model/order.py"
        ).read_text(encoding="utf-8")
        migration_source = (
            REPO_ROOT / "alembic/versions/c4e5f6a7b8c9_add_plan_id_to_order.py"
        ).read_text(encoding="utf-8")

        self.assertIn("plan_id: Mapped[int | None]", model_source)
        self.assertIn('ForeignKey("plan.id", ondelete="RESTRICT")', model_source)
        self.assertIn('op.add_column("order"', migration_source)
        self.assertIn('"fk_order_plan_id_plan"', migration_source)
        self.assertIn('ondelete="RESTRICT"', migration_source)

    def test_plan_lock_is_taken_for_capacity_serialization(self):
        source = (
            REPO_ROOT / "app/services/plan_enrollment.py"
        ).read_text(encoding="utf-8")
        self.assertIn(".with_for_update()", source)
        self.assertIn("Order.plan_id == plan.id", source)
