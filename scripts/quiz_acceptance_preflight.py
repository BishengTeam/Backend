#!/usr/bin/env python3
"""Run the read-only safety gate before a QF-55 real-environment UAT.

The command performs no application writes.  It refuses broad database names,
checks a disposable PostgreSQL target in read-only mode, verifies the frozen
fixture and HTTP contracts, and requires real Redis/worker/private quiz OSS
readiness before an operator starts the acceptance run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.check_migrations import static_check  # noqa: E402
from scripts.postgres_backup import (  # noqa: E402
    DatabaseTarget,
    REQUIRED_QUIZ_TABLES,
)
from scripts.quiz_acceptance_fixtures import (  # noqa: E402
    FIXTURE_PREFIX,
    check as check_fixtures,
)


ALLOWED_DATABASE_SUFFIXES = ("_test", "_uat", "_acceptance")
MAX_HTTP_RESPONSE_BYTES = 8 * 1024 * 1024
REQUIRED_ADMIN_PERMISSIONS = {"quiz:list", "quiz:write", "quiz:import"}


class PreflightError(RuntimeError):
    """A safe-to-print preflight failure without credential material."""


def validate_database_target(
    target: DatabaseTarget,
    *,
    confirmed_database: str,
) -> None:
    if target.database != confirmed_database:
        raise PreflightError("database confirmation does not match the URL target")
    if not target.database.endswith(ALLOWED_DATABASE_SUFFIXES):
        raise PreflightError(
            "acceptance database must end with _test, _uat, or _acceptance"
        )


def _one(row: object, label: str) -> object:
    if not isinstance(row, (tuple, list)) or len(row) != 1:
        raise PreflightError(f"database returned an invalid {label} result")
    return row[0]


def _check_database(
    target: DatabaseTarget,
    *,
    expected_head: str,
    expected_backup_fingerprint: str | None = None,
    connector: Callable[..., Any] | None = None,
    require_clean_quiz: bool,
) -> dict[str, object]:
    """Check the target using a PostgreSQL read-only session."""

    connection = None
    try:
        if connector is None:
            import psycopg2

            connector = psycopg2.connect
        connection_options: dict[str, object] = {
            "host": target.host,
            "port": target.port,
            "user": target.user,
            "password": target.password,
            "dbname": target.database,
            "connect_timeout": 5,
        }
        if target.sslmode:
            connection_options["sslmode"] = target.sslmode
        connection = connector(
            **connection_options,
        )
        connection.set_session(readonly=True, autocommit=True)
        cursor = connection.cursor()
        try:
            cursor.execute("SHOW transaction_read_only")
            if str(_one(cursor.fetchone(), "read-only mode")).lower() != "on":
                raise PreflightError("database connection is not read-only")

            cursor.execute(
                "SELECT current_database(), "
                "(SELECT system_identifier::text FROM pg_control_system())"
            )
            identity = cursor.fetchone()
            if not isinstance(identity, (tuple, list)) or len(identity) != 2:
                raise PreflightError("database returned an invalid identity result")
            actual_database = str(identity[0])
            if actual_database != target.database:
                raise PreflightError("connected database differs from the requested target")

            if expected_backup_fingerprint is not None:
                actual_fingerprint = hashlib.sha256(
                    (
                        f"{identity[1]}/{actual_database}"
                    ).encode("utf-8")
                ).hexdigest()
                if actual_fingerprint != expected_backup_fingerprint:
                    raise PreflightError(
                        "pre-run backup does not belong to the confirmed database target"
                    )

            cursor.execute("SELECT version_num FROM alembic_version ORDER BY version_num")
            revisions = [str(_one(row, "Alembic revision")) for row in cursor.fetchall()]
            if revisions != [expected_head]:
                raise PreflightError("database Alembic revision is not the repository head")

            if not require_clean_quiz:
                return {
                    "database": target.database,
                    "database_fingerprint_sha256": hashlib.sha256(
                        f"{identity[1]}/{target.database}".encode("utf-8")
                    ).hexdigest(),
                    "read_only": True,
                    "alembic_head": expected_head,
                }

            cursor.execute(
                "SELECT tablename FROM pg_catalog.pg_tables "
                "WHERE schemaname = 'public' AND tablename = ANY(%s)",
                (list(REQUIRED_QUIZ_TABLES),),
            )
            table_names = {str(_one(row, "quiz table")) for row in cursor.fetchall()}
            missing = sorted(set(REQUIRED_QUIZ_TABLES) - table_names)
            if missing:
                raise PreflightError(
                    "database is missing required quiz tables: " + ", ".join(missing)
                )

            cursor.execute(
                "SELECT count(*) FROM quiz_category WHERE name = %s",
                ("QF55 黄金题库",),
            )
            category_count = int(_one(cursor.fetchone(), "fixture category count"))
            cursor.execute(
                "SELECT count(*) FROM quiz_question WHERE question_text LIKE %s",
                (f"{FIXTURE_PREFIX}-%",),
            )
            question_count = int(_one(cursor.fetchone(), "fixture question count"))
            if category_count or question_count:
                raise PreflightError(
                    "database already contains QF-55 fixture data; restore the clean snapshot"
                )

            active_resource_counts: dict[str, int] = {}
            for label, query in (
                (
                    "in-progress practice sessions",
                    "SELECT count(*) FROM quiz_practice_session WHERE status = 'in_progress'",
                ),
                (
                    "in-progress exams",
                    "SELECT count(*) FROM quiz_exam WHERE status = 'in_progress'",
                ),
                (
                    "queued or running imports",
                    "SELECT count(*) FROM quiz_import_job "
                    "WHERE status IN ('queued', 'validating', 'importing')",
                ),
            ):
                cursor.execute(query)
                active_resource_counts[label] = int(
                    _one(cursor.fetchone(), label)
                )
            dirty = [
                f"{label}={count}"
                for label, count in active_resource_counts.items()
                if count
            ]
            if dirty:
                raise PreflightError(
                    "acceptance database has active quiz resources: " + ", ".join(dirty)
                )
        finally:
            cursor.close()
    except PreflightError:
        raise
    except Exception as exc:
        raise PreflightError(
            f"database preflight failed ({type(exc).__name__})"
        ) from exc
    finally:
        if connection is not None:
            connection.close()
    return {
        "database": target.database,
        "database_fingerprint_sha256": hashlib.sha256(
            (
                f"{identity[1]}/{target.database}"
            ).encode("utf-8")
        ).hexdigest(),
        "read_only": True,
        "alembic_head": expected_head,
        "quiz_table_count": len(REQUIRED_QUIZ_TABLES),
        "fixture_rows_before_run": 0,
    }


def check_database(
    target: DatabaseTarget,
    *,
    expected_head: str,
    expected_backup_fingerprint: str | None = None,
    connector: Callable[..., Any] | None = None,
) -> dict[str, object]:
    """Validate a clean, inactive acceptance database before writes."""

    return _check_database(
        target,
        expected_head=expected_head,
        expected_backup_fingerprint=expected_backup_fingerprint,
        connector=connector,
        require_clean_quiz=True,
    )


def check_database_identity(
    target: DatabaseTarget,
    *,
    expected_head: str,
    expected_backup_fingerprint: str | None = None,
    connector: Callable[..., Any] | None = None,
) -> dict[str, object]:
    """Read only the DB identity/head after UAT has intentionally written data."""

    return _check_database(
        target,
        expected_head=expected_head,
        expected_backup_fingerprint=expected_backup_fingerprint,
        connector=connector,
        require_clean_quiz=False,
    )


def validate_api_base(raw: str) -> str:
    value = raw.strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise PreflightError("API base must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise PreflightError("API base must not contain credentials, query, or fragment")
    if parsed.path not in {"", "/"}:
        raise PreflightError("API base must be an origin without a path")
    return value


def _http_json(
    api_base: str,
    path: str,
    *,
    token: str | None = None,
    expected_statuses: tuple[int, ...] = (200,),
    timeout: float = 5.0,
    opener: Callable[..., Any] = urlopen,
) -> tuple[int, dict[str, Any]]:
    headers = {"Accept": "application/json", "User-Agent": "qf55-preflight/1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(f"{api_base}{path}", headers=headers, method="GET")
    try:
        response = opener(request, timeout=timeout)
        status = int(getattr(response, "status", response.getcode()))
        content = response.read(MAX_HTTP_RESPONSE_BYTES + 1)
        close = getattr(response, "close", None)
        if close is not None:
            close()
    except HTTPError as exc:
        status = int(exc.code)
        content = exc.read(MAX_HTTP_RESPONSE_BYTES + 1)
    except (OSError, URLError) as exc:
        raise PreflightError(
            f"HTTP preflight could not reach {path} ({type(exc).__name__})"
        ) from exc
    if status not in expected_statuses:
        raise PreflightError(f"HTTP preflight {path} returned status {status}")
    if len(content) > MAX_HTTP_RESPONSE_BYTES:
        raise PreflightError(f"HTTP preflight response is too large: {path}")
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreflightError(f"HTTP preflight returned invalid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise PreflightError(f"HTTP preflight returned a non-object: {path}")
    return status, payload


def _require_code_zero(payload: dict[str, Any], path: str) -> None:
    if payload.get("code") != 0:
        raise PreflightError(f"HTTP preflight business response failed: {path}")


def _require_clean_user_quiz_stats(payload: dict[str, Any]) -> None:
    data = payload.get("data")
    practice = data.get("practice") if isinstance(data, dict) else None
    exam = data.get("exam") if isinstance(data, dict) else None
    if not isinstance(practice, dict) or not isinstance(exam, dict):
        raise PreflightError("user quiz statistics response is invalid")
    zero_practice = (
        "total_attempts",
        "first_attempts",
        "first_correct_attempts",
        "answered_questions",
        "active_wrong_count",
        "active_collection_count",
        "checkin_days",
        "consecutive_days",
        "today_questions",
    )
    zero_exam = (
        "completed_exam_count",
        "timed_out_exam_count",
        "total_questions",
        "correct_count",
        "wrong_count",
        "unanswered_count",
    )
    if not set(zero_practice) <= set(practice) or not set(zero_exam) <= set(exam):
        raise PreflightError("user quiz statistics response is incomplete")
    try:
        dirty = any(float(practice.get(name, 0)) != 0 for name in zero_practice)
        dirty = dirty or any(float(exam.get(name, 0)) != 0 for name in zero_exam)
    except (TypeError, ValueError) as exc:
        raise PreflightError("user quiz statistics response is invalid") from exc
    if dirty:
        raise PreflightError(
            "QF-55 requires dedicated users with no existing quiz history"
        )


def _check_admin(
    api_base: str,
    token: str,
    *,
    expected_role: str,
    timeout: float,
    opener: Callable[..., Any],
) -> int:
    _status, payload = _http_json(
        api_base,
        "/admin/auth/me",
        token=token,
        timeout=timeout,
        opener=opener,
    )
    _require_code_zero(payload, "/admin/auth/me")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("admin"), dict):
        raise PreflightError("administrator identity response is invalid")
    admin = data["admin"]
    if admin.get("role") != expected_role:
        raise PreflightError(f"expected a {expected_role} acceptance account")
    permissions = set(data.get("permissions") or [])
    if expected_role == "super_admin":
        if "*" not in permissions:
            raise PreflightError("super administrator wildcard permission is missing")
    elif not REQUIRED_ADMIN_PERMISSIONS <= permissions:
        raise PreflightError("normal administrator lacks required quiz permissions")
    try:
        return int(admin["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PreflightError("administrator identity has an invalid ID") from exc


def _runtime_quiz_operations(schema: dict[str, Any]) -> set[tuple[str, str]]:
    operations: set[tuple[str, str]] = set()
    for path, path_item in (schema.get("paths") or {}).items():
        if not isinstance(path, str) or not path.startswith(("/api/quiz", "/admin/quiz")):
            continue
        if not isinstance(path_item, dict):
            continue
        for method in ("get", "post", "put", "patch", "delete"):
            if method in path_item:
                operations.add((method.upper(), path))
    return operations


def check_http_environment(
    api_base: str,
    *,
    confirmed_database: str,
    confirmed_database_fingerprint: str | None = None,
    admin_token: str,
    super_admin_token: str,
    disabled_admin_token: str,
    user_token: str,
    other_user_token: str,
    contract_manifest: dict[str, Any],
    timeout: float = 5.0,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, object]:
    _status, health = _http_json(
        api_base, "/health", timeout=timeout, opener=opener
    )
    _require_code_zero(health, "/health")

    _status, ready = _http_json(
        api_base, "/ready", timeout=timeout, opener=opener
    )
    _require_code_zero(ready, "/ready")
    if ready.get("status") != "ready":
        raise PreflightError("runtime is not ready")
    checks = ready.get("checks")
    details = ready.get("details")
    if not isinstance(checks, dict) or not isinstance(details, dict):
        raise PreflightError("readiness response is missing dependency details")
    for dependency in ("database", "redis", "quiz_oss", "quiz_worker"):
        if checks.get(dependency) != "ok":
            raise PreflightError(f"required acceptance dependency is unavailable: {dependency}")
    quiz_oss = details.get("quiz_oss")
    if not isinstance(quiz_oss, dict) or (
        quiz_oss.get("mode") != "aliyun_oss" or quiz_oss.get("probe") != "ok"
    ):
        raise PreflightError("QF-55 requires a reachable private quiz OSS bucket")
    quiz_tasks = details.get("quiz_tasks")
    if not isinstance(quiz_tasks, dict) or quiz_tasks.get("source") != "redis":
        raise PreflightError("QF-55 requires independent Worker metrics from Redis")
    signals = quiz_tasks.get("signals")
    if not isinstance(signals, dict) or signals.get("ready") is not True:
        raise PreflightError("independent quiz Worker heartbeat is not ready")

    database_details = details.get("database")
    if confirmed_database_fingerprint is None:
        raise PreflightError("confirmed database fingerprint is required")
    expected_database_fingerprint = confirmed_database_fingerprint
    if not isinstance(database_details, dict) or (
        database_details.get("fingerprint_sha256") != expected_database_fingerprint
    ):
        raise PreflightError("runtime API is not bound to the confirmed acceptance database")

    _status, schema = _http_json(
        api_base, "/openapi.json", timeout=timeout, opener=opener
    )
    expected_operations = {
        (str(item["method"]), str(item["path"]))
        for item in contract_manifest["operations"]
    }
    actual_operations = _runtime_quiz_operations(schema)
    if actual_operations != expected_operations:
        raise PreflightError("runtime quiz operations differ from the frozen manifest")
    paths = schema.get("paths") or {}
    contract_version = contract_manifest["quiz_contract_version"]
    for item in contract_manifest["operations"]:
        operation = paths[item["path"]][str(item["method"]).lower()]
        if operation.get("x-quiz-contract-version") != contract_version:
            raise PreflightError("runtime quiz contract version differs from the manifest")
    for item in contract_manifest["removed_operations"]:
        path_item = paths.get(item["path"], {})
        if str(item["method"]).lower() in path_item:
            raise PreflightError("a removed quiz operation is still exposed")

    _status, public_categories = _http_json(
        api_base, "/api/quiz/categories", timeout=timeout, opener=opener
    )
    _require_code_zero(public_categories, "/api/quiz/categories")
    anonymous_status, anonymous_questions = _http_json(
        api_base,
        "/api/quiz/questions",
        expected_statuses=(401,),
        timeout=timeout,
        opener=opener,
    )
    if anonymous_status != 401 or anonymous_questions.get("code") != 40100:
        raise PreflightError("anonymous question access is not rejected by the frozen contract")

    admin_id = _check_admin(
        api_base,
        admin_token,
        expected_role="admin",
        timeout=timeout,
        opener=opener,
    )
    super_admin_id = _check_admin(
        api_base,
        super_admin_token,
        expected_role="super_admin",
        timeout=timeout,
        opener=opener,
    )
    if admin_id == super_admin_id:
        raise PreflightError("normal and super administrator accounts must be distinct")

    disabled_status, disabled_response = _http_json(
        api_base,
        "/admin/auth/me",
        token=disabled_admin_token,
        expected_statuses=(401,),
        timeout=timeout,
        opener=opener,
    )
    if disabled_status != 401 or disabled_response.get("code") != 40100:
        raise PreflightError("disabled administrator is not rejected")

    user_ids: list[int] = []
    for token in (user_token, other_user_token):
        _status, profile = _http_json(
            api_base,
            "/api/user/profile",
            token=token,
            timeout=timeout,
            opener=opener,
        )
        _require_code_zero(profile, "/api/user/profile")
        profile_data = profile.get("data")
        if not isinstance(profile_data, dict):
            raise PreflightError("user profile response is invalid")
        try:
            user_ids.append(int(profile_data["id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise PreflightError("user profile has an invalid ID") from exc
        _status, stats = _http_json(
            api_base,
            "/api/quiz/stats",
            token=token,
            timeout=timeout,
            opener=opener,
        )
        _require_code_zero(stats, "/api/quiz/stats")
        _require_clean_user_quiz_stats(stats)
    if len(set(user_ids)) != 2:
        raise PreflightError("the two acceptance tokens must belong to distinct users")

    return {
        "ready": True,
        "quiz_operation_count": len(actual_operations),
        "removed_operation_count": len(contract_manifest["removed_operations"]),
        "normal_admin": True,
        "super_admin": True,
        "user_accounts": 2,
        "quiz_oss": "private_aliyun_oss",
        "worker_metrics_source": "redis",
    }


def _read_secret(
    *,
    environment_name: str,
    file_path: Path | None,
) -> str:
    direct = (os.getenv(environment_name) or "").strip()
    if direct and file_path is not None:
        raise PreflightError(
            f"{environment_name} and its file option are mutually exclusive"
        )
    if file_path is not None:
        try:
            direct = file_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise PreflightError(f"cannot read secret file for {environment_name}") from exc
    if not direct:
        raise PreflightError(f"{environment_name} or its file option is required")
    return direct


def _load_contract_manifest() -> dict[str, Any]:
    path = ROOT / "app/contracts/quiz_contract_manifest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreflightError("cannot read the frozen quiz contract manifest") from exc
    if not isinstance(payload, dict) or payload.get("operation_count") != 52:
        raise PreflightError("the frozen quiz contract manifest is invalid")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-dir", type=Path, required=True)
    parser.add_argument("--database-url-file", type=Path)
    parser.add_argument(
        "--confirm-database",
        default=os.getenv("QF55_CONFIRM_DATABASE", ""),
        help="必须与目标数据库名完全一致",
    )
    parser.add_argument("--api-base", default=os.getenv("QF55_API_BASE", ""))
    parser.add_argument("--admin-token-file", type=Path)
    parser.add_argument("--super-admin-token-file", type=Path)
    parser.add_argument("--disabled-admin-token-file", type=Path)
    parser.add_argument("--user-token-file", type=Path)
    parser.add_argument("--other-user-token-file", type=Path)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        if not 0.5 <= args.timeout <= 30:
            raise PreflightError("HTTP timeout must be between 0.5 and 30 seconds")
        database_url = _read_secret(
            environment_name="QF55_DATABASE_URL",
            file_path=args.database_url_file,
        )
        target = DatabaseTarget.from_url(database_url)
        validate_database_target(
            target,
            confirmed_database=args.confirm_database.strip(),
        )
        api_base = validate_api_base(args.api_base)
        admin_token = _read_secret(
            environment_name="QF55_ADMIN_TOKEN",
            file_path=args.admin_token_file,
        )
        super_admin_token = _read_secret(
            environment_name="QF55_SUPER_ADMIN_TOKEN",
            file_path=args.super_admin_token_file,
        )
        disabled_admin_token = _read_secret(
            environment_name="QF55_DISABLED_ADMIN_TOKEN",
            file_path=args.disabled_admin_token_file,
        )
        user_token = _read_secret(
            environment_name="QF55_USER_TOKEN",
            file_path=args.user_token_file,
        )
        other_user_token = _read_secret(
            environment_name="QF55_OTHER_USER_TOKEN",
            file_path=args.other_user_token_file,
        )

        fixture_manifest = check_fixtures(args.fixture_dir)
        migration = static_check()
        heads = migration["heads"]
        if not isinstance(heads, list) or len(heads) != 1:
            raise PreflightError("repository must have exactly one Alembic head")
        database_report = check_database(
            target,
            expected_head=str(heads[0]),
        )
        http_report = check_http_environment(
            api_base,
            confirmed_database=target.database,
            confirmed_database_fingerprint=str(
                database_report["database_fingerprint_sha256"]
            ),
            admin_token=admin_token,
            super_admin_token=super_admin_token,
            disabled_admin_token=disabled_admin_token,
            user_token=user_token,
            other_user_token=other_user_token,
            contract_manifest=_load_contract_manifest(),
            timeout=args.timeout,
        )
        report = {
            "status": "ready_for_qf55_writes",
            "fixture_version": fixture_manifest["fixture_version"],
            "fixture_sha256": fixture_manifest["definition_sha256"],
            "database": database_report,
            "http": http_report,
        }
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(
                "quiz_acceptance_preflight=ok "
                f"database={database_report['database']} "
                f"fixtures={report['fixture_version']} "
                f"operations={http_report['quiz_operation_count']}"
            )
        return 0
    except (PreflightError, RuntimeError) as exc:
        print(f"quiz acceptance preflight failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
