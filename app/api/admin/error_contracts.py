"""Machine-readable OpenAPI error contracts for administrator endpoints.

The runtime exception handlers remain authoritative for response behaviour.
This module only keeps generated OpenAPI aligned with the frozen administrator
business-code contract, including cases where multiple codes share HTTP 422.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


ADMIN_ERROR_CODES: dict[str, dict[str, int | str]] = {
    "40001": {"status": 422, "description": "请求参数校验失败"},
    "40100": {"status": 401, "description": "管理员会话无效或已失效"},
    "40101": {"status": 403, "description": "管理员角色或再认证权限不足"},
    "40200": {"status": 422, "description": "当前业务或账号状态不允许该操作"},
    "40201": {"status": 409, "description": "请求版本、重复操作或当前状态冲突"},
    "40202": {"status": 429, "description": "请求过于频繁"},
    "40300": {"status": 404, "description": "目标资源不存在"},
    "50000": {"status": 500, "description": "服务器内部错误"},
}


def _response_example(code: str, description: str) -> dict[str, Any]:
    return {
        "summary": f"业务码 {code}",
        "value": {
            "code": int(code),
            "message": description,
            "data": None,
        },
    }


def admin_error_contract(*codes: str) -> dict[str, Any]:
    """Return FastAPI decorator kwargs for the selected administrator errors."""

    selected = tuple(dict.fromkeys(codes))
    unknown = [code for code in selected if code not in ADMIN_ERROR_CODES]
    if unknown:
        raise ValueError(f"unknown administrator error codes: {', '.join(unknown)}")

    by_status: dict[int, list[tuple[str, str]]] = {}
    for code in selected:
        metadata = ADMIN_ERROR_CODES[code]
        status = int(metadata["status"])
        description = str(metadata["description"])
        by_status.setdefault(status, []).append((code, description))

    responses: dict[int, dict[str, Any]] = {}
    for status, errors in by_status.items():
        responses[status] = {
            "description": "；".join(
                f"{code} {description}" for code, description in errors
            ),
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/APIErrorResponse"},
                    "examples": {
                        f"code_{code}": _response_example(code, description)
                        for code, description in errors
                    },
                }
            },
        }

    return {
        "responses": responses,
        "openapi_extra": {"x-error-codes": list(selected)},
    }


def error_statuses(codes: Iterable[str]) -> set[int]:
    """Expose the canonical status set for contract tests and tooling."""

    return {int(ADMIN_ERROR_CODES[code]["status"]) for code in codes}


__all__ = ["ADMIN_ERROR_CODES", "admin_error_contract", "error_statuses"]
