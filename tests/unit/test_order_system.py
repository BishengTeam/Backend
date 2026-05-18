import ast
import unittest
from pathlib import Path

from pydantic import ValidationError

from app.schemas.order import OrderCreate, OrderFilter
from app.schemas.payment import PaymentCallbackRequest, PaymentPrepayResponse


REPO_ROOT = Path(__file__).resolve().parents[2]


def _valid_order_payload(**overrides):
    payload = {
        "cert_type": "H3C-NE",
        "candidate_name": "张三",
        "candidate_phone": "13800138000",
        "candidate_idcard": "11010519491231002X",
    }
    payload.update(overrides)
    return payload


def _load_ast(relative_path: str) -> ast.Module:
    return ast.parse((REPO_ROOT / relative_path).read_text(encoding="utf-8"))


def _iter_order_route_decorators():
    tree = _load_ast("app/api/orders.py")
    route_methods = {"api_route", "delete", "get", "head", "options", "patch", "post", "put"}

    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr in route_methods
                and isinstance(func.value, ast.Name)
                and func.value.id == "router"
            ):
                yield node.name, decorator


class OrderSystemTests(unittest.TestCase):
    def test_order_create_accepts_valid_phone_and_idcard(self):
        order = OrderCreate(**_valid_order_payload())

        self.assertEqual(order.candidate_phone, "13800138000")
        self.assertEqual(order.candidate_idcard, "11010519491231002X")

    def test_order_create_rejects_invalid_phone_and_idcard(self):
        cases = [
            ("candidate_phone", "12345"),
            ("candidate_idcard", "not-an-id-card"),
        ]

        for field, bad_value in cases:
            with self.subTest(field=field, bad_value=bad_value):
                payload = _valid_order_payload(**{field: bad_value})
                with self.assertRaises(ValidationError):
                    OrderCreate(**payload)

    def test_order_filter_accepts_known_status_values(self):
        for status in [None, "pending", "paid", "completed", "refunded", "closed"]:
            with self.subTest(status=status):
                filters = OrderFilter(status=status)
                self.assertEqual(filters.status, status)

    def test_order_filter_rejects_unknown_status_value(self):
        with self.assertRaises(ValidationError):
            OrderFilter(status="cancelled")

    def test_payment_callback_rejects_unknown_trade_state(self):
        with self.assertRaises(ValidationError):
            PaymentCallbackRequest(out_trade_no="trade-no", trade_state="UNKNOWN")

    def test_order_api_routes_declare_explicit_response_model(self):
        route_decorators = list(_iter_order_route_decorators())
        self.assertTrue(route_decorators, "app/api/orders.py should define order API routes")

        missing = [
            function_name
            for function_name, decorator in route_decorators
            if not any(keyword.arg == "response_model" for keyword in decorator.keywords)
        ]

        self.assertFalse(missing, f"order API routes missing explicit response_model: {missing}")

    def test_order_service_does_not_load_user_identity_by_primary_key_user_id(self):
        tree = _load_ast("app/services/order.py")
        forbidden_calls = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (isinstance(node.func, ast.Attribute) and node.func.attr == "get"):
                continue
            if node.args and isinstance(node.args[0], ast.Name) and node.args[0].id == "UserIdentity":
                forbidden_calls.append(node.lineno)

        self.assertFalse(
            forbidden_calls,
            "OrderService must query UserIdentity by user_id column, not db.get(UserIdentity, user_id)",
        )

    def test_payment_api_routes_declare_explicit_response_model(self):
        tree = _load_ast("app/api/payment.py")
        missing = []

        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                func = decorator.func
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr in {"post"}
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "router"
                    and not any(keyword.arg == "response_model" for keyword in decorator.keywords)
                ):
                    missing.append(node.name)

        self.assertFalse(missing, f"payment API routes missing explicit response_model: {missing}")

    def test_order_status_machine_defines_required_transitions(self):
        source = (REPO_ROOT / "app/services/order.py").read_text(encoding="utf-8")

        self.assertIn('"pending": {"paid", "closed"}', source)
        self.assertIn('"closed": set()', source)
        self.assertIn('"paid": {"completed", "refunded"}', source)
        self.assertIn('"completed": {"refunded"}', source)

    def test_order_model_declares_inventory_and_close_fields(self):
        source = (REPO_ROOT / "app/models/order.py").read_text(encoding="utf-8")

        self.assertIn("inventory_id: Mapped[int | None]", source)
        self.assertIn("mapped_column(Integer, nullable=True, index=True)", source)
        self.assertIn("expires_at: Mapped[datetime | None]", source)
        self.assertIn("closed_at: Mapped[datetime | None]", source)
        self.assertIn("close_reason: Mapped[str | None] = mapped_column(String(128))", source)

    def test_order_status_constraint_allows_closed(self):
        source = (REPO_ROOT / "app/models/order.py").read_text(encoding="utf-8")

        self.assertIn("ck_order_status", source)
        self.assertIn("status IN ('pending', 'paid', 'completed', 'refunded', 'closed')", source)

    def test_closed_status_migration_replaces_constraint_and_adds_fields(self):
        source = (
            REPO_ROOT
            / "alembic/versions/d3e4f5a6b7c8_add_closed_order_status_and_inventory_fields.py"
        ).read_text(encoding="utf-8")

        self.assertLess(
            source.index('op.drop_constraint("ck_order_status", "order", type_="check")'),
            source.index("op.create_check_constraint("),
        )
        self.assertIn('sa.Column("inventory_id", sa.Integer(), nullable=True)', source)
        self.assertIn('sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True)', source)
        self.assertIn('sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True)', source)
        self.assertIn('sa.Column("close_reason", sa.String(length=128), nullable=True)', source)
        self.assertIn("status IN ('pending', 'paid', 'completed', 'refunded', 'closed')", source)

    def test_payment_callback_verifies_signature_locks_order_and_transitions_status(self):
        source = (REPO_ROOT / "app/services/payment.py").read_text(encoding="utf-8")

        self.assertIn("verify_signature", source)
        self.assertIn("with_for_update", source)
        self.assertIn("apply_order_status_transition", source)

    def test_payment_integration_has_no_implicit_hardcoded_mock_path(self):
        source = (REPO_ROOT / "app/integrations/wechat_pay.py").read_text(encoding="utf-8")
        if hasattr(PaymentPrepayResponse, "model_fields"):
            response_fields = set(PaymentPrepayResponse.model_fields)
        else:
            response_fields = set(PaymentPrepayResponse.__fields__)

        self.assertNotIn("mock", response_fields)
        self.assertNotIn("mock-prepay", source)
        self.assertNotIn("mock=True", source)
        self.assertNotIn("APP_DEBUG and not self.api_key", source)
        self.assertIn("Wechat Pay configuration is incomplete", source)

    def test_chat_service_has_no_hardcoded_manual_reply_backend(self):
        service_source = (REPO_ROOT / "app/services/chat.py").read_text(encoding="utf-8")
        backend_source = (REPO_ROOT / "app/integrations/chat_backend.py").read_text(encoding="utf-8")

        self.assertNotIn("ManualChatBackend", service_source)
        self.assertNotIn("ManualChatBackend", backend_source)
        self.assertNotIn("人工客服正在赶来", backend_source)
        self.assertIn("CHAT_BACKEND", service_source)
        self.assertIn("DifyChatBackend", backend_source)


if __name__ == "__main__":
    unittest.main()
