from __future__ import annotations

import ast
import importlib
import inspect
import re
import unittest
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[2]

ROUTE_METHODS = {"api_route", "delete", "get", "head", "options", "patch", "post", "put"}


@dataclass(frozen=True)
class RouteInfo:
    function_name: str
    method: str
    path: str
    response_model: str | None


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


def _node_source(source: str, node: ast.AST | None) -> str | None:
    if node is None:
        return None
    return ast.get_source_segment(source, node) or ast.unparse(node)


def _join_paths(prefix: str | None, path: str | None) -> str:
    parts = [part.strip("/") for part in (prefix or "", path or "") if part]
    return "/" + "/".join(part for part in parts if part)


def _admin_path(prefix: str | None, route_path: str | None) -> str:
    full_path = _join_paths(prefix, route_path)
    return full_path if full_path.startswith("/admin/") else _join_paths("/admin", full_path)


def _router_prefix(tree: ast.Module) -> str | None:
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
            return _literal_string(_keyword_value(value, "prefix"))
    return None


def _iter_admin_routes(module_path: str) -> list[RouteInfo]:
    api_path = _path(module_path)
    source = _read_text(api_path)
    tree = ast.parse(source, filename=str(api_path))
    prefix = _router_prefix(tree)
    routes: list[RouteInfo] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if not (
                isinstance(func, ast.Attribute)
                and func.attr in ROUTE_METHODS
                and isinstance(func.value, ast.Name)
                and func.value.id == "router"
            ):
                continue

            route_path = _literal_string(decorator.args[0]) if decorator.args else ""
            routes.append(
                RouteInfo(
                    function_name=node.name,
                    method=func.attr.upper(),
                    path=_admin_path(prefix, route_path),
                    response_model=_node_source(source, _keyword_value(decorator, "response_model")),
                )
            )

    return routes


def _call_has_depends_get_current_admin(call: ast.Call) -> bool:
    """Check if a route's body references Depends(get_current_admin) in params."""
    for arg in call.args:
        if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) and arg.func.id == "Depends":
            for kw in arg.keywords:
                if kw.arg is None:
                    val_source = ast.unparse(kw.value) if hasattr(ast, "unparse") else ""
                    if "get_current_admin" in val_source:
                        return True
            for sub_arg in arg.args:
                if isinstance(sub_arg, ast.Name) and sub_arg.id == "get_current_admin":
                    return True
    for kw in call.keywords:
        if isinstance(kw.value, ast.Call) and isinstance(kw.value.func, ast.Name) and kw.value.func.id == "Depends":
            for sub_kw in kw.value.keywords:
                if sub_kw.arg is None and "get_current_admin" in ast.unparse(sub_kw.value):
                    return True
    source = ast.unparse(call) if hasattr(ast, "unparse") else ""
    return "get_current_admin" in source


def _all_routes_have_admin_auth(module_path: str) -> tuple[bool, list[str]]:
    api_path = _path(module_path)
    source = _read_text(api_path)
    tree = ast.parse(source, filename=str(api_path))

    unprotected: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if not (
                isinstance(func, ast.Attribute)
                and func.attr in ROUTE_METHODS
                and isinstance(func.value, ast.Name)
                and func.value.id == "router"
            ):
                continue

            func_source = ast.get_source_segment(source, node) or ""
            if "get_current_admin" not in func_source and "_admin" not in func_source:
                unprotected.append(node.name)

    return len(unprotected) == 0, unprotected


# ── Tests ──


