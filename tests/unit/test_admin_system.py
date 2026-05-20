from __future__ import annotations

import ast
import importlib
import inspect
import re
import unittest
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]

ROUTE_METHODS = {"api_route", "delete", "get", "head", "options", "patch", "post", "put"}
FORBIDDEN_HTTP_TYPES = {
    "Request", "Response", "JSONResponse", "HTMLResponse",
    "PlainTextResponse", "RedirectResponse", "StreamingResponse",
}

EXPECTED_ADMIN_ENDPOINTS = (
    ("POST", "/admin/auth/login"),
    ("GET", "/admin/users"),
    ("GET", "/admin/users/{user_id}"),
    ("PUT", "/admin/users/{user_id}"),
    ("GET", "/admin/orders"),
    ("GET", "/admin/orders/{order_id}"),
    ("POST", "/admin/orders/{order_id}/refund"),
    ("GET", "/admin/courses"),
    ("POST", "/admin/courses"),
    ("PUT", "/admin/courses/{course_id}"),
    ("DELETE", "/admin/courses/{course_id}"),
    ("POST", "/admin/certifications"),
    ("PUT", "/admin/certifications/{cert_id}"),
    ("POST", "/admin/prices"),
    ("PUT", "/admin/prices/{price_id}"),
    ("DELETE", "/admin/prices/{price_id}"),
)

ADMIN_ROLES = ("super_admin", "content_editor", "customer_service", "finance", "auditor")

TARGET_CATEGORIES = {"管理后台-认证", "管理后台-用户管理", "管理后台-订单管理",
                     "管理后台-课程管理", "管理后台-认证管理", "管理后台-价格配置"}


@dataclass(frozen=True)
class RouteInfo:
    function_name: str
    method: str
    path: str
    response_model: str | None
    tags: list[str]


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


def _pydantic_models(module: object) -> list[type[BaseModel]]:
    models: list[type[BaseModel]] = []
    for _, value in inspect.getmembers(module, inspect.isclass):
        if issubclass(value, BaseModel) and value is not BaseModel:
            models.append(value)
    return models


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
        return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", node.value))
    return set()


def _node_source(source: str, node: ast.AST | None) -> str | None:
    if node is None:
        return None
    return ast.get_source_segment(source, node) or ast.unparse(node)


def _iter_admin_routes() -> list[RouteInfo]:
    """Collect all routes from all admin API files under app/api/admin/."""
    routes: list[RouteInfo] = []
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
                full_path = f"/admin{prefix or ''}{route_path}"

                tags: list[str] = []
                tags_kw = _keyword_value(decorator, "tags")
                if isinstance(tags_kw, ast.List):
                    tags = [
                        _literal_string(elt)
                        for elt in tags_kw.elts
                        if _literal_string(elt) is not None
                    ]

                routes.append(RouteInfo(
                    function_name=node.name,
                    method=d_func.attr.upper(),
                    path=full_path,
                    response_model=_node_source(source, _keyword_value(decorator, "response_model")),
                    tags=tags,
                ))

    return routes


