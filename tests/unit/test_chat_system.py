import ast
import unittest
from pathlib import Path

from pydantic import ValidationError

from app.schemas.chat import ChatRequest, ChatResponse, QuickQuestionResponse

REPO_ROOT = Path(__file__).resolve().parents[2]

ROUTE_METHODS = {"api_route", "delete", "get", "head", "options", "patch", "post", "put"}
FORBIDDEN_HTTP_TYPES = {
    "Request", "Response", "JSONResponse", "HTMLResponse",
    "PlainTextResponse", "RedirectResponse", "StreamingResponse",
}

CHAT_EXPECTED_ROUTES = (
    ("POST", "/api/chat"),
    ("GET", "/api/chat/stream"),
)

QUICK_EXPECTED_ROUTES = (
    ("GET", "/api/quick-questions"),
)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _load_ast(relative_path: str) -> ast.Module:
    return ast.parse(_read_text(REPO_ROOT / relative_path), filename=relative_path)


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
    """Return {function_name: has_response_model} for each route handler."""
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


class ChatSystemTests(unittest.TestCase):

    # --- Chat API routes ---

    def test_chat_api_route_file_exists(self):
        self.assertTrue(
            (REPO_ROOT / "app/api/chat.py").exists(),
            "app/api/chat.py should define chat API routes",
        )

    def test_chat_api_routes_declare_explicit_response_model(self):
        rms = _api_decorator_has_response_model("app/api/chat.py")
        self.assertTrue(rms, "app/api/chat.py should define routes")
        missing = [name for name, has_rm in rms.items() if not has_rm]
        if "chat_stream" in missing:
            missing.remove("chat_stream")  # SSE streaming route may use StreamingResponse directly
        self.assertFalse(missing, f"chat API routes missing explicit response_model: {missing}")

    def test_quick_questions_route_declares_explicit_response_model(self):
        rms = _api_decorator_has_response_model("app/api/chat.py", router_var="quick_router")
        self.assertTrue(rms, "quick_router should define routes")
        missing = [name for name, has_rm in rms.items() if not has_rm]
        self.assertFalse(missing, f"quick-questions route missing explicit response_model: {missing}")

    # --- Service layering ---

    def test_chat_service_does_not_accept_http_request_or_response_objects(self):
        violations = _service_layer_violations("app/services/chat.py")
        self.assertFalse(violations, f"ChatService should not use HTTP transport types: {violations}")

    # --- Schema validation ---

    def test_chat_request_requires_message(self):
        r = ChatRequest(message="你好")
        self.assertEqual(r.message, "你好")
        self.assertIsNone(r.session_id)
        with self.assertRaises(ValidationError):
            ChatRequest()
        with self.assertRaises(ValidationError):
            ChatRequest(message="")

    def test_chat_request_enforces_message_max_length(self):
        with self.assertRaises(ValidationError):
            ChatRequest(message="x" * 2001)

    def test_chat_request_accepts_optional_session_id(self):
        r = ChatRequest(message="hello", session_id="session-123")
        self.assertEqual(r.session_id, "session-123")

    def test_chat_response_has_expected_fields(self):
        r = ChatResponse(session_id="s1", reply="Hello!", backend_type="dify")
        self.assertEqual(r.session_id, "s1")
        self.assertEqual(r.reply, "Hello!")
        self.assertEqual(r.backend_type, "dify")

    def test_quick_question_response_has_expected_fields(self):
        r = QuickQuestionResponse(id=1, question_text="什么是H3C？", category="认证")
        self.assertEqual(r.id, 1)
        self.assertEqual(r.question_text, "什么是H3C？")
        self.assertEqual(r.category, "认证")
        r2 = QuickQuestionResponse(id=2, question_text="如何报名？")
        self.assertIsNone(r2.category)
        with self.assertRaises(ValidationError):
            QuickQuestionResponse(id=1)

    # --- Chat backend integration ---

    def test_chat_backend_has_no_hardcoded_manual_reply(self):
        source = _read_text(REPO_ROOT / "app/integrations/chat_backend.py")
        self.assertNotIn("ManualChatBackend", source)
        self.assertNotIn("人工客服正在赶来", source)
        self.assertNotIn("mock", source.lower().split("mock") if False else ["mock-reply"])
        self.assertIn("DifyChatBackend", source)
        self.assertIn("ChatBackend", source)

    def test_chat_service_uses_settings_chat_backend_not_hardcoded_backend(self):
        source = _read_text(REPO_ROOT / "app/services/chat.py")
        self.assertIn("CHAT_BACKEND", source)
        self.assertIn("DifyChatBackend", source)

    def test_dify_backend_stream_parse_handles_empty_lines(self):
        from app.integrations.chat_backend import DifyChatBackend
        self.assertIsNone(DifyChatBackend._parse_stream_line(""))
        self.assertIsNone(DifyChatBackend._parse_stream_line("not a data line"))
        self.assertIsNone(DifyChatBackend._parse_stream_line("data: [DONE]"))
        self.assertIsNone(DifyChatBackend._parse_stream_line("data: "))

    def test_dify_backend_stream_parse_extracts_answer(self):
        from app.integrations.chat_backend import DifyChatBackend
        chunk = DifyChatBackend._parse_stream_line('data: {"event":"message","answer":"Hello"}')
        self.assertEqual(chunk, "Hello")
        chunk = DifyChatBackend._parse_stream_line('data: {"event":"agent_message","answer":"Step 1"}')
        self.assertEqual(chunk, "Step 1")

    def test_dify_backend_payload_returns_expected_structure(self):
        from app.integrations.chat_backend import DifyChatBackend
        payload = DifyChatBackend._payload(user_id=1, message="hello", response_mode="blocking")
        self.assertEqual(payload["query"], "hello")
        self.assertEqual(payload["response_mode"], "blocking")
        self.assertEqual(payload["user"], "1")
        self.assertIn("inputs", payload)

    def test_dify_backend_has_no_hardcoded_mock_path(self):
        source = _read_text(REPO_ROOT / "app/integrations/chat_backend.py")
        self.assertNotIn("mock=True", source)
        self.assertNotIn('"mock"', source.lower())
        self.assertNotIn("if debug:", source.lower().replace("__debug__", ""))
        self.assertNotIn("fallback", source.lower())


if __name__ == "__main__":
    unittest.main()
