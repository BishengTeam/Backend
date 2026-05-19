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

ALLOWED_QUESTION_TYPES = ("single_choice", "multiple_choice", "judge")

EXPECTED_QUIZ_ENDPOINTS = (
    ("GET", "/api/quiz/categories"),
    ("GET", "/api/quiz/questions"),
    ("POST", "/api/quiz/submit"),
    ("GET", "/api/quiz/wrong-book"),
    ("POST", "/api/quiz/wrong-book"),
    ("DELETE", "/api/quiz/wrong-book/{id}"),
    ("GET", "/api/quiz/collections"),
    ("POST", "/api/quiz/collections"),
    ("DELETE", "/api/quiz/collections/{id}"),
    ("GET", "/api/quiz/checkin"),
    ("POST", "/api/quiz/checkin"),
)

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

        func = value.func
        if isinstance(func, ast.Name) and func.id == "APIRouter":
            return _literal_string(_keyword_value(value, "prefix"))
    return None


def _iter_quiz_routes() -> list[RouteInfo]:
    api_path = _path("app/api/quiz.py")
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


def _pydantic_models(module: object) -> list[type[BaseModel]]:
    models: list[type[BaseModel]] = []
    for _, value in inspect.getmembers(module, inspect.isclass):
        if issubclass(value, BaseModel) and value is not BaseModel:
            models.append(value)
    return models


def _import_quiz_schema():
    return importlib.import_module("app.schemas.quiz")


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


def _migration_call_segments() -> list[tuple[Path, str]]:
    segments: list[tuple[Path, str]] = []
    for migration in sorted(_path("alembic/versions").glob("*.py")):
        source = _read_text(migration)
        tree = ast.parse(source, filename=str(migration))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                segment = ast.get_source_segment(source, node) or ast.unparse(node)
                normalized = re.sub(r"[\s\"'`]+", "", segment.lower())
                segments.append((migration, normalized))
    return segments


def _has_unique_index_or_constraint(
    segments: list[tuple[Path, str]], table_name: str, column_names: tuple[str, ...]
) -> bool:
    for _, segment in segments:
        if table_name not in segment or not all(column_name in segment for column_name in column_names):
            continue
        if (
            "unique=true" in segment
            or "uniqueconstraint(" in segment
            or "create_unique_constraint(" in segment
            or "create_unique_index(" in segment
        ):
            return True
    return False


def _has_question_type_check_constraint(segments: list[tuple[Path, str]]) -> bool:
    for _, segment in segments:
        if "quiz_question" not in segment or "question_type" not in segment:
            continue
        if "check" not in segment:
            continue
        if all(question_type in segment for question_type in ALLOWED_QUESTION_TYPES):
            return True
    return False


