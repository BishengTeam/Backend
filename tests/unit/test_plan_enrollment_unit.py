"""PlanEnrollmentService v2 unit tests — window validation and capacity locking."""
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.port.exceptions import BusinessException, ConflictException
from app.services.plan_enrollment import (
    CAPACITY_OCCUPYING_ORDER_STATUSES,
    PlanEnrollmentService,
)


NOW = datetime(2026, 7, 14, tzinfo=timezone.utc)


def _open_plan(**overrides):
    values = {
        "id": 7,
        "name": "2026 第一批",
        "status": "published",
        "apply_start": NOW - timedelta(days=1),
        "apply_end": NOW + timedelta(days=1),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _plan_product(capacity=0):
    return SimpleNamespace(
        id=1, plan_id=7, product_id=1, capacity=capacity,
        renshe_config_id=None, occupation_name=None, occupation_code=None,
        skill_level=None, exam_type=None, application_type=None,
    )


def _product(**overrides):
    values = {"id": 1, "type": "h3c", "code": "GB0-192", "is_active": True}
    values.update(overrides)
    return SimpleNamespace(**values)


class _Result:
    """Flexible result mock supporting multiple SQLAlchemy result APIs."""

    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value

    def first(self):
        if isinstance(self.value, tuple):
            return self.value
        return (self.value,) if self.value is not None else None


class _FakeDb:
    def __init__(self, *results):
        self.results = list(results)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _Result(self.results.pop(0) if self.results else None)


class PlanEnrollmentWindowTests(unittest.TestCase):
    def test_open_published_plan_is_eligible(self):
        PlanEnrollmentService.validate_application_window(_open_plan(), now=NOW)

    def test_non_published_plan_is_rejected(self):
        with self.assertRaises(BusinessException) as raised:
            PlanEnrollmentService.validate_application_window(
                _open_plan(status="draft"), now=NOW,
            )
        self.assertEqual(raised.exception.message, "该批次当前不可报名")

    def test_future_and_expired_windows_are_rejected(self):
        with self.assertRaises(BusinessException) as future:
            PlanEnrollmentService.validate_application_window(
                _open_plan(apply_start=NOW + timedelta(minutes=1)), now=NOW,
            )
        self.assertEqual(future.exception.message, "该批次报名尚未开始")

        with self.assertRaises(BusinessException) as expired:
            PlanEnrollmentService.validate_application_window(
                _open_plan(apply_end=NOW - timedelta(minutes=1)), now=NOW,
            )
        self.assertEqual(expired.exception.message, "该批次报名已截止")

    def test_missing_window_is_rejected(self):
        with self.assertRaises(BusinessException):
            PlanEnrollmentService.validate_application_window(
                _open_plan(apply_start=None, apply_end=None), now=NOW,
            )


class PlanEnrollmentDatabaseTests(unittest.IsolatedAsyncioTestCase):
    """v2 lock_enrollable_plan tests using fake sequential results.

    Query order in lock_enrollable_plan:
      1. select(Plan)                    → scalar_one_or_none
      2. select(PlanProduct, CertProduct) → first (tuple)
      3. select(Order.id) duplicate check → scalar_one_or_none
      4. select(count) capacity check     → scalar_one (only if capacity > 0)
    """

    async def test_finite_capacity_counts_all_occupying_statuses(self):
        plan = _open_plan()
        pp = _plan_product(capacity=1)
        product = _product()
        # Queries: plan, (pp, product), no dup order, occupied=1
        db = _FakeDb(plan, (pp, product), None, 1)

        with self.assertRaises(BusinessException) as raised:
            await PlanEnrollmentService().lock_enrollable_plan(
                db, plan_id=plan.id, user_id=100,
                expected_vendor="H3C", now=NOW,
            )
        self.assertEqual(raised.exception.message, "该批次名额已满")
        self.assertEqual(CAPACITY_OCCUPYING_ORDER_STATUSES, ("pending", "paid", "completed"))

    async def test_unlimited_capacity_skips_capacity_query(self):
        plan = _open_plan()
        pp = _plan_product(capacity=0)
        product = _product()
        # capacity=0, so no 4th query
        db = _FakeDb(plan, (pp, product), None)

        result = await PlanEnrollmentService().lock_enrollable_plan(
            db, plan_id=plan.id, user_id=100,
            expected_vendor="H3C", now=NOW,
        )
        self.assertIs(result, plan)
        self.assertEqual(len(db.statements), 3)

    async def test_wrong_product_type_is_rejected(self):
        plan = _open_plan()
        # v2: WHERE type='h3c' 过滤 nisp 产品 → first() 返回 None
        db = _FakeDb(plan, None)

        with self.assertRaises(BusinessException) as raised:
            await PlanEnrollmentService().lock_enrollable_plan(
                db, plan_id=plan.id, user_id=100,
                expected_vendor="H3C", now=NOW,
            )
        # v2: type 不匹配时 query 过滤后返回 None → "不包含"
        self.assertIn("不包含", raised.exception.message)

    async def test_inactive_cert_product_is_rejected(self):
        plan = _open_plan()
        # v2: WHERE is_active=True 过滤下架产品 → first() 返回 None
        db = _FakeDb(plan, None)

        with self.assertRaises(BusinessException) as raised:
            await PlanEnrollmentService().lock_enrollable_plan(
                db, plan_id=plan.id, user_id=100,
                expected_vendor="H3C", now=NOW,
            )
        # v2: is_active 过滤在 query WHERE 中 → "不包含"
        self.assertIn("不包含", raised.exception.message)

    async def test_existing_active_order_is_rejected(self):
        plan = _open_plan()
        pp = _plan_product(capacity=10)
        product = _product()
        # 3rd query returns existing order id = 99
        db = _FakeDb(plan, (pp, product), 99)

        with self.assertRaises(ConflictException) as raised:
            await PlanEnrollmentService().lock_enrollable_plan(
                db, plan_id=plan.id, user_id=100,
                expected_vendor="H3C", now=NOW,
            )
        self.assertIn("请勿重复提交", raised.exception.message)

    async def test_no_matching_product_in_batch(self):
        plan = _open_plan()
        # 2nd query returns None (no PlanProduct found)
        db = _FakeDb(plan, None)

        with self.assertRaises(BusinessException) as raised:
            await PlanEnrollmentService().lock_enrollable_plan(
                db, plan_id=plan.id, user_id=100,
                expected_vendor="H3C", now=NOW,
            )
        self.assertIn("不包含", raised.exception.message)

    async def test_plan_not_found(self):
        db = _FakeDb(None)

        from app.port.exceptions import NotFoundException
        with self.assertRaises(NotFoundException):
            await PlanEnrollmentService().lock_enrollable_plan(
                db, plan_id=999, user_id=100,
                expected_vendor="H3C", now=NOW,
            )

    async def test_capacity_statuses_are_tuple(self):
        """CAPACITY_OCCUPYING_ORDER_STATUSES is a tuple for SQLAlchemy .in_()."""
        assert isinstance(CAPACITY_OCCUPYING_ORDER_STATUSES, tuple)
        assert all(isinstance(s, str) for s in CAPACITY_OCCUPYING_ORDER_STATUSES)
