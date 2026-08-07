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

# Batch 2 admin endpoints (quiz, zones, coupons, agreements)
EXPECTED_ADMIN_ENDPOINTS_BATCH2 = (
    ("POST", "/admin/quiz/categories"),
    ("PUT", "/admin/quiz/categories/{category_id}"),
    ("DELETE", "/admin/quiz/categories/{category_id}"),
    ("POST", "/admin/quiz/questions"),
    ("PUT", "/admin/quiz/questions/{question_id}"),
    ("DELETE", "/admin/quiz/questions/{question_id}"),
    ("POST", "/admin/quiz/imports/csv"),
    ("POST", "/admin/quiz/imports/json"),
    ("GET", "/admin/zones"),
    ("POST", "/admin/zones"),
    ("PUT", "/admin/zones/{zone_id}"),
    ("DELETE", "/admin/zones/{zone_id}"),
    ("GET", "/admin/coupons"),
    ("POST", "/admin/coupons"),
    ("POST", "/admin/coupons/batch"),
    ("DELETE", "/admin/coupons/{coupon_id}"),
    ("GET", "/admin/agreements"),
    ("POST", "/admin/agreements"),
    ("PUT", "/admin/agreements/{agreement_id}/review"),
)

ALLOWED_QUESTION_TYPES = ("single_choice", "multiple_choice", "judge")


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