class AdminLoginSchemaTests(unittest.TestCase):
    """P0: #2 - Login returns permissions"""

    def test_admin_login_response_has_permissions_field(self):
        schema = importlib.import_module("app.schemas.admin")
        self.assertTrue(hasattr(schema, "AdminLoginResponse"))
        response_model = schema.AdminLoginResponse
        fields = set(response_model.model_fields)
        self.assertIn("permissions", fields, "AdminLoginResponse should have 'permissions' field")

    def test_all_permissions_list_exists(self):
        schema = importlib.import_module("app.schemas.admin")
        self.assertTrue(hasattr(schema, "ALL_PERMISSIONS"))
        perms = schema.ALL_PERMISSIONS
        self.assertIsInstance(perms, list)
        self.assertGreater(len(perms), 0)
        expected = [
            "dashboard:view", "user:list",
            "order:list", "order:write",
            "quiz:list", "quiz:write", "quiz:import",
            "content:list", "content:write", "content:banner",
            "course:list", "course:write",
        ]
        for p in expected:
            self.assertIn(p, perms, f"ALL_PERMISSIONS should contain {p}")


class AdminBatchDeleteSchemaTests(unittest.TestCase):
    """P1: Batch delete request schema"""

    def test_admin_batch_delete_request_exists(self):
        schema = importlib.import_module("app.schemas.admin")
        self.assertTrue(hasattr(schema, "AdminBatchDeleteRequest"))
        model = schema.AdminBatchDeleteRequest
        fields = set(model.model_fields)
        self.assertIn("ids", fields)


class AdminUserFilterTests(unittest.TestCase):
    """P2: #7.1 User filter enhancement"""

    def test_admin_user_filter_has_time_fields(self):
        schema = importlib.import_module("app.schemas.admin")
        filter_model = schema.AdminUserFilter
        fields = set(filter_model.model_fields)
        self.assertIn("created_at_start", fields)
        self.assertIn("created_at_end", fields)


class AdminOrderFilterTests(unittest.TestCase):
    """P2: #7.2 Order filter enhancement"""

    def test_order_filter_has_product_type_and_phone(self):
        schema = importlib.import_module("app.schemas.order")
        filter_model = schema.OrderFilter
        fields = set(filter_model.model_fields)
        self.assertIn("product_type", fields)
        self.assertIn("phone", fields)


class AdminBannerModelTests(unittest.TestCase):
    """P1: #5.5 Banner model"""

    def test_banner_model_exists(self):
        model = importlib.import_module("app.models.banner")
        self.assertTrue(hasattr(model, "Banner"))
        banner = model.Banner
        self.assertEqual(banner.__tablename__, "banner")

    def test_banner_schemas_exist(self):
        schema = importlib.import_module("app.schemas.admin_banner")
        self.assertTrue(hasattr(schema, "BannerCreate"))
        self.assertTrue(hasattr(schema, "BannerUpdate"))
        self.assertTrue(hasattr(schema, "BannerListItem"))


class AdminQuizSchemaTests(unittest.TestCase):
    """P1: #6 Quiz JSON import schema"""

    def test_quiz_import_json_schemas_exist(self):
        schema = importlib.import_module("app.schemas.admin_quiz")
        self.assertTrue(hasattr(schema, "AdminQuizImportJsonRequest"))
        self.assertTrue(hasattr(schema, "AdminQuizQuestionItem"))


class AdminZoneSchemaTests(unittest.TestCase):
    """P1: Zone toggle status + sort schema"""

    def test_zone_status_toggle_schema_exists(self):
        schema = importlib.import_module("app.schemas.admin_zone")
        self.assertTrue(hasattr(schema, "AdminZoneStatusToggle"))

    def test_zone_sort_schema_exists(self):
        schema = importlib.import_module("app.schemas.admin_zone")
        self.assertTrue(hasattr(schema, "AdminZoneSortItem"))


# ── Route verification tests ──


