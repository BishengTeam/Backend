#!/usr/bin/env python3
"""Report required production feature configuration by name only.

The checker deliberately never prints endpoint URLs or credential values.  It
is safe to run in CI, inside a production container, or from an orchestration
host before recreating services.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


def _value(name: str) -> str:
    return os.environ.get(name, "").strip()


def _bool(name: str) -> bool | None:
    value = _value(name).lower()
    if value in TRUE_VALUES:
        return True
    if value in FALSE_VALUES:
        return False
    return None


def _secret_present(name: str) -> bool:
    if _value(name):
        return True
    file_path = _value(f"{name}_FILE")
    if not file_path:
        return False
    # Only verify that the mounted file exists; never read or display content.
    return Path(file_path).is_file()


def feature_configuration_report(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Return missing/invalid configuration names without secret material."""

    if env is not None:
        old_values: dict[str, str] = {}
        names = {
            "APP_ENV",
            "BANK_TRANSFER_NEW_ORDERS_ENABLED",
            "RENSHE_STORAGE_TYPE",
            "COURSE_DOCUMENT_UPLOADS_ENABLED",
            "ALIYUN_OSS_ENDPOINT",
            "ALIYUN_OSS_BUCKET",
            "ALIYUN_OSS_ACCESS_KEY_ID",
            "ALIYUN_OSS_ACCESS_KEY_ID_FILE",
            "ALIYUN_OSS_ACCESS_KEY_SECRET",
            "ALIYUN_OSS_ACCESS_KEY_SECRET_FILE",
            "QUIZ_TASKS_ENABLED",
            "QUIZ_EMBEDDED_WORKER_ENABLED",
            "QUIZ_IMPORT_STORAGE_TYPE",
            "QUIZ_OSS_ENDPOINT",
            "QUIZ_OSS_BUCKET",
            "VIDEOWEB_BASE_URL",
            "VIDEOWEB_SERVICE_TOKEN",
            "VIDEOWEB_SERVICE_TOKEN_FILE",
            "VIDEOWEB_LOOKUP_SERVICE_TOKEN",
            "VIDEOWEB_LOOKUP_SERVICE_TOKEN_FILE",
            "CHAT_BACKEND",
            "DIFY_API_BASE",
            "DIFY_API_KEY",
            "GZH_APPID",
            "GZH_APP_SECRET",
            "GZH_APP_SECRET_FILE",
        }
        try:
            for name in names:
                old_values[name] = os.environ.get(name, "")
                if name in env:
                    os.environ[name] = env[name]
                else:
                    os.environ.pop(name, None)
            return feature_configuration_report()
        finally:
            for name, value in old_values.items():
                if value:
                    os.environ[name] = value
                else:
                    os.environ.pop(name, None)

    missing_required: list[str] = []
    invalid_required: list[str] = []

    def require_value(name: str) -> None:
        if not _value(name):
            missing_required.append(name)

    def require_true(name: str) -> None:
        if _bool(name) is not True:
            invalid_required.append(name)

    def require_false(name: str) -> None:
        if _bool(name) is not False:
            invalid_required.append(name)

    def require_value_in(name: str, expected: str) -> None:
        if _value(name).lower() != expected:
            invalid_required.append(name)

    if _value("APP_ENV") != "production":
        invalid_required.append("APP_ENV")
    require_true("BANK_TRANSFER_NEW_ORDERS_ENABLED")
    require_value_in("RENSHE_STORAGE_TYPE", "aliyun_oss")
    require_true("COURSE_DOCUMENT_UPLOADS_ENABLED")
    for name in (
        "ALIYUN_OSS_ENDPOINT",
        "ALIYUN_OSS_BUCKET",
        "QUIZ_OSS_ENDPOINT",
        "QUIZ_OSS_BUCKET",
        "VIDEOWEB_BASE_URL",
    ):
        require_value(name)
    for name in (
        "ALIYUN_OSS_ACCESS_KEY_ID",
        "ALIYUN_OSS_ACCESS_KEY_SECRET",
        "VIDEOWEB_SERVICE_TOKEN",
        "VIDEOWEB_LOOKUP_SERVICE_TOKEN",
    ):
        if not _secret_present(name):
            missing_required.append(name)

    require_true("QUIZ_TASKS_ENABLED")
    require_false("QUIZ_EMBEDDED_WORKER_ENABLED")
    require_value_in("QUIZ_IMPORT_STORAGE_TYPE", "aliyun_oss")

    missing_optional: list[str] = []
    chat_backend = _value("CHAT_BACKEND").lower()
    if chat_backend == "dify":
        if not _value("DIFY_API_BASE"):
            missing_required.append("DIFY_API_BASE")
        if not _secret_present("DIFY_API_KEY"):
            missing_required.append("DIFY_API_KEY")
    elif not chat_backend:
        missing_optional.append("CHAT_BACKEND")

    gzh_id_present = bool(_value("GZH_APPID"))
    gzh_secret_present = _secret_present("GZH_APP_SECRET")
    if gzh_id_present != gzh_secret_present:
        missing_required.extend(
            name
            for name, exists in (
                ("GZH_APPID", gzh_id_present),
                ("GZH_APP_SECRET", gzh_secret_present),
            )
            if not exists
        )
    elif not gzh_id_present:
        missing_optional.extend(("GZH_APPID", "GZH_APP_SECRET"))

    # Keep output deterministic and stable for deployment automation.
    missing_required = sorted(set(missing_required))
    invalid_required = sorted(set(invalid_required))
    missing_optional = sorted(set(missing_optional))
    ready = not missing_required and not invalid_required
    return {
        "status": "ready" if ready else "incomplete",
        "missing_required": missing_required,
        "invalid_required": invalid_required,
        "missing_optional": missing_optional,
    }


def main() -> int:
    report = feature_configuration_report()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