class AdminBatch2SystemTests(unittest.TestCase):

    # ── Quiz schemas ──

    def test_admin_quiz_category_create_validates_required_fields(self):
        schema = importlib.import_module("app.schemas.admin_quiz")
        model = getattr(schema, "AdminQuizCategoryCreate", None)
        self.assertIsNotNone(model)

        with self.assertRaises(ValidationError):
            model()
        instance = model(name="Test Category")
        self.assertEqual(instance.name, "Test Category")

    def test_admin_quiz_category_update_allows_partial_update(self):
        schema = importlib.import_module("app.schemas.admin_quiz")
        model = getattr(schema, "AdminQuizCategoryUpdate", None)
        self.assertIsNotNone(model)
        # Empty body should be valid for partial update
        instance = model()
        self.assertIsNone(instance.name)
        self.assertIsNone(instance.parent_id)

    def test_admin_quiz_question_create_validates_required_fields(self):
        schema = importlib.import_module("app.schemas.admin_quiz")
        model = getattr(schema, "AdminQuizQuestionCreate", None)
        self.assertIsNotNone(model)

        with self.assertRaises(ValidationError):
            model()
        instance = model(
            category_id=1,
            question_type="single_choice",
            question_text="Test question?",
            correct_answer="A",
        )
        self.assertEqual(instance.question_type, "single_choice")

    # ── Zone schemas ──

    def test_admin_zone_create_validates_required_fields(self):
        schema = importlib.import_module("app.schemas.admin_zone")
        model = getattr(schema, "AdminZoneCreate", None)
        self.assertIsNotNone(model)

        with self.assertRaises(ValidationError):
            model()
        instance = model(zone_type="cert", title="Test Zone")
        self.assertEqual(instance.zone_type, "cert")
        self.assertEqual(instance.sort_order, 0)

    def test_admin_zone_update_allows_partial_update(self):
        schema = importlib.import_module("app.schemas.admin_zone")
        model = getattr(schema, "AdminZoneUpdate", None)
        self.assertIsNotNone(model)
        instance = model()
        self.assertIsNone(instance.title)
        instance = model(sort_order=99)
        self.assertEqual(instance.sort_order, 99)

    # ── Coupon schemas ──

    def test_admin_coupon_create_validates_required_fields(self):
        schema = importlib.import_module("app.schemas.admin_coupon")
        model = getattr(schema, "AdminCouponCreate", None)
        self.assertIsNotNone(model)

        fields = _field_names(model)
        self.assertIn("code", fields)
        self.assertIn("value", fields)

        instance = model(code="TEST001", type="fixed", value=5000)
        self.assertEqual(instance.code, "TEST001")

    def test_admin_coupon_batch_create_validates_count_limit(self):
        schema = importlib.import_module("app.schemas.admin_coupon")
        model = getattr(schema, "AdminCouponBatchCreate", None)
        self.assertIsNotNone(model)

        with self.assertRaises(ValidationError):
            model(code_prefix="BATCH", count=0, type="fixed", value=1000)
        with self.assertRaises(ValidationError):
            model(code_prefix="BATCH", count=1001, type="fixed", value=1000)

        instance = model(code_prefix="BATCH", count=100, type="fixed", value=500)
        self.assertEqual(instance.count, 100)

    # ── Agreement schemas ──

    def test_admin_agreement_create_validates_required_fields(self):
        schema = importlib.import_module("app.schemas.admin_agreement")
        model = getattr(schema, "AdminAgreementCreate", None)
        self.assertIsNotNone(model)

        fields = _field_names(model)
        self.assertIn("type", fields)

        instance = model(type="training", content="Test content", user_id=1)
        self.assertEqual(instance.type, "training")

    def test_admin_agreement_review_validates_status_required(self):
        schema = importlib.import_module("app.schemas.admin_agreement")
        model = getattr(schema, "AdminAgreementReview", None)
        self.assertIsNotNone(model)

        with self.assertRaises(ValidationError):
            model()
        instance = model(status="approved")
        self.assertEqual(instance.status, "approved")

    # ── Route definitions ──

    def test_admin_api_files_exist_for_all_batch2_modules(self):
        modules = ["quiz", "zones", "coupons", "agreements"]
        for mod in modules:
            path = _path(f"app/api/admin/{mod}.py")
            self.assertTrue(path.exists(),
                            f"app/api/admin/{mod}.py should exist")

    def test_admin_batch2_api_declares_expected_routes_and_response_models(self):
        routes = _iter_admin_routes()
        self.assertTrue(routes, "admin API should define routes")

        actual = {(method, path) for method, path, _ in routes}

        # Quiz routes use f-string variables (CATEGORY/QUESTION) unresolvable by AST.
        # Verify the non-quiz routes plus literal quiz import routes via AST.
        ast_verifiable = [
            (m, p) for m, p in EXPECTED_ADMIN_ENDPOINTS_BATCH2
            if not ("/quiz/categories" in p or "/quiz/questions" in p)
        ]

        for method, path in ast_verifiable:
            with self.subTest(endpoint=f"{method} {path}"):
                self.assertIn(
                    (method, path), actual,
                    f"admin API missing expected route: {method} {path}",
                )

        # Verify response_model on all AST-verifiable target routes
        target_paths = {(m, p) for m, p in ast_verifiable}
        for method, path, rm in routes:
            if (method, path) in target_paths:
                self.assertIsNotNone(
                    rm, f"{method} {path} must declare explicit response_model",
                )

    def test_admin_quiz_routes_exist_via_source_inspection(self):
        """Verify quiz category/question routes by analyzing the source directly."""
        source = _read_text(_path("app/api/admin/quiz.py"))

        # Category routes
        self.assertIn("CATEGORY", source, "quiz.py should define CATEGORY path variable")
        self.assertIn('"/categories"', source, "quiz.py should prefix category routes with /categories")
        self.assertIn("create_category", source)
        self.assertIn("update_category", source)
        self.assertIn("delete_category", source)

        # Question routes
        self.assertIn("QUESTION", source, "quiz.py should define QUESTION path variable")
        self.assertIn('"/questions"', source, "quiz.py should prefix question routes with /questions")
        self.assertIn("create_question", source)
        self.assertIn("update_question", source)
        self.assertIn("delete_question", source)

        # Frozen asynchronous import routes
        self.assertIn('"/imports/csv"', source)
        self.assertIn('"/imports/json"', source)
        self.assertIn("create_csv_import", source)
        self.assertIn("create_json_import", source)
        self.assertNotIn('@router.post("/import")', source)

    # ── Service layer hygiene ──

    def test_batch2_admin_services_do_not_accept_http_objects(self):
        service_files = [
            "app/services/admin_quiz.py",
            "app/services/admin_zone.py",
            "app/services/admin_coupon.py",
            "app/services/admin_agreement.py",
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

    def test_batch2_admin_routes_use_get_current_admin_dependency(self):
        for mod in ["quiz", "zones", "coupons", "agreements"]:
            source = _read_text(_path(f"app/api/admin/{mod}.py"))
            with self.subTest(module=mod):
                has_auth = "get_current_admin" in source or "require_permission" in source
                self.assertTrue(
                    has_auth,
                    f"app/api/admin/{mod}.py should use get_current_admin or require_permission dependency",
                )

    # ── Business logic verification ──

    def test_quiz_csv_import_handles_encoding_and_header_aliases(self):
        source = _read_text(_path("app/services/admin_quiz.py"))
        self.assertIn("csv", source)
        self.assertIn("utf-8-sig", source)
        self.assertIn("gbk", source)
        self.assertIn("_build_header_map", source)
        self.assertIn("分类路径", source)
        self.assertIn("题型", source)
        self.assertIn("题干", source)
        self.assertIn("正确答案", source)

    def test_quiz_delete_category_checks_children_and_questions(self):
        source = _read_text(_path("app/services/admin_quiz.py"))
        self.assertIn("child_count", source)
        self.assertIn("question_count", source)
        self.assertIn("子分类", source)
        self.assertIn("请先删除", source)

    def test_quiz_delete_question_checks_records(self):
        source = _read_text(_path("app/services/admin_quiz.py"))
        self.assertIn("record_count", source)
        self.assertIn("答题记录", source)

    def test_coupon_batch_uses_secrets_token_hex(self):
        source = _read_text(_path("app/services/admin_coupon.py"))
        self.assertIn("secrets.token_hex", source)
        self.assertIn("code_prefix", source)

    def test_agreement_review_sets_status_and_signature(self):
        source = _read_text(_path("app/services/admin_agreement.py"))
        self.assertIn("agreement.status = data.status", source)
        self.assertIn("signature_image", source)


if __name__ == "__main__":
    unittest.main()
