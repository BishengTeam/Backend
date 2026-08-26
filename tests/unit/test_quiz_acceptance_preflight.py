from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from app.contracts.quiz import QUIZ_CONTRACT_VERSION
from scripts.postgres_backup import DatabaseTarget, REQUIRED_QUIZ_TABLES
from scripts.quiz_acceptance_preflight import (
    PreflightError,
    check_database,
    check_http_environment,
    validate_api_base,
    validate_database_target,
)
from scripts.quiz_contract_manifest import canonical_manifest


class _Cursor:
    def __init__(self, *, database: str = "wemini_app_acceptance") -> None:
        self.database = database
        self.query = ""

    def execute(self, query, _params=None) -> None:
        self.query = query

    def fetchone(self):
        if "transaction_read_only" in self.query:
            return ("on",)
        if "current_database" in self.query:
            return (self.database, "test-system-id")
        if any(
            table in self.query
            for table in (
                "quiz_category",
                "quiz_question",
                "quiz_practice_session",
                "quiz_exam",
                "quiz_import_job",
            )
        ):
            return (0,)
        raise AssertionError(self.query)

    def fetchall(self):
        if "alembic_version" in self.query:
            return [("quiz003",)]
        if "pg_catalog.pg_tables" in self.query:
            return [(name,) for name in REQUIRED_QUIZ_TABLES]
        raise AssertionError(self.query)

    def close(self) -> None:
        return None


class _Connection:
    def __init__(self, *, database: str = "wemini_app_acceptance") -> None:
        self.database = database
        self.session: tuple[bool, bool] | None = None
        self.closed = False

    def set_session(self, *, readonly: bool, autocommit: bool) -> None:
        self.session = (readonly, autocommit)

    def cursor(self):
        return _Cursor(database=self.database)

    def close(self) -> None:
        self.closed = True


def _target(database: str = "wemini_app_acceptance") -> DatabaseTarget:
    return DatabaseTarget(
        host="db.test",
        port=5432,
        user="acceptance",
        password="secret",
        database=database,
    )


def test_database_preflight_requires_exact_disposable_target_and_read_only() -> None:
    target = _target()
    connection = _Connection()
    validate_database_target(target, confirmed_database=target.database)

    report = check_database(
        target,
        expected_head="quiz003",
        connector=lambda **_kwargs: connection,
    )

    assert connection.session == (True, True)
    assert connection.closed is True
    assert report["read_only"] is True
    assert report["quiz_table_count"] == 16

    fingerprint = hashlib.sha256(
        f"test-system-id/{target.database}".encode()
    ).hexdigest()
    check_database(
        target,
        expected_head="quiz003",
        expected_backup_fingerprint=fingerprint,
        connector=lambda **_kwargs: _Connection(),
    )
    with pytest.raises(PreflightError, match="backup does not belong"):
        check_database(
            target,
            expected_head="quiz003",
            expected_backup_fingerprint="0" * 64,
            connector=lambda **_kwargs: _Connection(),
        )

    with pytest.raises(PreflightError, match="confirmation"):
        validate_database_target(target, confirmed_database="another_acceptance")
    with pytest.raises(PreflightError, match="must end"):
        validate_database_target(_target("wemini_app_dev"), confirmed_database="wemini_app_dev")


def test_api_base_rejects_credentials_query_and_non_http() -> None:
    assert validate_api_base("https://uat.example.test/") == "https://uat.example.test"
    for value in (
        "postgresql://db/test",
        "https://user:secret@example.test",
        "https://example.test?token=secret",
    ):
        with pytest.raises(PreflightError):
            validate_api_base(value)


class _Response:
    def __init__(self, status: int, payload: dict) -> None:
        self.status = status
        self.content = json.dumps(payload).encode("utf-8")

    def getcode(self) -> int:
        return self.status

    def read(self, _limit: int) -> bytes:
        return self.content

    def close(self) -> None:
        return None


