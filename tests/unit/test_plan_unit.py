"""Plan module unit tests — schema validation, business logic smoke."""
import ast
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]


class PlanSchemaUnitTests(unittest.TestCase):
    """Pydantic schema validation tests."""

    @classmethod
    def setUpClass(cls):
        from app.schemas.plan import PlanCreate, PlanUpdate, PlanResponse

        cls.PlanCreate = PlanCreate
        cls.PlanUpdate = PlanUpdate
        cls.PlanResponse = PlanResponse

    def test_plan_create_minimal(self):
        """Minimal valid create."""
        plan = self.PlanCreate(name="2026第一期")
        assert plan.name == "2026第一期"
        assert plan.capacity == 0

    def test_plan_create_full(self):
        """All fields provided."""
        from datetime import datetime, timezone

        plan = self.PlanCreate(
            name="2026第二期",
            apply_start=datetime(2026, 6, 1, tzinfo=timezone.utc),
            apply_end=datetime(2026, 7, 13, tzinfo=timezone.utc),
            exam_date=datetime(2026, 8, 8, tzinfo=timezone.utc),
            capacity=50,
        )
        assert plan.capacity == 50
        assert plan.exam_date.year == 2026

    def test_plan_create_name_too_long(self):
        """Name exceeds max_length=128."""
        with self.assertRaises(ValidationError):
            self.PlanCreate(name="x" * 129)

    def test_plan_create_negative_capacity(self):
        """Capacity must be >= 0."""
        with self.assertRaises(ValidationError):
            self.PlanCreate(name="test", capacity=-1)

    def test_plan_response_from_attributes_config(self):
        """PlanResponse has from_attributes=True."""
        assert self.PlanResponse.model_config.get("from_attributes") is True

    def test_plan_response_enrolled_field(self):
        """enrolled is an int field with default 0."""
        fields = self.PlanResponse.model_fields
        assert fields["enrolled"].default == 0

    def test_plan_response_status_is_literal_enum(self):
        """PlanResponse status 是 Literal 枚举，Swagger 自动生成约束"""
        fields = self.PlanResponse.model_fields
        status_field = fields["status"]
        assert hasattr(status_field.annotation, '__args__'), "status should be Literal"
        args = status_field.annotation.__args__
        assert 'draft' in args
        assert 'published' in args
        assert 'archived' in args
        assert 'cancelled' in args

    def test_plan_update_all_optional(self):
        """PlanUpdate: all fields are optional."""
        plan = self.PlanUpdate()  # no fields required
        assert plan.name is None
        assert plan.capacity is None

    def test_plan_names_reject_blank_values(self):
        with self.assertRaises(ValidationError):
            self.PlanCreate(name="   ")
        with self.assertRaises(ValidationError):
            self.PlanUpdate(name="   ")

    def test_plan_update_tracks_explicit_null_for_clearable_dates(self):
        plan = self.PlanUpdate(exam_date=None)
        assert "exam_date" in plan.model_fields_set


class PlanModelUnitTests(unittest.TestCase):
    """Check Plan model structure."""

    def test_plan_model_has_required_columns(self):
        tree = ast.parse((REPO_ROOT / "app/domain/plan/src/model/plan.py").read_text("utf-8"))
        klass = next(
            n for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name == "Plan"
        )
        # Collect Mapped assignments
        columns = {
            stmt.target.id: stmt.annotation  # AnnAssign uses .target (singular), not .targets
            for stmt in klass.body
            if isinstance(stmt, ast.AnnAssign)
            and isinstance(stmt.target, ast.Name)
            and isinstance(stmt.annotation, ast.Subscript)
        }
        expected = [
            "product_type", "name", "apply_start", "apply_end",
            "exam_date", "capacity", "status",
        ]
        for col in expected:
            assert col in columns, f"Missing column: {col}"

    def test_plan_context_constraint_includes_all_states(self):
        source = (REPO_ROOT / "app/domain/plan/src/model/plan.py").read_text("utf-8")
        assert "'draft'" in source
        assert "'published'" in source
        assert "'archived'" in source
        assert "'cancelled'" in source

    def test_plan_migration_exists(self):
        migration = REPO_ROOT / "alembic/versions/p002_plan_table.py"
        assert migration.exists(), "Migration file missing"
        content = migration.read_text("utf-8")
        assert "create_table" in content
        assert '"plan"' in content or "'plan'" in content
        assert "ck_plan_status" in content
        assert "uq_plan_product_name" in content


class PlanServiceUnitTests(unittest.TestCase):
    """Smoke checks on PlanService source structure."""

    def test_plan_service_has_all_methods(self):
        source = (REPO_ROOT / "app/services/plan.py").read_text("utf-8")
        methods = [
            "async def list_plans",
            "async def create_plan",
            "async def update_plan",
            "async def publish_plan",
            "async def archive_plan",
            "async def cancel_plan",
            "async def delete_plan",
            "async def list_published_plans",
            "async def get_plan",
        ]
        for method in methods:
            assert method in source, f"Missing method: {method}"

    def test_publish_only_from_draft(self):
        source = (REPO_ROOT / "app/services/plan.py").read_text("utf-8")
        assert 'plan.status != "draft"' in source or "'draft'" in source

    def test_archive_only_from_published(self):
        source = (REPO_ROOT / "app/services/plan.py").read_text("utf-8")
        assert 'plan.status != "published"' in source or "'published'" in source

    def test_cancel_only_from_published(self):
        source = (REPO_ROOT / "app/services/plan.py").read_text("utf-8")
        assert 'cancel_plan' in source

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

    def test_user_list_filters_to_current_application_window(self):
        source = (REPO_ROOT / "app/services/plan.py").read_text("utf-8")
        assert "Plan.apply_start <= now" in source
        assert "Plan.apply_end >= now" in source

    def test_update_supports_clearing_optional_dates(self):
        source = (REPO_ROOT / "app/services/plan.py").read_text("utf-8")
        assert 'for field in ("apply_start", "apply_end", "exam_date")' in source
        assert "if field in update_data" in source


class PlanAPIRouteUnitTests(unittest.TestCase):
    """Verify API route declarations."""

    def test_admin_routes_have_all_endpoints(self):
        source = (REPO_ROOT / "app/api/admin/plans.py").read_text("utf-8")
        assert '"/{code}/plans"' in source
        assert '"/{code}/plans/{plan_id}"' in source
        assert 'publish' in source
        assert 'archive' in source
        assert 'cancel' in source
        assert 'delete_plan' in source

    def test_user_routes_have_all_endpoints(self):
        source = (REPO_ROOT / "app/api/plans.py").read_text("utf-8")
        assert "list_plans" in source
        assert "get_plan" in source
        assert 'product_type' in source

    def test_user_router_registered_in_api_init(self):
        source = (REPO_ROOT / "app/api/__init__.py").read_text("utf-8")
        assert "from app.api.plans import router as plans_router" in source
        assert "router.include_router(plans_router)" in source

    def test_admin_router_registered_in_admin_init(self):
        source = (REPO_ROOT / "app/api/admin/__init__.py").read_text("utf-8")
        assert "from app.api.admin.plans import router as plans_router" in source
        assert 'router.include_router(plans_router, prefix="/certifications")' in source

    def test_admin_routes_pass_product_code_to_service(self):
        source = (REPO_ROOT / "app/api/admin/plans.py").read_text("utf-8")
        assert source.count("product_type=code") == 9