class AdminSystemTests(unittest.TestCase):

    # ── Schema validation ──

    def test_admin_login_request_validates_username_and_password_required(self):
        schema = importlib.import_module("app.schemas.admin")
        model = getattr(schema, "AdminLoginRequest", None)
        self.assertIsNotNone(model, "AdminLoginRequest should exist in app.schemas.admin")

        self.assertIn("username", _field_names(model))
        self.assertIn("password", _field_names(model))

        with self.assertRaises(ValidationError):
            model()
        with self.assertRaises(ValidationError):
            model(username="", password="")
        # valid
        instance = model(username="admin", password="admin123")
        self.assertEqual(instance.username, "admin")

    def test_admin_login_response_includes_token_expires_and_admin_info(self):
        schema = importlib.import_module("app.schemas.admin")
        model = getattr(schema, "AdminLoginResponse", None)
        self.assertIsNotNone(model, "AdminLoginResponse should exist")

        fields = _field_names(model)
        self.assertIn("access_token", fields)
        self.assertIn("expires_in", fields)
        self.assertIn("admin", fields)

    def test_admin_user_update_schema_only_allows_is_active(self):
        schema = importlib.import_module("app.schemas.admin")
        model = getattr(schema, "AdminUserUpdate", None)
        self.assertIsNotNone(model, "AdminUserUpdate should exist")

        instance = model(is_active=False)
        self.assertFalse(instance.is_active)
        instance = model(is_active=True)
        self.assertTrue(instance.is_active)

    def test_admin_course_create_validates_required_fields(self):
        schema = importlib.import_module("app.schemas.admin_course")
        model = getattr(schema, "AdminCourseCreate", None)
        self.assertIsNotNone(model, "AdminCourseCreate should exist")

        fields = _field_names(model)
        self.assertIn("title", fields)
        self.assertIn("category", fields)
        self.assertIn("price", fields)

        with self.assertRaises(ValidationError):
            model()
        with self.assertRaises(ValidationError):
            model(title="", category="", price=-1)

        instance = model(title="Test Course", category="networking", price=9900)
        self.assertEqual(instance.title, "Test Course")

    def test_admin_certification_create_validates_required_fields(self):
        schema = importlib.import_module("app.schemas.admin_certification")
        model = getattr(schema, "AdminCertificationCreate", None)
        self.assertIsNotNone(model, "AdminCertificationCreate should exist")

        fields = _field_names(model)
        self.assertIn("name", fields)
        self.assertIn("chinese_name", fields)
        self.assertIn("code", fields)
        self.assertIn("vendor", fields)

        instance = model(
            name="TEST", chinese_name="测试", code="T-001", vendor="H3C"
        )
        self.assertEqual(instance.code, "T-001")

    def test_admin_price_create_validates_required_fields(self):
        schema = importlib.import_module("app.schemas.admin_price")
        model = getattr(schema, "AdminPriceCreate", None)
        self.assertIsNotNone(model, "AdminPriceCreate should exist")

        fields = _field_names(model)
        self.assertIn("cert_type", fields)
        self.assertIn("user_type", fields)
        self.assertIn("price", fields)

        with self.assertRaises(ValidationError):
            model(price=-1)
        instance = model(cert_type="H3C", user_type="student", price=5000)
        self.assertEqual(instance.price, 5000)

    # ── Route definitions ──

    def test_admin_api_files_exist_for_all_target_modules(self):
        modules = ["auth", "users", "orders", "courses", "certifications", "prices"]
        for mod in modules:
            path = _path(f"app/api/admin/{mod}.py")
            self.assertTrue(
                path.exists(),
                f"app/api/admin/{mod}.py should exist for admin {mod} module",
            )

    def test_admin_api_declares_expected_routes_and_response_models(self):
        routes = _iter_admin_routes()
        self.assertTrue(routes, "admin API should define routes")

        actual = {(route.method, route.path): route for route in routes}

        # Verify target endpoints exist
        for method, path in EXPECTED_ADMIN_ENDPOINTS:
            with self.subTest(endpoint=f"{method} {path}"):
                self.assertIn(
                    (method, path), actual,
                    f"admin API missing expected route: {method} {path}",
                )

        # Verify only target modules have response_model
        target_methods_paths = {(m, p) for m, p in EXPECTED_ADMIN_ENDPOINTS}
        for key, route in actual.items():
            if key in target_methods_paths:
                self.assertIsNotNone(
                    route.response_model,
                    f"{route.method} {route.path} ({route.function_name}) "
                    f"must declare explicit response_model",
                )
                self.assertNotEqual(
                    route.response_model, "None",
                    f"{route.method} {route.path} response_model must not be None",
                )

    # ── Service layer hygiene ──

    def test_admin_services_do_not_accept_http_request_or_response_objects(self):
        service_files = [
            "app/services/admin_auth.py",
            "app/services/admin_user.py",
            "app/services/admin_order.py",
            "app/services/admin_course.py",
            "app/services/admin_certification.py",
            "app/services/admin_price.py",
        ]
        for relative_path in service_files:
            path = _path(relative_path)
            self.assertTrue(path.exists(),
                            f"{relative_path} should contain the admin service layer")

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
                        annotation_names = _annotation_names(arg.annotation)
                        if annotation_names & FORBIDDEN_HTTP_TYPES:
                            violations.append(
                                f"{relative_path}:{arg.lineno}: {node.name} param "
                                f"{arg.arg} annotated as HTTP type"
                            )

            self.assertFalse(
                violations,
                f"Admin service functions must not accept HTTP transport objects: {violations}",
            )

    # ── Auth dependency ──

    def test_admin_routes_use_get_current_admin_dependency(self):
        for module_name in ["users", "orders", "courses", "certifications", "prices"]:
            source = _read_text(_path(f"app/api/admin/{module_name}.py"))
            with self.subTest(module=module_name):
                self.assertIn(
                    "get_current_admin",
                    source,
                    f"app/api/admin/{module_name}.py should use get_current_admin dependency",
                )

    def test_auth_middleware_checks_admin_token_type(self):
        source = _read_text(_path("app/middleware/auth.py"))
        self.assertIn('"admin"', source)
        self.assertIn('payload.get("type")', source)
        self.assertIn("!= ", source)

    def test_create_admin_access_token_includes_type_and_role(self):
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        from app.core.security import create_admin_access_token, decode_access_token

        token = create_admin_access_token(1, "admin", "super_admin")
        payload = decode_access_token(token)

        self.assertEqual(payload["type"], "admin")
        self.assertEqual(payload["admin_id"], 1)
        self.assertEqual(payload["username"], "admin")
        self.assertEqual(payload["role"], "super_admin")
        self.assertIn("exp", payload)
        self.assertIn("iat", payload)

    # ── Admin model ──

    def test_admin_user_model_defines_role_constraint(self):
        model_file = _path("app/models/admin_user.py")
        self.assertTrue(model_file.exists())
        source = _read_text(model_file)

        self.assertIn("ADMIN_ROLES", source)
        for role in ADMIN_ROLES:
            self.assertIn(role, source, f"admin model should include role '{role}'")

    def test_admin_password_hashing_uses_pbkdf2(self):
        source = _read_text(_path("app/services/admin_auth.py"))
        self.assertIn("pbkdf2_hmac", source)
        self.assertIn("salt", source)
        self.assertIn("600_000", source)
        self.assertIn("secrets.compare_digest", source)


if __name__ == "__main__":
    unittest.main()