def _openapi(manifest: dict) -> dict:
    paths: dict[str, dict] = {}
    for item in manifest["operations"]:
        operation = {
            "x-quiz-contract-version": QUIZ_CONTRACT_VERSION,
        }
        if item["auth"] != "public":
            operation["security"] = [{"BearerAuth": []}]
        paths.setdefault(item["path"], {})[item["method"].lower()] = operation
    return {"paths": paths}


def _opener(manifest: dict):
    database_fingerprint = hashlib.sha256(
        b"test-system-id/wemini_app_acceptance"
    ).hexdigest()
    normal = {
        "code": 0,
        "data": {
            "admin": {"id": 10, "role": "quiz_admin"},
            "permissions": ["quiz:list", "quiz:write", "quiz:import"],
        },
    }
    super_admin = {
        "code": 0,
        "data": {
            "admin": {"id": 11, "role": "super_admin"},
            "permissions": ["*"],
        },
    }

    def open_request(request: Request, *, timeout: float):
        assert timeout == 5
        path = request.full_url.removeprefix("https://uat.example.test")
        authorization = request.headers.get("Authorization")
        if path == "/health":
            return _Response(200, {"code": 0})
        if path == "/ready":
            return _Response(
                200,
                {
                    "code": 0,
                    "status": "ready",
                    "checks": {
                        "database": "ok",
                        "admin_identity": "ok",
                        "redis": "ok",
                        "quiz_oss": "ok",
                        "quiz_worker": "ok",
                    },
                    "details": {
                        "database": {
                            "status": "ok",
                            "fingerprint_sha256": database_fingerprint,
                        },
                        "admin_identity": {"status": "ok"},
                        "quiz_oss": {"mode": "aliyun_oss", "probe": "ok"},
                        "quiz_tasks": {
                            "source": "redis",
                            "signals": {"ready": True},
                        },
                    },
                },
            )
        if path == "/openapi.json":
            return _Response(200, _openapi(manifest))
        if path == "/api/quiz/categories":
            return _Response(200, {"code": 0, "data": []})
        if path == "/api/quiz/questions":
            payload = json.dumps({"code": 40100, "message": "unauthorized"}).encode()
            raise HTTPError(request.full_url, 401, "Unauthorized", {}, NoneWithRead(payload))
        if path == "/admin/auth/me":
            if authorization == "Bearer disabled":
                payload = json.dumps(
                    {"code": 40100, "message": "account disabled"}
                ).encode()
                raise HTTPError(
                    request.full_url, 401, "Unauthorized", {}, NoneWithRead(payload)
                )
            return _Response(200, super_admin if authorization == "Bearer super" else normal)
        if path == "/api/user/profile":
            return _Response(
                200,
                {
                    "code": 0,
                    "data": {
                        "id": 21 if authorization == "Bearer user-a" else 22
                    },
                },
            )
        if path == "/api/quiz/stats":
            return _Response(
                200,
                {
                    "code": 0,
                    "data": {
                        "practice": {
                            "total_attempts": 0,
                            "first_attempts": 0,
                            "first_correct_attempts": 0,
                            "answered_questions": 0,
                            "active_wrong_count": 0,
                            "active_collection_count": 0,
                            "checkin_days": 0,
                            "consecutive_days": 0,
                            "today_questions": 0,
                        },
                        "exam": {
                            "completed_exam_count": 0,
                            "timed_out_exam_count": 0,
                            "total_questions": 0,
                            "correct_count": 0,
                            "wrong_count": 0,
                            "unanswered_count": 0,
                        },
                    },
                },
            )
        raise AssertionError(path)

    return open_request


def _database_fingerprint() -> str:
    return hashlib.sha256(b"test-system-id/wemini_app_acceptance").hexdigest()


class NoneWithRead:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def read(self, _limit: int = -1) -> bytes:
        return self.payload

    def close(self) -> None:
        return None


def test_http_preflight_requires_frozen_contract_accounts_private_oss_and_worker() -> None:
    manifest = canonical_manifest()

    report = check_http_environment(
        "https://uat.example.test",
        confirmed_database="wemini_app_acceptance",
        confirmed_database_fingerprint=_database_fingerprint(),
        admin_token="admin",
        super_admin_token="super",
        disabled_admin_token="disabled",
        user_token="user-a",
        other_user_token="user-b",
        contract_manifest=manifest,
        opener=_opener(manifest),
    )

    assert report["ready"] is True
    assert report["quiz_operation_count"] == 86
    assert report["removed_operation_count"] == 14
    assert report["quiz_oss"] == "private_aliyun_oss"
    assert report["worker_metrics_source"] == "redis"


