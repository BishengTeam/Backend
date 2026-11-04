from __future__ import annotations

from pathlib import Path

from app.port.config import Settings, _load_secret_files
from scripts.check_production_features import feature_configuration_report


def _ready_env(tmp_path: Path) -> dict[str, str]:
    return {
        "APP_ENV": "production",
        "BANK_TRANSFER_NEW_ORDERS_ENABLED": "true",
        "RENSHE_STORAGE_TYPE": "aliyun_oss",
        "COURSE_DOCUMENT_UPLOADS_ENABLED": "true",
        "ALIYUN_OSS_ENDPOINT": "https://oss.example.test",
        "ALIYUN_OSS_BUCKET": "materials",
        "ALIYUN_OSS_ACCESS_KEY_ID": "secret-id",
        "ALIYUN_OSS_ACCESS_KEY_SECRET": "secret-key",
        "QUIZ_TASKS_ENABLED": "true",
        "QUIZ_EMBEDDED_WORKER_ENABLED": "false",
        "QUIZ_IMPORT_STORAGE_TYPE": "aliyun_oss",
        "QUIZ_OSS_ENDPOINT": "https://oss.example.test",
        "QUIZ_OSS_BUCKET": "quiz",
        "VIDEOWEB_BASE_URL": "http://videoweb.internal:8100",
        "VIDEOWEB_SERVICE_TOKEN": "a" * 32,
        "VIDEOWEB_LOOKUP_SERVICE_TOKEN": "b" * 32,
        "CHAT_BACKEND": "dify",
        "DIFY_API_BASE": "http://dify.internal/v1",
        "DIFY_API_KEY": "c" * 32,
        "GZH_APPID": "wx0000000000000000",
        "GZH_APP_SECRET": "d" * 32,
    }


def test_report_is_ready_and_never_contains_values(tmp_path: Path) -> None:
    values = _ready_env(tmp_path)
    report = feature_configuration_report(values)

    assert report == {
        "status": "ready",
        "missing_required": [],
        "invalid_required": [],
        "missing_optional": [],
    }
    assert "secret-id" not in str(report)
    assert "videoweb.internal" not in str(report)


def test_report_names_disabled_features_without_secret_material(tmp_path: Path) -> None:
    values = _ready_env(tmp_path)
    values.update(
        {
            "COURSE_DOCUMENT_UPLOADS_ENABLED": "false",
            "RENSHE_STORAGE_TYPE": "disabled",
            "QUIZ_IMPORT_STORAGE_TYPE": "disabled",
            "VIDEOWEB_BASE_URL": "",
            "VIDEOWEB_SERVICE_TOKEN": "",
            "CHAT_BACKEND": "",
            "GZH_APPID": "",
            "GZH_APP_SECRET": "",
        }
    )
    values.pop("VIDEOWEB_LOOKUP_SERVICE_TOKEN")

    report = feature_configuration_report(values)

    assert report["status"] == "incomplete"
    assert "VIDEOWEB_BASE_URL" in report["missing_required"]
    assert "VIDEOWEB_SERVICE_TOKEN" in report["missing_required"]
    assert "VIDEOWEB_LOOKUP_SERVICE_TOKEN" in report["missing_required"]
    assert {
        "COURSE_DOCUMENT_UPLOADS_ENABLED",
        "RENSHE_STORAGE_TYPE",
        "QUIZ_IMPORT_STORAGE_TYPE",
    } <= set(report["invalid_required"])
    assert {"CHAT_BACKEND", "GZH_APPID", "GZH_APP_SECRET"} <= set(
        report["missing_optional"]
    )


def test_videoweb_service_token_secret_files_are_settings_fields(
    tmp_path: Path,
) -> None:
    assert "VIDEOWEB_SERVICE_TOKEN_FILE" in Settings.model_fields
    assert "VIDEOWEB_LOOKUP_SERVICE_TOKEN_FILE" in Settings.model_fields

    service = tmp_path / "videoweb-service-token"
    lookup = tmp_path / "videoweb-lookup-token"
    service.write_text("s" * 64, encoding="utf-8")
    lookup.write_text("l" * 64, encoding="utf-8")

    loaded = _load_secret_files(
        {
            "VIDEOWEB_SERVICE_TOKEN_FILE": str(service),
            "VIDEOWEB_LOOKUP_SERVICE_TOKEN_FILE": str(lookup),
        }
    )

    assert loaded["VIDEOWEB_SERVICE_TOKEN"] == "s" * 64
    assert loaded["VIDEOWEB_LOOKUP_SERVICE_TOKEN"] == "l" * 64
