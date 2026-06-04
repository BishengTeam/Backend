import ast
import unittest
from pathlib import Path

from pydantic import ValidationError

from app.schemas.certification import CertificationFilter, CertificationResponse, Vendor
from app.schemas.course import (
    CourseDetailResponse,
    CourseEnrollRequest,
    CourseEnrollmentResponse,
    CourseFilter,
    CourseListResponse,
    EnrollmentStatus,
)
from app.schemas.system import PosterResponse
from app.schemas.common import APIResponse, PaginatedData, success, created

REPO_ROOT = Path(__file__).resolve().parents[2]

ROUTE_METHODS = {"api_route", "delete", "get", "head", "options", "patch", "post", "put"}
FORBIDDEN_HTTP_TYPES = {
    "Request", "Response", "JSONResponse", "HTMLResponse",
    "PlainTextResponse", "RedirectResponse", "StreamingResponse",
}

COURSES_EXPECTED_ROUTES = (
    ("GET", "/api/courses"),
    ("GET", "/api/courses/my"),
    ("GET", "/api/courses/{course_id}"),
    ("POST", "/api/courses/enroll"),
)

CERT_EXPECTED_ROUTES = (
    ("GET", "/api/cert/certifications"),
)

SYSTEM_EXPECTED_ROUTES = (
    ("GET", "/api/system/poster"),
)

PRICES_EXPECTED_ROUTES = (
    ("GET", "/api/prices"),
)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _load_ast(relative_path: str) -> ast.Module:
    return ast.parse(_read_text(REPO_ROOT / relative_path), filename=relative_path)


def _literal_string(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    try:
        value = ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None
    return value if isinstance(value, str) else None


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
        for elt in node.elts:
            names |= _annotation_names(elt)
        return names
    return set()


def _service_layer_violations(relative_path: str) -> list[str]:
    tree = _load_ast(relative_path)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith(("fastapi", "starlette")):
                imported = {alias.asname or alias.name for alias in node.names}
                forbidden = imported & FORBIDDEN_HTTP_TYPES
                if forbidden:
                    violations.append(f"line {node.lineno}: imports {sorted(forbidden)}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            if node.args.vararg:
                args.append(node.args.vararg)
            if node.args.kwarg:
                args.append(node.args.kwarg)
            for arg in args:
                names = _annotation_names(arg.annotation)
                if names & FORBIDDEN_HTTP_TYPES:
                    violations.append(
                        f"line {arg.lineno}: {node.name} param {arg.arg} annotated with {sorted(names & FORBIDDEN_HTTP_TYPES)}"
                    )
    return violations


def _api_decorator_has_response_model(file_path_relative: str, router_var: str = "router") -> dict[str, bool]:
    tree = _load_ast(file_path_relative)
    result: dict[str, bool] = {}
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
                and func.value.id == router_var
            ):
                continue
            result[node.name] = any(kw.arg == "response_model" for kw in decorator.keywords)
    return result


def _route_paths(file_path_relative: str, router_var: str = "router") -> set[tuple[str, str]]:
    """Return {(METHOD, path)} for all routes."""
    tree = _load_ast(file_path_relative)
    prefix = None
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
        if not any(isinstance(t, ast.Name) and t.id == router_var for t in targets):
            continue
        f = value.func
        if isinstance(f, ast.Name) and f.id == "APIRouter":
            prefix = _literal_string(next(
                (kw.value for kw in value.keywords if kw.arg == "prefix"), None) if hasattr(value, 'keywords') else None)

    result: set[tuple[str, str]] = set()
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
                and func.value.id == router_var
            ):
                continue
            p = _literal_string(decorator.args[0]) if decorator.args else ""
            full = (prefix or "") + (p or "")
            result.add((func.attr.upper(), full))
    return result


