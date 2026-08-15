"""Generated OpenAPI proof for administrator account-management errors."""

from __future__ import annotations

from app.api.admin.error_contracts import ADMIN_ERROR_CODES, error_statuses
from app.main import app


SETTINGS_ERROR_CODES = {
    ("get", "/admin/settings/admins"): (
        "40001",
        "40100",
        "40101",
        "50000",
    ),
    ("post", "/admin/settings/admins"): (
        "40001",
        "40100",
        "40101",
        "40200",
        "40201",
        "50000",
    ),
    ("patch", "/admin/settings/admins/{admin_id}"): (
        "40001",
        "40100",
        "40101",
        "40200",
        "40300",
        "50000",
    ),
    ("post", "/admin/settings/admins/{admin_id}/disable"): (
        "40001",
        "40100",
        "40101",
        "40200",
        "40300",
        "50000",
    ),
    ("post", "/admin/settings/admins/{admin_id}/enable"): (
        "40001",
        "40100",
        "40101",
        "40200",
        "40201",
        "40300",
        "50000",
    ),
    ("post", "/admin/settings/admins/{admin_id}/password-reset"): (
        "40001",
        "40100",
        "40101",
        "40200",
        "40201",
        "40300",
        "50000",
    ),
    ("post", "/admin/settings/admins/{admin_id}/unlock"): (
        "40001",
        "40100",
        "40101",
        "40200",
        "40300",
        "50000",
    ),
    ("get", "/admin/settings/security-audit"): (
        "40001",
        "40100",
        "40101",
        "40200",
        "50000",
    ),
    ("put", "/admin/settings/admins/{admin_id}"): (
        "40001",
        "40100",
        "40101",
        "40200",
        "40201",
        "40300",
        "50000",
    ),
    ("put", "/admin/settings/admins/{admin_id}/password"): (
        "40001",
        "40100",
        "40101",
        "40200",
        "40201",
        "40300",
        "50000",
    ),
}


def test_admin_settings_openapi_declares_codes_statuses_and_examples() -> None:
    app.openapi_schema = None
    schema = app.openapi()

    for (method, path), expected_codes in SETTINGS_ERROR_CODES.items():
        operation = schema["paths"][path][method]
        assert operation["x-error-codes"] == list(expected_codes)
        assert set(operation["responses"]) == {
            "200",
            *(str(status) for status in error_statuses(expected_codes)),
        }
        assert operation["security"] == [{"BearerAuth": []}]

        for code in expected_codes:
            status = str(ADMIN_ERROR_CODES[code]["status"])
            media = operation["responses"][status]["content"]["application/json"]
            assert media["schema"] == {
                "$ref": "#/components/schemas/APIErrorResponse"
            }
            assert media["examples"][f"code_{code}"]["value"]["code"] == int(code)


def test_credential_operations_require_bounded_idempotency_keys() -> None:
    app.openapi_schema = None
    paths = app.openapi()["paths"]
    credential_operations = (
        paths["/admin/settings/admins"]["post"],
        paths["/admin/settings/admins/{admin_id}/enable"]["post"],
        paths["/admin/settings/admins/{admin_id}/password-reset"]["post"],
        paths["/admin/settings/admins/{admin_id}/password"]["put"],
    )

    for operation in credential_operations:
        header = next(
            parameter
            for parameter in operation["parameters"]
            if parameter["in"] == "header"
            and parameter["name"].lower() == "idempotency-key"
        )
        assert header["required"] is True
        assert header["schema"]["minLength"] == 16
        assert header["schema"]["maxLength"] == 64
        assert header["schema"]["pattern"] == "^[A-Za-z0-9._:-]+$"
