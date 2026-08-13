#!/usr/bin/env python3
"""Verify QF-55's pending exam after the real 60-minute deadline.

This command is intentionally separate from the write-capable acceptance run.
It first proves, through a read-only PostgreSQL query, that the independent
Worker already settled the recorded exam.  Only then does it call the HTTP
detail and statistics endpoints, so their query-time fallback cannot be
mistaken for Worker evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.check_migrations import static_check  # noqa: E402
from scripts.postgres_backup import DatabaseTarget  # noqa: E402
from scripts.quiz_acceptance_preflight import (  # noqa: E402
    PreflightError,
    _read_secret,
    check_database_identity,
    validate_api_base,
    validate_database_target,
)
from scripts.quiz_acceptance_runner import (  # noqa: E402
    AcceptanceError,
    AcceptanceHttpClient,
    _iso_now,
    _new_report_path,
    _safe_report_directory,
    _write_json_atomic,
    load_timeout_checkpoint,
    run_timeout_followup,
    validate_backup_reference,
)


def check_worker_settled_exam(
    target: DatabaseTarget,
    *,
    exam_id: int,
    connector: Callable[..., Any] | None = None,
) -> dict[str, object]:
    """Prove settlement without invoking an HTTP fallback or changing data."""

    connection = None
    try:
        if connector is None:
            import psycopg2

            connector = psycopg2.connect
        options: dict[str, object] = {
            "host": target.host,
            "port": target.port,
            "user": target.user,
            "password": target.password,
            "dbname": target.database,
            "connect_timeout": 5,
        }
        if target.sslmode:
            options["sslmode"] = target.sslmode
        connection = connector(**options)
        connection.set_session(readonly=True, autocommit=True)
        cursor = connection.cursor()
        try:
            cursor.execute("SHOW transaction_read_only")
            read_only = cursor.fetchone()
            if not isinstance(read_only, (tuple, list)) or read_only != ("on",):
                raise AcceptanceError("timeout follow-up database session is not read-only")
            cursor.execute(
                "SELECT status, deadline_at, timed_out_at, submitted_at, abandoned_at, "
                "question_count, correct_count, wrong_count, unanswered_count "
                "FROM quiz_exam WHERE id = %s",
                (exam_id,),
            )
            row = cursor.fetchone()
        finally:
            cursor.close()
        if not isinstance(row, (tuple, list)) or len(row) != 9:
            raise AcceptanceError("timeout follow-up exam does not exist")
        (
            status,
            deadline_at,
            timed_out_at,
            submitted_at,
            abandoned_at,
            question_count,
            correct_count,
            wrong_count,
            unanswered_count,
        ) = row
        if status != "timed_out":
            raise AcceptanceError("independent Worker has not settled the exam as timed_out")
        if (
            timed_out_at is None
            or deadline_at is None
            or timed_out_at < deadline_at
            or submitted_at is not None
            or abandoned_at is not None
        ):
            raise AcceptanceError("timed-out exam lifecycle fields are inconsistent")
        counts = (correct_count, wrong_count, unanswered_count)
        if any(value is None for value in counts) or sum(int(value) for value in counts) != int(
            question_count
        ):
            raise AcceptanceError("timed-out exam result counts are inconsistent")
        return {"exam_id": exam_id, "status": "timed_out", "read_only": True}
    except (AcceptanceError, PreflightError):
        raise
    except Exception as exc:
        raise AcceptanceError(
            f"timeout follow-up database check failed ({type(exc).__name__})"
        ) from exc
    finally:
        if connection is not None:
            connection.close()


async def _verify_runtime(
    client: AcceptanceHttpClient,
    *,
    expected_database_fingerprint: str,
) -> dict[str, object]:
    payload, _http = await client.request("GET", "/ready")
    checks = payload.get("checks")
    details = payload.get("details")
    if payload.get("status") != "ready" or not isinstance(checks, dict) or not isinstance(
        details, dict
    ):
        raise AcceptanceError("timeout follow-up runtime is not ready")
    for dependency in ("database", "redis", "quiz_worker"):
        if checks.get(dependency) != "ok":
            raise AcceptanceError(
                f"timeout follow-up dependency is unavailable: {dependency}"
            )
    database = details.get("database")
    tasks = details.get("quiz_tasks")
    if not isinstance(database, dict) or database.get(
        "fingerprint_sha256"
    ) != expected_database_fingerprint:
        raise AcceptanceError("timeout follow-up API is bound to another database")
    if not isinstance(tasks, dict) or tasks.get("source") != "redis":
        raise AcceptanceError("timeout follow-up requires Redis Worker metrics")
    signals = tasks.get("signals")
    if not isinstance(signals, dict) or signals.get("ready") is not True:
        raise AcceptanceError("timeout follow-up Worker heartbeat is not ready")
    processors = tasks.get("processors")
    timeout_processor = (
        processors.get("quiz-exam-timeout") if isinstance(processors, dict) else None
    )
    if not isinstance(timeout_processor, dict):
        raise AcceptanceError("timeout follow-up has no exam Worker metrics")
    try:
        timeout_successes = int(timeout_processor["successes"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AcceptanceError("timeout follow-up exam Worker metrics are invalid") from exc
    return {
        "ready": True,
        "worker_metrics_source": "redis",
        "exam_timeout_successes": timeout_successes,
    }


async def async_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--database-url-file", type=Path)
    parser.add_argument(
        "--confirm-database", default=os.getenv("QF55_CONFIRM_DATABASE", "")
    )
    parser.add_argument("--api-base", default=os.getenv("QF55_API_BASE", ""))
    parser.add_argument("--other-user-token-file", type=Path)
    parser.add_argument(
        "--backup-reference", default=os.getenv("QF55_BACKUP_REFERENCE", "")
    )
    parser.add_argument("--backup-manifest", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)

    stage = "argument_validation"
    report_dir: Path | None = None
    try:
        if not args.execute:
            raise AcceptanceError(
                "timeout follow-up may trigger query settlement and requires --execute"
            )
        if not 0.5 <= args.timeout <= 120:
            raise AcceptanceError("HTTP timeout must be between 0.5 and 120 seconds")
        report_dir = _safe_report_directory(args.report_dir)
        api_base = validate_api_base(args.api_base)
        database_url = _read_secret(
            environment_name="QF55_DATABASE_URL", file_path=args.database_url_file
        )
        user_token = _read_secret(
            environment_name="QF55_OTHER_USER_TOKEN",
            file_path=args.other_user_token_file,
        )
        migration = static_check()
        heads = migration.get("heads")
        if not isinstance(heads, list) or len(heads) != 1:
            raise AcceptanceError("repository must have exactly one Alembic head")
        target = DatabaseTarget.from_url(database_url)
        validate_database_target(target, confirmed_database=args.confirm_database.strip())
        backup = validate_backup_reference(
            args.backup_reference.strip(), args.backup_manifest
        )
        if backup["alembic_revision"] != str(heads[0]):
            raise AcceptanceError("pre-run backup Alembic revision is not repository head")

        stage = "database_identity"
        database = check_database_identity(
            target,
            expected_head=str(heads[0]),
            expected_backup_fingerprint=backup["database_fingerprint"],
        )
        fingerprint = str(database["database_fingerprint_sha256"])
        checkpoint = load_timeout_checkpoint(
            args.source_report,
            confirmed_database=target.database,
            expected_database_fingerprint=fingerprint,
        )

        async with AcceptanceHttpClient(
            api_base, timeout_seconds=args.timeout
        ) as client:
            stage = "runtime_readiness"
            runtime_before = await _verify_runtime(
                client, expected_database_fingerprint=fingerprint
            )
            stage = "worker_settlement"
            worker = await asyncio.to_thread(
                check_worker_settled_exam,
                target,
                exam_id=int(checkpoint["exam_id"]),
            )
            stage = "http_and_statistics"
            report = await run_timeout_followup(
                client=client,
                user_token=user_token,
                checkpoint=checkpoint,
            )
            runtime_after = await _verify_runtime(
                client, expected_database_fingerprint=fingerprint
            )
        if int(runtime_after["exam_timeout_successes"]) < int(
            runtime_before["exam_timeout_successes"]
        ):
            raise AcceptanceError("exam Worker success counter moved backwards")
        report["preflight"] = {
            "database": database,
            "runtime": {
                "ready": True,
                "worker_metrics_source": "redis",
                "exam_timeout_successes_before": runtime_before[
                    "exam_timeout_successes"
                ],
                "exam_timeout_successes_after": runtime_after[
                    "exam_timeout_successes"
                ],
            },
            "worker": worker,
        }
        report_path = _new_report_path(report_dir, "timeout-followup")
        _write_json_atomic(report_path, report)
        print(
            "quiz_acceptance_timeout_followup=passed "
            f"report={report_path} exam_id={checkpoint['exam_id']}"
        )
        return 0
    except (AcceptanceError, PreflightError, RuntimeError) as exc:
        failure_path: Path | None = None
        if report_dir is not None:
            failure_path = _new_report_path(report_dir, "timeout-followup-failed")
            _write_json_atomic(
                failure_path,
                {
                    "schema_version": 1,
                    "status": "failed",
                    "started_at": _iso_now(),
                    "finished_at": _iso_now(),
                    "failure": {
                        "stage": stage,
                        "exception_type": type(exc).__name__[:128],
                    },
                },
            )
        suffix = f" report={failure_path}" if failure_path is not None else ""
        print(
            "quiz acceptance timeout follow-up failed: "
            f"stage={stage} type={type(exc).__name__}{suffix}",
            file=sys.stderr,
        )
        return 1


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
