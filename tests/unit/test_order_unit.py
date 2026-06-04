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
        self.assertIn("if order.status == target_status:", source)
        self.assertIn("return False", source)
        self.assertIn("if target_status not in allowed_targets:", source)
        self.assertIn("raise ConflictException", source)

    def test_order_service_locks_inventory_with_conditional_update(self):
        source = (REPO_ROOT / "app/services/inventory.py").read_text(encoding="utf-8")

        self.assertIn("UPDATE inventory", source)
        self.assertIn("available_quota = available_quota - 1", source)
        self.assertIn("locked_quota = locked_quota + 1", source)
        self.assertEqual(source.count("UPDATE inventory"), source.count("updated_at = now()"))
        self.assertIn("AND available_quota >= 1", source)
        self.assertIn("RETURNING id, total_quota, available_quota, locked_quota, sold_quota", source)
        self.assertIn('raise BusinessException("认证报名名额不足")', source)

    def test_order_creation_sets_inventory_and_expiration_in_transaction(self):
        source = (REPO_ROOT / "app/services/order.py").read_text(encoding="utf-8")
        create_order_source = source[
            source.index("async def create_order") : source.index("async def list_orders")
        ]

        self.assertIn("async with db.begin():", create_order_source)
        self.assertLess(
            create_order_source.index("inventory_change = await lock_certification_inventory"),
            create_order_source.index("order = Order("),
        )
        self.assertIn("inventory_id=inventory_change.inventory_id", create_order_source)
        self.assertIn("expires_at=expires_at", create_order_source)
        self.assertIn('status="pending"', create_order_source)
        self.assertIn("add_inventory_record(", create_order_source)
        self.assertIn("action=INVENTORY_LOCK_ACTION", create_order_source)
        self.assertNotIn("await db.commit()", create_order_source)

    def test_order_model_declares_inventory_and_close_fields(self):
        source = (REPO_ROOT / "app/models/order.py").read_text(encoding="utf-8")

        self.assertIn("inventory_id: Mapped[int | None]", source)
        self.assertIn('ForeignKey("inventory.id")', source)
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
        self.assertIn('order.transaction_id = data.transaction_id', source)
        self.assertIn("order.paid_at = data.paid_at or self._now()", source)
        self.assertIn("confirm_inventory_sale", source)
        self.assertIn("await self._confirm_inventory_sale(db, order)", source)

    def test_payment_callback_idempotency_confirms_inventory_only_for_pending_order(self):
        source = (REPO_ROOT / "app/services/payment.py").read_text(encoding="utf-8")
        success_source = source[
            source.index('if data.trade_state == "SUCCESS":') :
            source.index('elif data.trade_state == "REFUND":')
        ]
        paid_or_completed_source = success_source[
            success_source.index('elif order.status in {"paid", "completed"}:') :
            success_source.index("else:")
        ]

        self.assertIn("if not data.transaction_id:", success_source)
        self.assertIn("Order.transaction_id == data.transaction_id", success_source)
        self.assertIn("Order.id != order.id", success_source)
        self.assertIn("raise ConflictException", success_source)
        self.assertIn('if order.status == "pending":', success_source)
        self.assertIn("await self._confirm_inventory_sale(db, order)", success_source)
        self.assertIn('elif order.status in {"paid", "completed"}:', success_source)
        self.assertNotIn("await self._confirm_inventory_sale", paid_or_completed_source)
        self.assertIn("metadata_changed = True", paid_or_completed_source)

    def test_inventory_service_confirms_and_releases_locked_inventory(self):
        source = (REPO_ROOT / "app/services/inventory.py").read_text(encoding="utf-8")

        self.assertIn("def add_inventory_record", source)
        self.assertIn("async def confirm_inventory_sale", source)
        self.assertIn("locked_quota = locked_quota - 1", source)
        self.assertIn("sold_quota = sold_quota + 1", source)
        self.assertIn("async def release_inventory_lock", source)
        self.assertIn("available_quota = available_quota + 1", source)
        self.assertIn("action=INVENTORY_CONFIRM_ACTION", source)
        self.assertIn("action=INVENTORY_RELEASE_ACTION", source)

    def test_close_expired_pending_order_closes_only_expired_pending_orders(self):
        source = (REPO_ROOT / "app/services/order_timeout.py").read_text(encoding="utf-8")

        self.assertIn('if order.status != "pending":', source)
        self.assertIn("if order.expires_at is None:", source)
        self.assertIn("expires_at = order.expires_at", source)
        self.assertIn("expires_at = expires_at.replace(tzinfo=timezone.utc)", source)
        self.assertIn("if expires_at > now:", source)
        self.assertIn('apply_order_status_transition(order, "closed")', source)
        self.assertIn("order.closed_at = now", source)
        self.assertIn("order.close_reason = close_reason", source)

    def test_order_timeout_close_service_selects_expired_pending_orders_with_row_lock(self):
        source = (REPO_ROOT / "app/services/order_timeout.py").read_text(encoding="utf-8")

        self.assertIn('Order.status == "pending"', source)
        self.assertIn("Order.expires_at.is_not(None)", source)
        self.assertIn("Order.expires_at <= closed_at", source)
        self.assertIn("with_for_update(skip_locked=True)", source)
        self.assertIn("from app.core.exceptions import BusinessException", source)
        self.assertIn('raise BusinessException("limit must be greater than 0")', source)
        self.assertIn('raise BusinessException("close_reason must be 1-128 characters")', source)
        self.assertNotIn("raise ValueError", source)
        self.assertIn("close_reason", source)
        self.assertIn("await release_inventory_lock(db, order, reason=close_reason)", source)

    def test_payment_prepay_closes_expired_pending_order_before_wechat_call(self):
        source = (REPO_ROOT / "app/services/payment.py").read_text(encoding="utf-8")

        self.assertIn("def _is_expired", source)
        self.assertIn("PREPAY_EXPIRATION_GUARD_SECONDS = 60", source)
        self.assertIn("def _seconds_until_expiration", source)
        self.assertIn("def _is_expiring_soon", source)
        self.assertIn("async def _ensure_order_payable_for_prepay", source)
        self.assertIn("remaining_seconds <= PREPAY_EXPIRATION_GUARD_SECONDS", source)
        self.assertIn('raise BusinessException("订单即将过期，请重新下单")', source)
        self.assertIn("await self._close_expired_order(db, order, now)", source)
        self.assertIn('apply_order_status_transition(order, "closed")', source)
        self.assertIn("order.closed_at = now", source)
        self.assertIn('order.close_reason = "expired"', source)
        self.assertIn("await self._release_inventory_lock(db, order)", source)
        self.assertIn("release_inventory_lock", source)
        self.assertLess(
            source.index("await self._ensure_order_payable_for_prepay(db, order, now)"),
            source.index("await self.wechat_pay.create_jsapi_prepay("),
        )
        self.assertLess(
            source.index("await self.wechat_pay.create_jsapi_prepay("),
            source.rindex("await self._ensure_order_payable_for_prepay(db, order, now)"),
        )
        self.assertLess(
            source.rindex("await self._ensure_order_payable_for_prepay(db, order, now)"),
            source.index("return PaymentPrepayResponse("),
        )
        self.assertEqual(
            source.count("await self._ensure_order_payable_for_prepay(db, order, now)"),
            2,
        )
        self.assertLess(
            source.index("if self._is_expiring_soon(order, now):"),
            source.index("await self.wechat_pay.create_jsapi_prepay("),
        )

    def test_order_model_declares_unique_transaction_id_index(self):
        source = (REPO_ROOT / "app/models/order.py").read_text(encoding="utf-8")

        self.assertIn('Index("ix_order_transaction_id_unique", "transaction_id", unique=True)', source)

    def test_transaction_id_unique_index_migration_exists(self):
        source = (
            REPO_ROOT
            / "alembic/versions/e1f2a3b4c5d6_add_unique_order_transaction_id.py"
        ).read_text(encoding="utf-8")

        self.assertIn('down_revision: Union[str, Sequence[str], None] = "d3e4f5a6b7c8"', source)
        self.assertIn('"ix_order_transaction_id_unique"', source)
        self.assertIn('"transaction_id"', source)
        self.assertIn("unique=True", source)

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
