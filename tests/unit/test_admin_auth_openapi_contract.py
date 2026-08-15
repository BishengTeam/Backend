"""Generated OpenAPI proof for the frozen administrator auth errors."""

from __future__ import annotations

from app.api.admin.error_contracts import ADMIN_ERROR_CODES, error_statuses
from app.main import app


AUTH_ERROR_CODES = {
    ("get", "/admin/auth/me"): ("40001", "40100", "40202", "50000"),
    ("post", "/admin/auth/login"): ("40001", "40100", "40202", "50000"),
    ("post", "/admin/auth/change-password"): (
        "40001",
        "40100",
        "40200",
        "40202",
        "50000",
    ),
    ("post", "/admin/auth/reauth"): (
        "40001",
        "40100",
        "40101",
        "40200",
        "40202",
        "50000",
    ),
    ("post", "/admin/auth/logout"): ("40001", "40100", "40200", "50000"),
}


def test_admin_error_catalog_covers_the_frozen_business_codes() -> None:
    assert ADMIN_ERROR_CODES == {
        "40001": {"status": 422, "description": "请求参数校验失败"},
        "40100": {"status": 401, "description": "管理员会话无效或已失效"},
        "40101": {"status": 403, "description": "管理员角色或再认证权限不足"},
        "40200": {"status": 422, "description": "当前业务或账号状态不允许该操作"},
        "40201": {"status": 409, "description": "请求版本、重复操作或当前状态冲突"},
        "40202": {"status": 429, "description": "请求过于频繁"},
        "40300": {"status": 404, "description": "目标资源不存在"},
        "50000": {"status": 500, "description": "服务器内部错误"},
    }


def test_admin_auth_openapi_declares_codes_statuses_and_examples() -> None:
    app.openapi_schema = None
    schema = app.openapi()

    for (method, path), expected_codes in AUTH_ERROR_CODES.items():
        operation = schema["paths"][path][method]
        assert operation["x-error-codes"] == list(expected_codes)
        assert set(operation["responses"]) == {
            "200",
            *(str(status) for status in error_statuses(expected_codes)),
        }

        for code in expected_codes:
            status = str(ADMIN_ERROR_CODES[code]["status"])
            media = operation["responses"][status]["content"]["application/json"]
            assert media["schema"] == {
                "$ref": "#/components/schemas/APIErrorResponse"
            }
            assert media["examples"][f"code_{code}"]["value"]["code"] == int(code)


def test_admin_auth_openapi_keeps_bearer_boundary_explicit() -> None:
    app.openapi_schema = None
    paths = app.openapi()["paths"]

    assert paths["/admin/auth/login"]["post"].get("security") is None
    for method, path in AUTH_ERROR_CODES:
        if path == "/admin/auth/login":
            continue
        assert paths[path][method]["security"] == [{"BearerAuth": []}]
