from __future__ import annotations

import ast
import importlib
import inspect
import unittest
from pathlib import Path

from pydantic import BaseModel, ValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]

ROUTE_METHODS = {"api_route", "delete", "get", "head", "options", "patch", "post", "put"}
FORBIDDEN_HTTP_TYPES = {
    "Request", "Response", "JSONResponse", "HTMLResponse",
    "PlainTextResponse", "RedirectResponse", "StreamingResponse",
}

# Batch 3 admin endpoints (tickets, statistics, settings, competition)
EXPECTED_ADMIN_ENDPOINTS_BATCH3 = (
    ("GET", "/admin/tickets"),
    ("PUT", "/admin/tickets/{ticket_id}"),
    ("GET", "/admin/statistics/dashboard"),
    ("GET", "/admin/settings/admins"),
    ("POST", "/admin/settings/admins"),
    ("PUT", "/admin/settings/admins/{admin_id}"),
    ("GET", "/admin/competition/export"),
)


def _path(relative_path: str) -> Path:
    return REPO_ROOT / relative_path


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _load_ast(relative_path: str) -> ast.Module:
    file_path = _path(relative_path)
    return ast.parse(_read_text(file_path), filename=str(file_path))


def _literal_string(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    try:
        value = ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None
    return value if isinstance(value, str) else None


def _keyword_value(call: ast.Call, keyword_name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == keyword_name:
            return keyword.value
    return None


def _field_names(model: type[BaseModel]) -> set[str]:
    if hasattr(model, "model_fields"):
        return set(model.model_fields)
    return set(getattr(model, "__fields__", {}))


def _annotation_names(node: ast.AST | None) -> set[str]:
    if node is None:
        return set()
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Attribute):
        return {node.attr} | _annotation_names(node.value)
    if isinstance(node, ast.Subscript):
        return _annotation_names(node.value) | _annotation_names(node.slice)
    if isinstance(node, ast.BinOp):
        return _annotation_names(node.left) | _annotation_names(node.right)
    if isinstance(node, (ast.Tuple, ast.List)):
        names: set[str] = set()
        for element in node.elts:
            names |= _annotation_names(element)
        return names
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        import re
        return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", node.value))
    return set()


def _node_source(source: str, node: ast.AST | None) -> str | None:
    if node is None:
        return None
    return ast.get_source_segment(source, node) or ast.unparse(node)


def _iter_admin_routes() -> list[tuple[str, str, str | None]]:
    """Collect all routes from all admin API files. Returns [(method, path, response_model), ...]."""
    routes: list[tuple[str, str, str | None]] = []
    admin_api_dir = _path("app/api/admin")
    for file_path in sorted(admin_api_dir.glob("*.py")):
        if file_path.name.startswith("_"):
            continue
        source = _read_text(file_path)
        tree = ast.parse(source, filename=str(file_path))

        prefix: str | None = None
        for node in ast.walk(tree):
            value = None
            targets: list[ast.AST] = []
            if isinstance(node, ast.Assign):
                value = node.value
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign):
                value = node.value
                targets = [node.target]
            if not isinstance(value, ast.Call):
                continue
            if not any(isinstance(target, ast.Name) and target.id == "router" for target in targets):
                continue
            func = value.func
            if isinstance(func, ast.Name) and func.id == "APIRouter":
                prefix = _literal_string(_keyword_value(value, "prefix"))
                break

        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                d_func = decorator.func
                if not (
                    isinstance(d_func, ast.Attribute)
                    and d_func.attr in ROUTE_METHODS
                    and isinstance(d_func.value, ast.Name)
                    and d_func.value.id == "router"
                ):
                    continue

                route_path = _literal_string(decorator.args[0]) if decorator.args else ""
                if route_path is None:
                    route_path = ""
                full_path = f"/admin{prefix or ''}{route_path}"
                rm = _node_source(source, _keyword_value(decorator, "response_model"))
                routes.append((d_func.attr.upper(), full_path, rm))

    return routes


