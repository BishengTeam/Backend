#!/usr/bin/env python3
"""Validate the frozen human-resources OpenAPI surface.

This is intentionally a contract check rather than an API smoke test.  It
imports the application, generates the same OpenAPI document served at
``/openapi.json``, and fails when a required path, security declaration or
material enum drifts.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterable
from pathlib import Path


os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("APP_DEBUG", "false")
os.environ.setdefault(
    "JWT_SECRET", "test-only-jwt-secret-that-is-at-least-32-characters"
)
os.environ.setdefault("PII_HASH_KEY", "test-only-pii-hash-key-at-least-32-characters")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402


EXPECTED_PATHS: dict[str, set[str]] = {
    "/api/renshe/applications": {"get"},
    "/api/renshe/applications/draft": {"post"},
    "/api/renshe/applications/{application_id}": {"get"},
    "/api/renshe/applications/{application_id}/cancel-payment": {"post"},
    "/api/renshe/applications/{application_id}/refunds": {"post"},
    "/api/renshe/applications/{application_id}/submit": {"post"},
    "/api/renshe/refunds/{refund_id}": {"get"},
    "/api/renshe/verification-materials/{kind}": {"post"},
    "/api/renshe/verification-materials/{kind}/signed-url": {"get"},
    "/api/payment/prepay": {"post"},
    "/api/payment/orders/{order_id}/sync": {"post"},
    "/api/payment/callback": {"post"},
    "/api/payment/refund-callback": {"post"},
    "/admin/renshe/applications": {"get"},
    "/admin/renshe/applications/{application_id}": {"get"},
    "/admin/renshe/applications/{application_id}/external-review": {"post"},
    "/admin/renshe/applications/{application_id}/initial-review": {"post"},
    "/admin/renshe/cleanup-runs/{run_id}/retry": {"post"},
    "/admin/renshe/export-volumes/{volume_id}/signed-url": {"get"},
    "/admin/renshe/exports/{job_id}": {"get"},
    "/admin/renshe/exports/{job_id}/retry": {"post"},
    "/admin/renshe/materials/{material_id}/signed-url": {"get"},
    "/admin/renshe/plans/{plan_id}/cleanup-runs": {"get"},
    "/admin/renshe/plans/{plan_id}/exports": {"get", "post"},
    "/admin/renshe/refunds": {"get"},
    "/admin/renshe/refunds/{refund_id}/decision": {"post"},
    "/admin/renshe/audit-logs": {"get"},
    "/admin/renshe/reviews/{review_id}/corrections": {"post"},
    "/admin/renshe/users/{user_id}/verification-materials/{kind}/signed-url": {
        "get"
    },
    "/admin/certifications/{code}/plans/{plan_id}/impact": {"get"},
}

ADDITIONAL_RENSHE_PATHS = {
    "/admin/certifications/{code}/plans/{plan_id}/impact",
    "/api/payment/prepay",
    "/api/payment/orders/{order_id}/sync",
    "/api/payment/callback",
    "/api/payment/refund-callback",
}
PUBLIC_CALLBACK_PATHS = {
    "/api/payment/callback",
    "/api/payment/refund-callback",
}

EXPECTED_MATERIAL_KINDS = [
    "id_card_front",
    "id_card_back",
    "portrait",
    "student_card",
    "xuexin_registration",
    "education_proof",
]


def _operations(paths: dict) -> Iterable[tuple[str, str, dict]]:
    for path, item in paths.items():
        for method, operation in item.items():
            if method in {"get", "post", "put", "patch", "delete"}:
                yield path, method, operation


def build_report() -> tuple[dict, list[str]]:
    schema = app.openapi()
    paths = schema.get("paths", {})
    errors: list[str] = []

    for path, methods in EXPECTED_PATHS.items():
        operation_item = paths.get(path)
        if operation_item is None:
            errors.append(f"missing path: {path}")
            continue
        for method in methods:
            operation = operation_item.get(method)
            if operation is None:
                errors.append(f"missing operation: {method.upper()} {path}")
                continue
            if path not in PUBLIC_CALLBACK_PATHS and not operation.get("security"):
                errors.append(f"missing Bearer security: {method.upper()} {path}")
            if path in PUBLIC_CALLBACK_PATHS and operation.get("security"):
                errors.append(f"callback must not require Bearer security: {method.upper()} {path}")
            if "200" not in operation.get("responses", {}):
                errors.append(f"missing success response: {method.upper()} {path}")
            if operation.get("x-contract-version") != "2026-08-10":
                errors.append(f"missing contract version: {method.upper()} {path}")
            if path in PUBLIC_CALLBACK_PATHS:
                if operation.get("x-wechat-pay-api-version") != "v3":
                    errors.append(f"callback is not marked V3: {method.upper()} {path}")
                if operation.get("x-error-codes"):
                    errors.append(f"callback must use V3 ACK errors: {method.upper()} {path}")
                continue
            if operation.get("x-error-codes") != [
                "40001",
                "40100",
                "40101",
                "40200",
                "40201",
                "40300",
                "40400",
                "50000",
            ]:
                errors.append(f"error code drift: {method.upper()} {path}")

    human_operations = [
        (path, method, operation)
        for path, method, operation in _operations(paths)
        if (
            "/api/renshe" in path
            or "/admin/renshe" in path
            or path in ADDITIONAL_RENSHE_PATHS
        )
    ]
    unexpected = {
        (path, method)
        for path, method, _operation in human_operations
        if method not in EXPECTED_PATHS.get(path, set())
    }
    if unexpected:
        errors.append(
            "unexpected human-resources operations: "
            + ", ".join(f"{method.upper()} {path}" for path, method in sorted(unexpected))
        )

    enterprise_paths = sorted(path for path in paths if "/enterprise" in path)
    if enterprise_paths:
        errors.append("enterprise APIs must remain disabled: " + ", ".join(enterprise_paths))

    schemas = schema.get("components", {}).get("schemas", {})
    error_schema = schemas.get("APIErrorResponse", {})
    detail_schema = error_schema.get("properties", {}).get("detail", {})
    if detail_schema.get("type") != ["array", "object", "null"]:
        errors.append("APIErrorResponse.detail must allow array/object/null")
    material_schema = schemas.get("RensheVerificationMaterialResponse", {})
    material_kinds = (
        material_schema.get("properties", {}).get("kind", {}).get("enum", [])
    )
    if material_kinds != EXPECTED_MATERIAL_KINDS:
        errors.append(
            "material enum drift: "
            f"expected {EXPECTED_MATERIAL_KINDS}, got {material_kinds}"
        )

    report = {
        "openapi_paths": len(paths),
        "renshe_operations": len(human_operations),
        "expected_renshe_operations": sum(len(methods) for methods in EXPECTED_PATHS.values()),
        "enterprise_paths": len(enterprise_paths),
        "material_kinds": material_kinds,
        "errors": errors,
    }
    return report, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="输出 JSON 报告")
    args = parser.parse_args()

    report, errors = build_report()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            "renshe_contract "
            f"paths={report['renshe_operations']}/{report['expected_renshe_operations']} "
            f"enterprise={report['enterprise_paths']} "
            f"materials={len(report['material_kinds'])}"
        )
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
