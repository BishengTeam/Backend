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

EXPECTED_POINTS_ENDPOINTS = (
    ("GET", "/api/points"),
    ("GET", "/api/points/history"),
    ("POST", "/api/points/claim"),
    ("POST", "/api/points/redeem"),
)

CLAIM_SCENES = ("daily_checkin", "quiz_task", "new_user", "activity")
REDEEM_TYPES = ("exam_discount", "course")
ROUTE_METHODS = {"api_route", "delete", "get", "head", "options", "patch", "post", "put"}
FORBIDDEN_HTTP_TYPES = {
    "Request",
    "Response",
    "JSONResponse",
    "HTMLResponse",
    "PlainTextResponse",
    "RedirectResponse",
    "StreamingResponse",
}


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


def _api_path(prefix: str | None, route_path: str | None) -> str:
    full_path = _join_paths(prefix, route_path)
    return full_path if full_path.startswith("/api/") else _join_paths("/api", full_path)


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
        if isinstance(value.func, ast.Name) and value.func.id == "APIRouter":
            return _literal_string(_keyword_value(value, "prefix"))
    return None


def _iter_points_routes() -> list[RouteInfo]:
    api_path = _path("app/api/points.py")
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
                    path=_api_path(prefix, route_path),
                    response_model=_node_source(source, _keyword_value(decorator, "response_model")),
                )
            )
    return routes


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


def _import_points_schema():
    return importlib.import_module("app.schemas.points")