class AdminBatch3SystemTests(unittest.TestCase):

    # ── Ticket schemas ──

    def test_admin_ticket_filter_defaults(self):
        schema = importlib.import_module("app.schemas.admin_ticket")
        model = getattr(schema, "AdminTicketFilter", None)
        self.assertIsNotNone(model)
        instance = model()
        self.assertIsNone(instance.status)

    def test_admin_ticket_update_allows_partial_update(self):
        schema = importlib.import_module("app.schemas.admin_ticket")
        model = getattr(schema, "AdminTicketUpdate", None)
        self.assertIsNotNone(model)
        instance = model()
        self.assertIsNone(instance.teacher_id)
        self.assertIsNone(instance.status)
        instance = model(teacher_id=5, status="in_progress")
        self.assertEqual(instance.teacher_id, 5)
        self.assertEqual(instance.status, "in_progress")

    def test_admin_ticket_list_item_has_expected_fields(self):
        schema = importlib.import_module("app.schemas.admin_ticket")
        model = getattr(schema, "AdminTicketListItem", None)
        self.assertIsNotNone(model)
        fields = _field_names(model)
        for f in ("id", "user_id", "teacher_id", "content", "status", "created_at", "updated_at"):
            self.assertIn(f, fields, f"AdminTicketListItem must have field '{f}'")

    # ── Settings schemas ──

    def test_admin_settings_user_create_validates_required_fields(self):
        schema = importlib.import_module("app.schemas.admin_settings")
        model = getattr(schema, "AdminSettingsUserCreate", None)
        self.assertIsNotNone(model)

        with self.assertRaises(ValidationError):
            model()
        instance = model(
            username="  testuser  ", display_name="  测试管理员  "
        )
        self.assertEqual(instance.username, "testuser")
        self.assertEqual(instance.display_name, "测试管理员")
        with self.assertRaises(ValidationError):
            model(username="legacy", display_name="测试管理员", role="super_admin")

    def test_admin_settings_user_create_selects_a_non_super_role_and_rejects_password(self):
        schema = importlib.import_module("app.schemas.admin_settings")
        model = getattr(schema, "AdminSettingsUserCreate", None)
        fields = _field_names(model)
        self.assertEqual(fields, {"username", "display_name", "role"})
        instance = model(
            username="testuser",
            display_name="测试管理员",
            role="quiz_admin",
        )
        self.assertEqual(instance.role, "quiz_admin")
        self.assertEqual(model(username="testuser", display_name="测试管理员").role, "quiz_admin")
        with self.assertRaises(ValidationError):
            model(
                username="testuser",
                display_name="测试管理员",
                password="caller-selected-value-42",
            )

    def test_admin_settings_user_update_only_allows_display_name(self):
        schema = importlib.import_module("app.schemas.admin_settings")
        model = getattr(schema, "AdminSettingsUserUpdate", None)
        self.assertIsNotNone(model)
        with self.assertRaises(ValidationError):
            model()
        instance = model(display_name="题库管理员")
        self.assertEqual(instance.display_name, "题库管理员")
        self.assertNotIn("role", model.model_fields)
        self.assertNotIn("is_active", model.model_fields)
        with self.assertRaises(ValidationError):
            model(display_name="题库管理员", is_active=False)

    def test_admin_settings_user_list_item_has_expected_fields(self):
        schema = importlib.import_module("app.schemas.admin_settings")
        model = getattr(schema, "AdminSettingsUserListItem", None)
        self.assertIsNotNone(model)
        fields = _field_names(model)
        for f in (
            "id", "username", "display_name", "role", "is_active",
            "must_change_password", "locked_until", "last_login_at",
            "created_at", "updated_at",
        ):
            self.assertIn(f, fields, f"AdminSettingsUserListItem must have field '{f}'")
        # Must NOT expose password_hash
        self.assertNotIn("password_hash", fields,
                         "AdminSettingsUserListItem must NOT expose password_hash")

    # ── Route definitions ──

    def test_admin_api_files_exist_for_all_batch3_modules(self):
        modules = ["tickets", "statistics", "settings", "competition"]
        for mod in modules:
            path = _path(f"app/api/admin/{mod}.py")
            self.assertTrue(path.exists(),
                            f"app/api/admin/{mod}.py should exist")

    def test_admin_batch3_api_declares_expected_routes_and_response_models(self):
        routes = _iter_admin_routes()
        self.assertTrue(routes, "admin API should define routes")

        actual = {(method, path) for method, path, _ in routes}

        for method, path in EXPECTED_ADMIN_ENDPOINTS_BATCH3:
            with self.subTest(endpoint=f"{method} {path}"):
                self.assertIn(
                    (method, path), actual,
                    f"admin API missing expected route: {method} {path}",
                )

        # Verify response_model on all target routes (except competition/export which uses response_class)
        target_paths = set(EXPECTED_ADMIN_ENDPOINTS_BATCH3)
        for method, path, rm in routes:
            if (method, path) in target_paths and (method, path) != ("GET", "/admin/competition/export"):
                self.assertIsNotNone(
                    rm, f"{method} {path} must declare explicit response_model",
                )

    # ── Service layer hygiene ──

    def test_batch3_admin_services_do_not_accept_http_objects(self):
        service_files = [
            "app/services/admin_ticket.py",
            "app/services/admin_statistics.py",
            "app/services/admin_settings.py",
            "app/services/admin_competition.py",
        ]
        for relative_path in service_files:
            path = _path(relative_path)
            self.assertTrue(path.exists())
            tree = _load_ast(relative_path)
            violations: list[str] = []

            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if module.startswith(("fastapi", "starlette")):
                        imported_names = {
                            alias.asname or alias.name for alias in node.names
                        }
                        forbidden = imported_names & FORBIDDEN_HTTP_TYPES
                        if forbidden:
                            violations.append(
                                f"{relative_path}:{node.lineno}: imports HTTP types {sorted(forbidden)}"
                            )
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    args = [
                        *node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs,
                    ]
                    if node.args.vararg:
                        args.append(node.args.vararg)
                    if node.args.kwarg:
                        args.append(node.args.kwarg)
                    for arg in args:
                        ann = _annotation_names(arg.annotation)
                        if ann & FORBIDDEN_HTTP_TYPES:
                            violations.append(
                                f"{relative_path}:{arg.lineno}: {node.name} param "
                                f"{arg.arg} annotated as HTTP type"
                            )

            self.assertFalse(violations,
                             f"Admin services must not accept HTTP objects: {violations}")

    # ── Auth dependency ──

    def test_batch3_admin_routes_use_get_current_admin_dependency(self):
        for mod in ["tickets", "statistics", "settings", "competition"]:
            source = _read_text(_path(f"app/api/admin/{mod}.py"))
            with self.subTest(module=mod):
                has_auth = any(
                    dependency in source
                    for dependency in (
                        "get_current_admin",
                        "require_permission",
                        "require_super_admin",
                    )
                )
                self.assertTrue(
                    has_auth,
                    f"app/api/admin/{mod}.py should use get_current_admin or require_permission dependency",
                )

    # ── Business logic verification ──

    def test_ticket_service_raises_not_found_for_missing_ticket(self):
        source = _read_text(_path("app/services/admin_ticket.py"))
        self.assertIn("NotFoundException", source)
        self.assertIn('"工单"', source)

    def test_statistics_dashboard_returns_all_required_fields(self):
        source = _read_text(_path("app/services/admin_statistics.py"))
        self.assertIn("total_users", source)
        self.assertIn("total_orders", source)
        self.assertIn("recent_orders_30d", source)
        self.assertIn("paid_orders", source)
        self.assertIn("revenue_fen", source)
        self.assertIn("recent_revenue_30d_fen", source)
        self.assertIn("conversion_rate", source)

    def test_statistics_dashboard_uses_30_day_window(self):
        source = _read_text(_path("app/services/admin_statistics.py"))
        self.assertIn("timedelta(days=30)", source)

    def test_settings_service_hashes_password_on_create(self):
        source = _read_text(_path("app/services/admin_settings.py"))
        self.assertIn("hash_password", source)
        self.assertIn("password_hash", source)

    def test_settings_service_raises_not_found_for_missing_admin(self):
        source = _read_text(_path("app/services/admin_settings.py"))
        self.assertIn("NotFoundException", source)
        self.assertIn('"管理员"', source)

    def test_competition_export_returns_csv_with_header(self):
        source = _read_text(_path("app/services/admin_competition.py"))
        self.assertIn("csv.writer", source)
        self.assertIn("io.StringIO", source)
        self.assertIn("text/csv", _read_text(_path("app/api/admin/competition.py")))
        self.assertIn("竞赛名称", source)
        self.assertIn("学校", source)

    def test_competition_export_uses_plain_text_response(self):
        source = _read_text(_path("app/api/admin/competition.py"))
        self.assertIn("PlainTextResponse", source)
        self.assertIn("response_class=PlainTextResponse", source)

    # ── AdminUser model ──

    def test_admin_user_model_has_role_constraint(self):
        source = _read_text(_path("app/domain/user/src/model/admin_user.py"))
        self.assertIn("ck_admin_user_role", source)
        self.assertIn("ADMIN_ROLES", source)
        self.assertIn("super_admin", source)
        self.assertIn("QUIZ_ADMIN_ROLE", source)
        self.assertNotIn("content_editor", source)

    def test_admin_user_model_does_not_expose_password_in_schema(self):
        # Verify AdminSettingsUserListItem does NOT include password_hash
        schema = importlib.import_module("app.schemas.admin_settings")
        model = getattr(schema, "AdminSettingsUserListItem", None)
        fields = _field_names(model)
        self.assertNotIn("password_hash", fields)


if __name__ == "__main__":
    unittest.main()