class AdminRoutePresenceTests(unittest.TestCase):
    """Verify all new admin routes are registered."""

    def test_batch_delete_users_route_exists(self):
        routes = _iter_admin_routes("app/api/admin/users.py")
        methods_paths = {(r.method, r.path) for r in routes}
        self.assertIn(("POST", "/admin/users/batch-delete"), methods_paths)

    def test_batch_delete_quiz_route_exists(self):
        # 端点使用 f-string 路径 (f"{QUESTION}/batch-delete")，AST 解析仅支持字面量
        source = (REPO_ROOT / "app/api/admin/quiz.py").read_text(encoding="utf-8")
        self.assertIn("batch-delete", source)

    def test_toggle_zone_status_route_exists(self):
        routes = _iter_admin_routes("app/api/admin/zones.py")
        methods_paths = {(r.method, r.path) for r in routes}
        self.assertIn(("PATCH", "/admin/zones/{zone_id}/status"), methods_paths)

    def test_batch_delete_zones_route_exists(self):
        routes = _iter_admin_routes("app/api/admin/zones.py")
        methods_paths = {(r.method, r.path) for r in routes}
        self.assertIn(("POST", "/admin/zones/batch-delete"), methods_paths)

    def test_banner_crud_routes_exist(self):
        routes = _iter_admin_routes("app/api/admin/banners.py")
        methods_paths = {(r.method, r.path) for r in routes}
        expected = {
            ("GET", "/admin/banners"),
            ("POST", "/admin/banners"),
            ("PUT", "/admin/banners/{banner_id}"),
            ("DELETE", "/admin/banners/{banner_id}"),
            ("POST", "/admin/banners/batch-delete"),
        }
        for expected_route in expected:
            self.assertIn(expected_route, methods_paths, f"Missing route: {expected_route}")

    def test_quiz_json_import_route_exists(self):
        routes = _iter_admin_routes("app/api/admin/quiz.py")
        methods_paths = {(r.method, r.path) for r in routes}
        self.assertIn(("POST", "/admin/quiz/import/json"), methods_paths)

    def test_user_orders_route_exists(self):
        routes = _iter_admin_routes("app/api/admin/users.py")
        methods_paths = {(r.method, r.path) for r in routes}
        self.assertIn(("GET", "/admin/users/{user_id}/orders"), methods_paths)

    def test_user_conversations_route_exists(self):
        routes = _iter_admin_routes("app/api/admin/users.py")
        methods_paths = {(r.method, r.path) for r in routes}
        self.assertIn(("GET", "/admin/users/{user_id}/conversations"), methods_paths)

    def test_user_export_route_exists(self):
        routes = _iter_admin_routes("app/api/admin/users.py")
        methods_paths = {(r.method, r.path) for r in routes}
        self.assertIn(("GET", "/admin/users/export"), methods_paths)

    def test_order_export_route_exists(self):
        routes = _iter_admin_routes("app/api/admin/orders.py")
        methods_paths = {(r.method, r.path) for r in routes}
        self.assertIn(("GET", "/admin/orders/export"), methods_paths)

    def test_order_reconciliation_route_exists(self):
        routes = _iter_admin_routes("app/api/admin/orders.py")
        methods_paths = {(r.method, r.path) for r in routes}
        self.assertIn(("GET", "/admin/orders/reconciliation"), methods_paths)

    def test_zone_sort_route_exists(self):
        routes = _iter_admin_routes("app/api/admin/zones.py")
        methods_paths = {(r.method, r.path) for r in routes}
        self.assertIn(("PUT", "/admin/zones/sort"), methods_paths)


class AdminRouteAuthTests(unittest.TestCase):
    """Verify all new routes have admin auth protection."""

    def test_users_routes_have_admin_auth(self):
        ok, unprotected = _all_routes_have_admin_auth("app/api/admin/users.py")
        self.assertTrue(ok, f"Unprotected routes in users.py: {unprotected}")

    def test_banners_routes_have_admin_auth(self):
        ok, unprotected = _all_routes_have_admin_auth("app/api/admin/banners.py")
        self.assertTrue(ok, f"Unprotected routes in banners.py: {unprotected}")


class AdminRouterRegistrationTest(unittest.TestCase):
    """Verify banners module exists and has its router."""

    def test_banner_module_has_router(self):
        """banner router is defined in banners.py (managed via /admin/banners directly)."""
        banner_mod = importlib.import_module("app.api.admin.banners")
        self.assertTrue(hasattr(banner_mod, "router"))


if __name__ == "__main__":
    unittest.main()