class CoursesSystemTests(unittest.TestCase):

    # --- Courses API routes ---

    def test_courses_api_routes_declare_explicit_response_model(self):
        rms = _api_decorator_has_response_model("app/api/courses.py")
        self.assertTrue(rms, "app/api/courses.py should define routes")
        missing = [name for name, has_rm in rms.items() if not has_rm]
        self.assertFalse(missing, f"courses API routes missing explicit response_model: {missing}")

    # --- Certification API routes ---

    def test_certification_api_routes_declare_explicit_response_model(self):
        rms = _api_decorator_has_response_model("app/api/certification.py")
        self.assertTrue(rms, "app/api/certification.py should define routes")
        missing = [name for name, has_rm in rms.items() if not has_rm]
        self.assertFalse(missing, f"certification API routes missing explicit response_model: {missing}")

    # --- System API routes ---

    def test_system_api_routes_declare_explicit_response_model(self):
        rms = _api_decorator_has_response_model("app/api/system.py")
        self.assertTrue(rms, "app/api/system.py should define routes")
        missing = [name for name, has_rm in rms.items() if not has_rm]
        self.assertFalse(missing, f"system API routes missing explicit response_model: {missing}")

    # --- Price config API routes ---

    def test_price_config_api_routes_declare_explicit_response_model(self):
        rms = _api_decorator_has_response_model("app/api/price_config.py")
        self.assertTrue(rms, "app/api/price_config.py should define routes")
        missing = [name for name, has_rm in rms.items() if not has_rm]
        self.assertFalse(missing, f"price_config API routes missing explicit response_model: {missing}")

    # --- Service layering ---

    def test_course_service_does_not_accept_http_request_or_response_objects(self):
        violations = _service_layer_violations("app/services/course.py")
        self.assertFalse(violations, f"CourseService should not use HTTP transport types: {violations}")

    def test_certification_service_does_not_accept_http_request_or_response_objects(self):
        violations = _service_layer_violations("app/services/certification.py")
        self.assertFalse(violations, f"CertificationService should not use HTTP transport types: {violations}")

    # --- Schema validation: Courses ---

    def test_course_list_response_has_expected_fields(self):
        r = CourseListResponse(id=1, title="课程A", category="网络", price=9900, teacher_name="王老师")
        self.assertEqual(r.id, 1)
        self.assertEqual(r.title, "课程A")
        self.assertEqual(r.price, 9900)
        self.assertIsNone(r.description)
        with self.assertRaises(ValidationError):
            CourseListResponse(id=1)

    def test_course_detail_response_has_expected_fields(self):
        r = CourseDetailResponse(id=1, title="课程A", category="网络", price=9900)
        self.assertEqual(r.video_url, None)
        self.assertEqual(r.batches, None)

    def test_course_enroll_request_requires_course_id(self):
        r = CourseEnrollRequest(course_id=1)
        self.assertEqual(r.course_id, 1)
        with self.assertRaises(ValidationError):
            CourseEnrollRequest()

    def test_course_enrollment_response_has_nested_course(self):
        course = CourseListResponse(id=1, title="课程A", category="网络", price=9900)
        r = CourseEnrollmentResponse(
            id=100, course=course, status="enrolled", learning_access=True,
            created_at="2026-01-01T00:00:00Z",
        )
        self.assertEqual(r.id, 100)
        self.assertEqual(r.course.title, "课程A")
        self.assertEqual(r.status, "enrolled")
        self.assertTrue(r.learning_access)

    def test_course_filter_is_optional(self):
        f = CourseFilter()
        self.assertIsNone(f.category)
        f2 = CourseFilter(category="网络")
        self.assertEqual(f2.category, "网络")

    # --- Schema validation: Certification ---

    def test_certification_response_has_expected_fields(self):
        r = CertificationResponse(
            id=1, name="H3CNE", chinese_name="H3C认证网络工程师",
            code="H3C-NE", vendor="H3C", requires_xuexin=False, pay_first=False,
        )
        self.assertEqual(r.vendor, "H3C")
        self.assertFalse(r.pay_first)
        with self.assertRaises(ValidationError):
            CertificationResponse(id=1, name="H3CNE", vendor="H3C")

    def test_vendor_literal_accepts_known_values(self):
        r = CertificationResponse(
            id=1, name="NISP一级", chinese_name="NISP", code="NISP-1",
            vendor="NISP", requires_xuexin=False, pay_first=True,
        )
        self.assertEqual(r.vendor, "NISP")

    def test_vendor_literal_rejects_unknown_values(self):
        with self.assertRaises(ValidationError):
            CertificationResponse(
                id=1, name="X", chinese_name="X", code="X",
                vendor="Cisco", requires_xuexin=False, pay_first=False,
            )

    def test_certification_filter_accepts_valid_vendor(self):
        for v in ("H3C", "深信服", "NISP", "人社"):
            with self.subTest(vendor=v):
                f = CertificationFilter(vendor=v)
                self.assertEqual(f.vendor, v)

    def test_certification_filter_rejects_invalid_vendor(self):
        with self.assertRaises(ValidationError):
            CertificationFilter(vendor="InvalidVendor")

    # --- Schema validation: System ---

    def test_poster_response_has_optional_url(self):
        r = PosterResponse()
        self.assertIsNone(r.url)
        r2 = PosterResponse(url="https://example.com/poster.png")
        self.assertEqual(r2.url, "https://example.com/poster.png")

    # --- Common schema helpers ---

    def test_api_response_defaults(self):
        r = APIResponse()
        self.assertEqual(r.code, 0)
        self.assertEqual(r.message, "ok")
        self.assertIsNone(r.data)
        r2 = APIResponse[str](code=0, message="ok", data="hello")
        self.assertEqual(r2.data, "hello")

    def test_success_helper(self):
        r = success()
        self.assertEqual(r.code, 0)
        self.assertEqual(r.message, "ok")
        self.assertIsNone(r.data)
        r2 = success(data={"key": "val"}, message="done")
        self.assertEqual(r2.data, {"key": "val"})
        self.assertEqual(r2.message, "done")

    def test_created_helper(self):
        r = created(data={"id": 1})
        self.assertEqual(r.code, 0)
        self.assertEqual(r.message, "创建成功")
        self.assertEqual(r.data, {"id": 1})

    def test_paginated_data_structure(self):
        items = [CourseListResponse(id=i, title=f"课程{i}", category="网络", price=9900) for i in range(3)]
        p = PaginatedData[CourseListResponse](items=items, total=10, page=1, page_size=20)
        self.assertEqual(p.total, 10)
        self.assertEqual(len(p.items), 3)
        self.assertEqual(p.page, 1)
        self.assertEqual(p.page_size, 20)

    # --- Main app health endpoint ---

    def test_main_app_has_health_endpoint(self):
        source = _read_text(REPO_ROOT / "app/main.py")
        self.assertIn("/health", source)
        self.assertIn("@app.get", source)

    def test_main_app_registers_api_router(self):
        source = _read_text(REPO_ROOT / "app/main.py")
        self.assertIn("include_router", source)
        self.assertIn("api_router", source)

    def test_api_init_includes_all_module_routers(self):
        source = _read_text(REPO_ROOT / "app/api/__init__.py")
        expected_modules = ["auth", "user", "chat", "courses", "orders", "payment", "quiz", "system", "cert", "prices"]
        for mod in expected_modules:
            with self.subTest(module=mod):
                self.assertIn(mod, source, f"api/__init__.py should include {mod} router")


if __name__ == "__main__":
    unittest.main()
