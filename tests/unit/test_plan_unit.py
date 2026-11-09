"""Plan v2 module unit tests — batch schema validation, business logic smoke."""
import ast
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]


class PlanSchemaUnitTests(unittest.TestCase):
    """Pydantic schema validation tests for v2 batch schemas."""

    @classmethod
    def setUpClass(cls):
        from app.schemas.plan import BatchCreate, BatchUpdate, BatchResponse

        cls.BatchCreate = BatchCreate
        cls.BatchUpdate = BatchUpdate
        cls.BatchResponse = BatchResponse

    def test_batch_create_minimal(self):
        """Minimal valid create."""
        batch = self.BatchCreate(name="2026第一期")
        assert batch.name == "2026第一期"
        assert batch.products == []
        assert batch.time_segments == []

    def test_batch_create_full(self):
        """All fields provided."""
        batch = self.BatchCreate(
            name="2026第二期",
            apply_start=datetime(2026, 6, 1, tzinfo=timezone.utc),
            apply_end=datetime(2026, 7, 13, tzinfo=timezone.utc),
            exam_date=datetime(2026, 8, 8, tzinfo=timezone.utc),
            exam_location="测试考点",
            contact_name="张老师",
            contact_phone="13800138000",
        )
        assert batch.exam_date.year == 2026
        assert batch.exam_location == "测试考点"
        assert batch.sort_order == 0

    def test_batch_create_name_too_long(self):
        """Name exceeds max_length=128."""
        with self.assertRaises(ValidationError):
            self.BatchCreate(name="x" * 129)

    def test_batch_create_with_products(self):
        """Batch with product list."""
        from app.schemas.plan import PlanProductCreate, TimeSegmentCreate
        from datetime import time
        batch = self.BatchCreate(
            name="混合批次",
            products=[PlanProductCreate(product_id=1, capacity=50)],
            time_segments=[TimeSegmentCreate(
                label="上午场", start_time=time(9, 0), end_time=time(12, 0)
            )],
        )
        assert len(batch.products) == 1
        assert batch.products[0].capacity == 50
        assert len(batch.time_segments) == 1

    def test_batch_response_from_attributes_config(self):
        """BatchResponse uses Pydantic v2 model."""
        assert hasattr(self.BatchResponse, "model_validate")

    def test_batch_response_status_is_literal_enum(self):
        """BatchResponse status 是 Literal 枚举"""
        fields = self.BatchResponse.model_fields
        status_field = fields["status"]
        assert hasattr(status_field.annotation, "__args__"), "status should be Literal"
        args = status_field.annotation.__args__
        assert "draft" in args
        assert "published" in args
        assert "archived" in args
        assert "cancelled" in args

    def test_batch_update_all_optional(self):
        """BatchUpdate: all fields are optional."""
        batch = self.BatchUpdate()
        assert batch.name is None
        assert batch.apply_start is None

    def test_batch_names_reject_blank_values(self):
        with self.assertRaises(ValidationError):
            self.BatchCreate(name="   ")
        with self.assertRaises(ValidationError):
            self.BatchUpdate(name="   ")

    def test_time_segment_validation(self):
        """TimeSegmentCreate validates label and times."""
        from app.schemas.plan import TimeSegmentCreate
        from datetime import time
        seg = TimeSegmentCreate(label="上午", start_time=time(9,0), end_time=time(12,0))
        assert seg.label == "上午"
        assert seg.sort_order == 0

    def test_plan_product_create_capacity(self):
        """PlanProductCreate capacity >= 0."""
        from app.schemas.plan import PlanProductCreate
        pp = PlanProductCreate(product_id=1, capacity=30)
        assert pp.capacity == 30
        assert pp.renshe_config_id is None