def test_http_preflight_rejects_local_quiz_storage() -> None:
    manifest = canonical_manifest()
    base_opener = _opener(manifest)

    def local_oss(request: Request, *, timeout: float):
        response = base_opener(request, timeout=timeout)
        path = request.full_url.removeprefix("https://uat.example.test")
        if path == "/ready":
            payload = json.loads(response.content)
            payload["details"]["quiz_oss"] = {
                "mode": "local",
                "probe": "not_required",
            }
            return _Response(200, payload)
        return response

    with pytest.raises(PreflightError, match="private quiz OSS"):
        check_http_environment(
            "https://uat.example.test",
            confirmed_database="wemini_app_acceptance",
            confirmed_database_fingerprint=_database_fingerprint(),
            admin_token="admin",
            super_admin_token="super",
            disabled_admin_token="disabled",
            user_token="user-a",
            other_user_token="user-b",
            contract_manifest=manifest,
            opener=local_oss,
        )


def test_http_preflight_rejects_two_tokens_for_the_same_user() -> None:
    manifest = canonical_manifest()
    base_opener = _opener(manifest)

    def same_user(request: Request, *, timeout: float):
        path = request.full_url.removeprefix("https://uat.example.test")
        if path == "/api/user/profile":
            return _Response(200, {"code": 0, "data": {"id": 21}})
        return base_opener(request, timeout=timeout)

    with pytest.raises(PreflightError, match="distinct users"):
        check_http_environment(
            "https://uat.example.test",
            confirmed_database="wemini_app_acceptance",
            confirmed_database_fingerprint=_database_fingerprint(),
            admin_token="admin",
            super_admin_token="super",
            disabled_admin_token="disabled",
            user_token="user-a",
            other_user_token="user-b",
            contract_manifest=manifest,
            opener=same_user,
        )


def test_http_preflight_rejects_runtime_bound_to_another_database() -> None:
    manifest = canonical_manifest()
    base_opener = _opener(manifest)

    def wrong_database(request: Request, *, timeout: float):
        response = base_opener(request, timeout=timeout)
        path = request.full_url.removeprefix("https://uat.example.test")
        if path == "/ready":
            payload = json.loads(response.content)
            payload["details"]["database"]["fingerprint_sha256"] = hashlib.sha256(
                b"wemini_app_dev"
            ).hexdigest()
            return _Response(200, payload)
        return response

    with pytest.raises(PreflightError, match="confirmed acceptance database"):
        check_http_environment(
            "https://uat.example.test",
            confirmed_database="wemini_app_acceptance",
            confirmed_database_fingerprint=_database_fingerprint(),
            admin_token="admin",
            super_admin_token="super",
            disabled_admin_token="disabled",
            user_token="user-a",
            other_user_token="user-b",
            contract_manifest=manifest,
            opener=wrong_database,
        )


def test_http_preflight_rejects_users_with_existing_quiz_history() -> None:
    manifest = canonical_manifest()
    base_opener = _opener(manifest)

    def dirty_user(request: Request, *, timeout: float):
        response = base_opener(request, timeout=timeout)
        path = request.full_url.removeprefix("https://uat.example.test")
        if path == "/api/quiz/stats":
            payload = json.loads(response.content)
            payload["data"]["practice"]["total_attempts"] = 1
            return _Response(200, payload)
        return response

    with pytest.raises(PreflightError, match="no existing quiz history"):
        check_http_environment(
            "https://uat.example.test",
            confirmed_database="wemini_app_acceptance",
            confirmed_database_fingerprint=_database_fingerprint(),
            admin_token="admin",
            super_admin_token="super",
            disabled_admin_token="disabled",
            user_token="user-a",
            other_user_token="user-b",
            contract_manifest=manifest,
            opener=dirty_user,
        )