class PointsSystemTests(unittest.TestCase):
    def test_points_schemas_validate_claim_and_redeem_payloads(self):
        schema = _import_points_schema()

        claim_model = getattr(schema, "PointsClaimRequest")
        for scene in CLAIM_SCENES:
            with self.subTest(scene=scene):
                claim = claim_model(scene=scene)
                self.assertEqual(claim.scene, scene)
        with self.assertRaises(ValidationError):
            claim_model(scene="unknown")

        activity = claim_model(scene="activity", source_id="activity-2026")
        self.assertEqual(activity.source_id, "activity-2026")

        redeem_model = getattr(schema, "PointsRedeemRequest")
        for redeem_type in REDEEM_TYPES:
            with self.subTest(redeem_type=redeem_type):
                redeem = redeem_model(redeem_type=redeem_type, amount=10)
                self.assertEqual(redeem.redeem_type, redeem_type)
        with self.assertRaises(ValidationError):
            redeem_model(redeem_type="cash", amount=10)
        with self.assertRaises(ValidationError):
            redeem_model(redeem_type="course", amount=0)

    def test_points_response_schemas_expose_expected_fields(self):
        schema = _import_points_schema()

        expected_fields = {
            "PointsBalanceResponse": {"balance"},
            "PointsHistoryResponse": {
                "id",
                "action_type",
                "amount",
                "balance_after",
                "description",
                "source_type",
                "source_id",
                "created_at",
            },
            "PointsClaimResponse": {"claimed", "scene", "amount", "balance", "history_id"},
            "PointsRedeemResponse": {"redeem_type", "amount", "balance", "history_id"},
        }
        for model_name, fields in expected_fields.items():
            with self.subTest(model=model_name):
                model = getattr(schema, model_name)
                self.assertTrue(fields <= _field_names(model))

    def test_points_api_declares_expected_routes_and_response_models(self):
        self.assertTrue(_path("app/api/points.py").exists(), "app/api/points.py should define points routes")
        routes = _iter_points_routes()
        self.assertTrue(routes, "app/api/points.py should define points API routes")

        actual = {(route.method, route.path): route for route in routes}
        missing_routes = [
            f"{method} {path}"
            for method, path in EXPECTED_POINTS_ENDPOINTS
            if (method, path) not in actual
        ]
        self.assertFalse(missing_routes, f"points API missing expected routes: {missing_routes}")

        missing_response_models = [
            f"{route.method} {route.path} ({route.function_name})"
            for route in routes
            if not route.response_model or route.response_model == "None"
        ]
        self.assertFalse(
            missing_response_models,
            f"points API routes must declare explicit response_model: {missing_response_models}",
        )

    def test_points_router_is_registered_in_api_aggregator(self):
        source = _read_text(_path("app/api/__init__.py"))

        self.assertIn("from app.api.points import router as points_router", source)
        self.assertIn("router.include_router(points_router)", source)

    def test_points_service_does_not_accept_http_request_or_response_objects(self):
        service_path = _path("app/services/points.py")
        self.assertTrue(service_path.exists(), "app/services/points.py should contain the points service layer")

        tree = ast.parse(_read_text(service_path), filename=str(service_path))
        violations: list[str] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.startswith(("fastapi", "starlette")):
                    imported_names = {alias.asname or alias.name for alias in node.names}
                    forbidden = imported_names & FORBIDDEN_HTTP_TYPES
                    if forbidden:
                        violations.append(
                            f"line {node.lineno}: imports HTTP transport types {sorted(forbidden)}"
                        )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = [
                    *node.args.posonlyargs,
                    *node.args.args,
                    *node.args.kwonlyargs,
                ]
                if node.args.vararg:
                    args.append(node.args.vararg)
                if node.args.kwarg:
                    args.append(node.args.kwarg)

                for arg in args:
                    annotation_names = _annotation_names(arg.annotation)
                    if annotation_names & FORBIDDEN_HTTP_TYPES:
                        violations.append(
                            f"line {arg.lineno}: {node.name} parameter {arg.arg} "
                            f"is annotated as HTTP transport type {sorted(annotation_names & FORBIDDEN_HTTP_TYPES)}"
                        )
                    if arg.arg.lower() in {"request", "response", "http_request", "http_response"}:
                        violations.append(
                            f"line {arg.lineno}: {node.name} parameter {arg.arg} leaks HTTP transport concerns"
                        )

        self.assertFalse(
            violations,
            "Points service functions should receive domain/user/db values, not FastAPI Request/Response: "
            f"{violations}",
        )

    def test_points_service_uses_row_lock_and_idempotent_source_for_claim_and_redeem(self):
        source = _read_text(_path("app/services/points.py"))

        self.assertIn("async def grant_points", source)
        self.assertIn("async def claim_points", source)
        self.assertIn("async def redeem_points", source)
        self.assertIn("with_for_update()", source)
        self.assertIn("ON CONFLICT (user_id) DO NOTHING", source)
        self.assertIn("source_type=\"points_claim\"", source)
        self.assertIn("existing is not None", source)
        self.assertIn("return existing, False", source)
        self.assertIn("if account.balance < data.amount:", source)
        self.assertIn('raise BusinessException("积分余额不足")', source)
        self.assertIn("amount=-data.amount", source)

    def test_points_model_and_migration_define_constraints(self):
        model_source = _read_text(_path("app/models/points.py"))
        migration_source = _read_text(
            _path("alembic/versions/a4b5c6d7e8f9_add_points_constraints_and_source_fields.py")
        )

        for source in (model_source, migration_source):
            with self.subTest(source=source[:20]):
                self.assertIn("ck_user_points_balance_non_negative", source)
                self.assertIn("ck_points_history_amount_non_zero", source)
                self.assertIn("ck_points_history_balance_after_non_negative", source)
                self.assertIn("source_type", source)
                self.assertIn("source_id", source)
                self.assertIn("uq_points_history_user_source_action", source)

    def test_points_interface_list_documents_claim_endpoint(self):
        docs = _read_text(_path("docs/接口列表.md"))

        for method, endpoint in EXPECTED_POINTS_ENDPOINTS:
            with self.subTest(endpoint=f"{method} {endpoint}"):
                self.assertIn(f"{method} {endpoint}", docs)


if __name__ == "__main__":
    unittest.main()
