import ast
import unittest
from dataclasses import dataclass
from pathlib import Path

import jwt

from app.core.security import create_access_token, create_refresh_token, decode_access_token
from app.core.exceptions import (
    AppException,
    BusinessException,
    ConflictException,
    ForbiddenException,
    NotFoundException,
    ThirdPartyException,
    UnauthorizedException,
    ValidationException,
)
from app.schemas.user import (
    LoginRequest,
    LoginResponse,
    PhoneDecryptRequest,
    RefreshRequest,
    RefreshResponse,
    UserIdentityCreate,
    UserProfile,
)
from pydantic import ValidationError as PydanticValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]

ROUTE_METHODS = {"api_route", "delete", "get", "head", "options", "patch", "post", "put"}
FORBIDDEN_HTTP_TYPES = {
    "Request", "Response", "JSONResponse", "HTMLResponse",
    "PlainTextResponse", "RedirectResponse", "StreamingResponse",
}

AUTH_EXPECTED_ROUTES = (
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/refresh"),
)

USER_EXPECTED_ROUTES = (
    ("DELETE", "/api/user/account"),
    ("POST", "/api/user/phone/decrypt"),
    ("POST", "/api/user/identity"),
    ("GET", "/api/user/identity"),
)


@dataclass(frozen=True)
class RouteInfo:
    function_name: str
    method: str
    path: str
    has_response_model: bool


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
        if not any(isinstance(t, ast.Name) and t.id == "router" for t in targets):
            continue
        func = value.func
        if isinstance(func, ast.Name) and func.id == "APIRouter":
            return _literal_string(getattr(value, 'keywords', None) and next(
                (kw.value for kw in value.keywords if kw.arg == "prefix"), None) if hasattr(value, 'keywords') else None)
    return None


def _iter_routes(file_path_relative: str, router_var: str = "router") -> list[RouteInfo]:
    file_path = REPO_ROOT / file_path_relative
    source = _read_text(file_path)
    tree = ast.parse(source, filename=str(file_path))
    prefix = None
    for node in ast.walk(tree):
        targets: list[ast.AST] = []
        value = None
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
        func = value.func
        if isinstance(func, ast.Name) and func.id == "APIRouter":
            prefix = _literal_string(getattr(value, 'keywords', None) and next(
                (kw.value for kw in value.keywords if kw.arg == "prefix"), None) if hasattr(value, 'keywords') else None)

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
                and func.value.id == router_var
            ):
                continue
            route_path = _literal_string(decorator.args[0]) if decorator.args else ""
            full_path = (prefix or "") + (route_path or "")
            full_path = "/api" + full_path if full_path.startswith("/") else "/api/" + full_path
            has_rm = any(kw.arg == "response_model" for kw in decorator.keywords)
            routes.append(RouteInfo(
                function_name=node.name,
                method=func.attr.upper(),
                path=full_path,
                has_response_model=has_rm,
            ))
    return routes


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


def _service_violates_layering(relative_path: str) -> list[str]:
    tree = _load_ast(relative_path)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith(("fastapi", "starlette")):
                imported_names = {alias.asname or alias.name for alias in node.names}
                forbidden = imported_names & FORBIDDEN_HTTP_TYPES
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