class PlanModelUnitTests(unittest.TestCase):
    """Check Plan v2 model structure."""

    def test_plan_model_has_required_columns(self):
        tree = ast.parse((REPO_ROOT / "app/domain/plan/src/model/plan.py").read_text("utf-8"))
        klass = next(
            n for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name == "Plan"
        )
        columns = {
            stmt.target.id
            for stmt in klass.body
            if isinstance(stmt, ast.AnnAssign)
            and isinstance(stmt.target, ast.Name)
        }
        expected = [
            "name", "apply_start", "apply_end",
            "exam_date", "exam_location", "status",
        ]
        for col in expected:
            assert col in columns, f"Missing column: {col}"

    def test_plan_v2_no_product_columns(self):
        """v2 Plan 不再包含产品级字段。"""
        tree = ast.parse((REPO_ROOT / "app/domain/plan/src/model/plan.py").read_text("utf-8"))
        klass = next(
            n for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name == "Plan"
        )
        columns = {
            stmt.target.id
            for stmt in klass.body
            if isinstance(stmt, ast.AnnAssign)
            and isinstance(stmt.target, ast.Name)
        }
        removed = ["product_type", "capacity", "price_cents", "occupation_name", "skill_level"]
        for col in removed:
            assert col not in columns, f"Column {col} should be removed in v2"

    def test_plan_status_constraint_includes_all_states(self):
        source = (REPO_ROOT / "app/domain/plan/src/model/plan.py").read_text("utf-8")
        assert "'draft'" in source
        assert "'published'" in source
        assert "'archived'" in source
        assert "'cancelled'" in source

    def test_plan_batch_refactor_migration_exists(self):
        migration = REPO_ROOT / "alembic/versions/cbr001_batch_refactor.py"
        assert migration.exists(), "cbr001 migration file missing"
        content = migration.read_text("utf-8")
        assert "plan_product" in content
        assert "plan_time_segment" in content


class PlanServiceUnitTests(unittest.TestCase):
    """Smoke checks on PlanService v2 source structure."""

    def test_plan_service_has_v2_methods(self):
        source = (REPO_ROOT / "app/services/plan.py").read_text("utf-8")
        methods = [
            "async def list_batches",
            "async def get_batch",
            "async def create_batch",
            "async def update_batch",
            "async def delete_batch",
            "async def publish",
            "async def close_registration",
            "async def finalize",
            "async def cancel",
            "async def archive",
            "async def add_product",
            "async def remove_product",
            "async def add_time_segment",
            "async def remove_time_segment",
            "async def list_published_batches_for_product",
        ]
        for method in methods:
            assert method in source, f"Missing method: {method}"

    def test_publish_only_from_draft(self):
        source = (REPO_ROOT / "app/services/plan.py").read_text("utf-8")
        assert 'plan.status != "draft"' in source

    def test_schedule_validation_rejects_invalid_windows(self):
        from app.port.exceptions import ValidationException
        from app.services.plan import PlanService

        now = datetime(2026, 7, 14, tzinfo=timezone.utc)
        with self.assertRaises(ValidationException):
            PlanService._validate_schedule(
                apply_start=None,
                apply_end=None,
                exam_date=None,
                require_window=True,
                now=now,
            )
        with self.assertRaises(ValidationException):
            PlanService._validate_schedule(
                apply_start=now,
                apply_end=now,
                exam_date=None,
            )
        with self.assertRaises(ValidationException):
            PlanService._validate_schedule(
                apply_start=now - timedelta(days=2),
                apply_end=now - timedelta(days=1),
                exam_date=None,
                require_window=True,
                now=now,
            )
        with self.assertRaises(ValidationException):
            PlanService._validate_schedule(
                apply_start=now,
                apply_end=now + timedelta(days=2),
                exam_date=now + timedelta(days=1),
            )

    def test_publish_validates_products_and_segments(self):
        source = (REPO_ROOT / "app/services/plan.py").read_text("utf-8")
        assert "至少包含一个产品" in source
        assert "至少包含一个考试时段" in source


class PlanAPIRouteUnitTests(unittest.TestCase):
    """Verify v2 API route declarations."""

    def test_admin_routes_have_batch_endpoints(self):
        source = (REPO_ROOT / "app/api/admin/plans.py").read_text("utf-8")
        assert '"/batches"' in source
        assert '"/batches/{batch_id}"' in source
        assert "publish" in source
        assert "close-registration" in source
        assert "finalize" in source
        assert "cancel" in source
        assert "archive" in source
        assert "time-segments" in source

    def test_user_routes_have_all_endpoints(self):
        source = (REPO_ROOT / "app/api/plans.py").read_text("utf-8")
        assert "list_plans" in source
        assert "get_plan" in source
        assert "product_type" in source

    def test_user_router_registered_in_api_init(self):
        source = (REPO_ROOT / "app/api/__init__.py").read_text("utf-8")
        assert "from app.api.plans import router as plans_router" in source
        assert "router.include_router(plans_router)" in source

    def test_admin_router_registered_in_admin_init(self):
        source = (REPO_ROOT / "app/api/admin/__init__.py").read_text("utf-8")
        assert "from app.api.admin.plans import router as plans_router" in source
        assert 'router.include_router(plans_router, prefix="/certifications")' in source

    def test_old_plan_routes_return_410(self):
        source = (REPO_ROOT / "app/api/admin/plans.py").read_text("utf-8")
        assert "410" in source or "GONE" in source