class QuizSystemTests(unittest.TestCase):
    def test_quiz_question_type_schema_accepts_only_supported_values(self):
        self.assertTrue(
            _path("app/schemas/quiz.py").exists(),
            "app/schemas/quiz.py should contain quiz Pydantic schemas",
        )
        schema = _import_quiz_schema()
        preferred = getattr(schema, "QuizQuestionQuery", None)
        candidates = [preferred] if preferred else []
        candidates.extend(
            model
            for model in _pydantic_models(schema)
            if model not in candidates and "question_type" in _field_names(model)
        )

        selected_model: type[BaseModel] | None = None
        validation_errors: list[str] = []
        for model in candidates:
            try:
                model(question_type=ALLOWED_QUESTION_TYPES[0])
            except ValidationError as exc:
                validation_errors.append(f"{model.__name__}: {exc}")
                continue
            selected_model = model
            break

        self.assertIsNotNone(
            selected_model,
            "app.schemas.quiz should expose a Pydantic query schema whose question_type "
            f"accepts {ALLOWED_QUESTION_TYPES}; errors: {validation_errors}",
        )
        assert selected_model is not None

        for question_type in ALLOWED_QUESTION_TYPES:
            with self.subTest(question_type=question_type):
                model = selected_model(question_type=question_type)
                self.assertEqual(getattr(model, "question_type"), question_type)

        with self.assertRaises(ValidationError):
            selected_model(question_type="essay")

    def test_quiz_question_list_response_schema_omits_correct_answer(self):
        self.assertTrue(
            _path("app/schemas/quiz.py").exists(),
            "app/schemas/quiz.py should contain quiz Pydantic schemas",
        )
        schema = _import_quiz_schema()
        response_model = getattr(schema, "QuizQuestionResponse", None)
        if response_model is None:
            for model in _pydantic_models(schema):
                fields = _field_names(model)
                if {"id", "question_type", "question_text", "options"} <= fields:
                    response_model = model
                    break

        self.assertIsNotNone(
            response_model,
            "app.schemas.quiz should expose QuizQuestionResponse or an equivalent public question schema",
        )
        self.assertNotIn(
            "correct_answer",
            _field_names(response_model),
            "question list response schema must not expose correct_answer",
        )

    def test_quiz_answer_normalization_supports_core_question_types(self):
        source = _read_text(_path("app/utils/quiz_helpers.py"))

        self.assertIn('if question_type == "multiple_choice"', source)
        self.assertIn('replace("，", ",")', source)
        self.assertIn('replace("；", ",")', source)
        self.assertIn('JUDGE_TRUE_VALUES', source)
        self.assertIn('"正确"', source)
        self.assertIn('return "TRUE"', source)
        self.assertIn('return "FALSE"', source)

    def test_quiz_record_payload_omits_answer_by_default_and_can_include_it_for_submit(self):
        source = _read_text(_path("app/utils/quiz_helpers.py"))

        self.assertIn("include_correct_answer: bool = False", source)
        self.assertIn('"question": question_payload(question, include_correct_answer=False)', source)
        self.assertIn('if include_correct_answer:', source)
        self.assertIn('payload["correct_answer"] = question.correct_answer', source)

    def test_quiz_api_declares_expected_routes_and_response_models(self):
        self.assertTrue(_path("app/api/quiz.py").exists(), "app/api/quiz.py should define quiz routes")
        routes = _iter_quiz_routes()
        self.assertTrue(routes, "app/api/quiz.py should define quiz API routes")

        actual = {(route.method, route.path): route for route in routes}
        missing_routes = [
            f"{method} {path}"
            for method, path in EXPECTED_QUIZ_ENDPOINTS
            if (method, path) not in actual
        ]
        self.assertFalse(missing_routes, f"quiz API missing expected routes: {missing_routes}")

        missing_response_models = [
            f"{route.method} {route.path} ({route.function_name})"
            for route in routes
            if not route.response_model or route.response_model == "None"
        ]
        self.assertFalse(
            missing_response_models,
            f"quiz API routes must declare explicit response_model: {missing_response_models}",
        )

    def test_quiz_service_does_not_accept_http_request_or_response_objects(self):
        service_path = _path("app/services/quiz.py")
        self.assertTrue(service_path.exists(), "app/services/quiz.py should contain the quiz service layer")

        tree = _load_ast("app/services/quiz.py")
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
            "Quiz service functions should receive domain/user/db values, not FastAPI Request/Response: "
            f"{violations}",
        )

    def test_quiz_migrations_define_unique_indexes_and_question_type_check(self):
        segments = _migration_call_segments()
        self.assertTrue(segments, "alembic/versions should contain migration files")

        self.assertTrue(
            _has_unique_index_or_constraint(segments, "quiz_record", ("user_id", "question_id")),
            "migration should define a unique index/constraint on quiz_record(user_id, question_id)",
        )
        self.assertTrue(
            _has_unique_index_or_constraint(segments, "quiz_checkin", ("user_id", "checkin_date")),
            "migration should define a unique index/constraint on quiz_checkin(user_id, checkin_date)",
        )
        self.assertTrue(
            _has_question_type_check_constraint(segments),
            "migration should define a quiz_question.question_type check constraint for "
            f"{ALLOWED_QUESTION_TYPES}",
        )

    def test_quiz_interface_plan_documents_target_endpoint_list(self):
        docs = [
            path
            for base in (_path("docs/plan"), _path("docs"))
            if base.exists()
            for path in base.glob("*.md")
            if path.is_file() and "GET /api/quiz/categories" in _read_text(path)
        ]
        self.assertTrue(docs, "quiz endpoints should be documented in the plan or interface list")

        combined_text = "\n".join(_read_text(path) for path in docs)
        for method, endpoint in EXPECTED_QUIZ_ENDPOINTS:
            with self.subTest(endpoint=f"{method} {endpoint}"):
                self.assertIn(f"{method} {endpoint}", combined_text)

        plan_docs = [path for path in docs if "docs\\plan" in str(path) or "docs/plan" in str(path)]
        if plan_docs:
            plan_text = "\n".join(_read_text(path) for path in plan_docs)
            for method, endpoint in EXPECTED_QUIZ_ENDPOINTS:
                expected_cell = rf"\|\s*`?{re.escape(method + ' ' + endpoint)}`?\s*\|"
                with self.subTest(plan_row=f"{method} {endpoint}"):
                    self.assertRegex(
                        plan_text,
                        expected_cell,
                        "quiz plan should keep the target endpoint list in a markdown table",
                    )


if __name__ == "__main__":
    unittest.main()