class AuthSystemTests(unittest.TestCase):

    # --- Auth API routes ---

    def test_auth_api_declares_expected_routes(self):
        routes = _iter_routes("app/api/auth.py")
        self.assertTrue(routes, "app/api/auth.py should define API routes")
        actual = {(r.method, r.path) for r in routes}
        for method, path in AUTH_EXPECTED_ROUTES:
            with self.subTest(endpoint=f"{method} {path}"):
                self.assertIn((method, path), actual, f"auth API missing route {method} {path}")

    def test_auth_api_routes_declare_explicit_response_model(self):
        routes = _iter_routes("app/api/auth.py")
        self.assertTrue(routes)
        missing = [f"{r.method} {r.path} ({r.function_name})" for r in routes if not r.has_response_model]
        self.assertFalse(missing, f"auth API routes missing explicit response_model: {missing}")

    # --- User API routes ---

    def test_user_api_declares_expected_routes(self):
        routes = _iter_routes("app/api/user.py")
        self.assertTrue(routes, "app/api/user.py should define API routes")
        actual = {(r.method, r.path) for r in routes}
        for method, path in USER_EXPECTED_ROUTES:
            with self.subTest(endpoint=f"{method} {path}"):
                self.assertIn((method, path), actual, f"user API missing route {method} {path}")

    def test_user_api_routes_declare_explicit_response_model(self):
        routes = _iter_routes("app/api/user.py")
        self.assertTrue(routes)
        missing = [f"{r.method} {r.path} ({r.function_name})" for r in routes if not r.has_response_model]
        self.assertFalse(missing, f"user API routes missing explicit response_model: {missing}")

    # --- Service layering ---

    def test_auth_service_does_not_accept_http_request_or_response_objects(self):
        violations = _service_violates_layering("app/services/auth.py")
        self.assertFalse(violations, f"AuthService should not use HTTP transport types: {violations}")

    def test_user_service_does_not_accept_http_request_or_response_objects(self):
        violations = _service_violates_layering("app/services/user.py")
        self.assertFalse(violations, f"UserService should not use HTTP transport types: {violations}")

    # --- Schema validation ---

    def test_login_request_requires_code(self):
        r = LoginRequest(code="test_code")
        self.assertEqual(r.code, "test_code")
        with self.assertRaises(PydanticValidationError):
            LoginRequest(code="")
        with self.assertRaises(PydanticValidationError):
            LoginRequest()

    def test_refresh_request_requires_token(self):
        r = RefreshRequest(refresh_token="token123")
        self.assertEqual(r.refresh_token, "token123")
        with self.assertRaises(PydanticValidationError):
            RefreshRequest()

    def test_login_response_contains_jwt_and_user_profile(self):
        r = LoginResponse(
            access_token="at", refresh_token="rt", expires_in=7200,
            user=UserProfile(id=1, openid="wx_openid", created_at="2026-01-01T00:00:00Z"),
        )
        self.assertEqual(r.access_token, "at")
        self.assertEqual(r.refresh_token, "rt")
        self.assertEqual(r.expires_in, 7200)
        self.assertEqual(r.user.id, 1)

    def test_refresh_response_contains_new_token_pair(self):
        r = RefreshResponse(access_token="new_at", refresh_token="new_rt", expires_in=7200)
        self.assertEqual(r.access_token, "new_at")
        self.assertEqual(r.refresh_token, "new_rt")

    def test_phone_decrypt_request_requires_encrypted_data_and_iv(self):
        r = PhoneDecryptRequest(encrypted_data="enc", iv="iv_val")
        self.assertEqual(r.encrypted_data, "enc")
        self.assertEqual(r.iv, "iv_val")
        with self.assertRaises(PydanticValidationError):
            PhoneDecryptRequest()
        with self.assertRaises(PydanticValidationError):
            PhoneDecryptRequest(encrypted_data="enc")

    def test_user_identity_create_enforces_fields(self):
        r = UserIdentityCreate(
            user_type="student", real_name="张三", id_card_number="11010519491231002X",
            student_card_oss="oss_key",
        )
        self.assertEqual(r.user_type, "student")
        self.assertEqual(r.id_card_number, "11010519491231002X")
        with self.assertRaises(PydanticValidationError):
            UserIdentityCreate(user_type="student")
        with self.assertRaises(PydanticValidationError):
            UserIdentityCreate(user_type="invalid_type", real_name="张三", id_card_number="11010519491231002X")
        with self.assertRaises(PydanticValidationError):
            UserIdentityCreate(user_type="student", real_name="张三", id_card_number="123")  # too short

    def test_user_identity_create_allows_enterprise_type(self):
        r = UserIdentityCreate(
            user_type="enterprise", real_name="李四", id_card_number="11010519491231002X",
        )
        self.assertEqual(r.user_type, "enterprise")
        self.assertIsNone(r.student_card_oss)

    # --- JWT / Security ---

    def test_create_access_token_returns_valid_jwt(self):
        token = create_access_token(user_id=1, openid="wx_test")
        self.assertIsInstance(token, str)
        self.assertTrue(len(token) > 0)

    def test_create_refresh_token_returns_random_string(self):
        token = create_refresh_token()
        self.assertIsInstance(token, str)
        self.assertEqual(len(token), 64)  # 48 bytes URL-safe base64

    def test_decode_access_token_returns_payload(self):
        token = create_access_token(user_id=42, openid="openid_42")
        payload = decode_access_token(token)
        self.assertEqual(payload["type"], "access")
        self.assertEqual(payload["user_id"], 42)
        self.assertEqual(payload["openid"], "openid_42")
        self.assertIn("exp", payload)
        self.assertIn("iat", payload)

    def test_decode_invalid_token_raises_jwt_error(self):
        with self.assertRaises(jwt.DecodeError):
            decode_access_token("not.a.valid.token")
        with self.assertRaises(jwt.DecodeError):
            decode_access_token("")

    def test_refresh_tokens_are_unique(self):
        tokens = {create_refresh_token() for _ in range(10)}
        self.assertEqual(len(tokens), 10)

    # --- Exception error codes ---

    def test_unauthorized_exception_has_code_40100(self):
        exc = UnauthorizedException()
        self.assertEqual(exc.code, 40100)
        self.assertEqual(exc.http_status_code, 401)
        exc2 = UnauthorizedException("自定义消息")
        self.assertEqual(exc2.code, 40100)

    def test_forbidden_exception_has_code_40101(self):
        exc = ForbiddenException()
        self.assertEqual(exc.code, 40101)
        self.assertEqual(exc.http_status_code, 403)

    def test_not_found_exception_has_code_40300(self):
        exc = NotFoundException("用户")
        self.assertEqual(exc.code, 40300)
        self.assertEqual(exc.http_status_code, 404)
        self.assertIn("用户", exc.message)

    def test_business_exception_has_code_40200(self):
        exc = BusinessException("积分不足")
        self.assertEqual(exc.code, 40200)
        self.assertEqual(exc.http_status_code, 422)

    def test_conflict_exception_has_code_40201(self):
        exc = ConflictException("重复报名")
        self.assertEqual(exc.code, 40201)
        self.assertEqual(exc.http_status_code, 409)

    def test_third_party_exception_has_code_40400(self):
        exc = ThirdPartyException("微信接口异常")
        self.assertEqual(exc.code, 40400)
        self.assertEqual(exc.http_status_code, 502)

    def test_validation_exception_has_code_40001(self):
        exc = ValidationException()
        self.assertEqual(exc.code, 40001)
        self.assertEqual(exc.http_status_code, 422)
        self.assertEqual(exc.detail, [])
        exc2 = ValidationException("字段错误", detail=[{"field": "phone", "reason": "格式不正确"}])
        self.assertEqual(exc2.code, 40001)
        self.assertEqual(len(exc2.detail), 1)

    def test_app_exception_is_subclass_of_exception(self):
        for cls in [
            AppException, UnauthorizedException, ForbiddenException,
            NotFoundException, BusinessException, ConflictException,
            ThirdPartyException, ValidationException,
        ]:
            with self.subTest(cls=cls.__name__):
                self.assertTrue(issubclass(cls, Exception))


if __name__ == "__main__":
    unittest.main()
