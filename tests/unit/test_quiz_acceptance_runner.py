from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from scripts.quiz_acceptance_runner import (
    AcceptanceError,
    AcceptanceHttpClient,
    AcceptanceState,
    QuizAcceptanceRunner,
    build_failure_report,
    load_timeout_checkpoint,
    run_timeout_followup,
    sanitize_evidence,
    validate_backup_reference,
    verify_private_signed_download,
)


def test_evidence_allowlist_rejects_secrets_urls_and_question_content() -> None:
    assert sanitize_evidence(
        {
            "scenario": "QF55-TEST",
            "http_status": 200,
            "business_code": 0,
            "request_id": "req-safe",
            "report_rows": [1, 2, 4],
        }
    )["report_rows"] == [1, 2, 4]

    for key in ("token", "signed_url", "question_text", "correct_answer"):
        with pytest.raises(AcceptanceError, match="sensitive evidence field"):
            sanitize_evidence({key: "must-not-be-written"})
    with pytest.raises(AcceptanceError, match="not allow-listed"):
        sanitize_evidence({"arbitrary_field": "value"})


def test_backup_reference_requires_matching_verified_archive(tmp_path: Path) -> None:
    archive = tmp_path / "wemini-postgres-manual-20260812T000000Z-deadbeef.dump"
    archive.write_bytes(b"frozen backup")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest = archive.with_name(archive.name + ".manifest.json")
    manifest.write_text(
        json.dumps(
            {
                "status": "verified",
                "archive": archive.name,
                "sha256": digest,
                "alembic_revision": "plan003",
                "database_fingerprint": hashlib.sha256(
                    b"test-system-id/wemini_app_acceptance"
                ).hexdigest(),
            }
        ),
        encoding="utf-8",
    )

    report = validate_backup_reference(
        f"{archive.name}#sha256={digest}", manifest
    )
    assert report["alembic_revision"] == "plan003"
    assert report["database_fingerprint"] == hashlib.sha256(
        b"test-system-id/wemini_app_acceptance"
    ).hexdigest()

    with pytest.raises(AcceptanceError, match="does not match"):
        validate_backup_reference(
            f"{archive.name}#sha256={'0' * 64}", manifest
        )


@pytest.mark.asyncio
async def test_http_client_records_only_safe_metadata() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret-token"
        return httpx.Response(
            200,
            headers={"X-Request-ID": "req-runner-1"},
            json={
                "code": 0,
                "message": "ok",
                "data": {
                    "url": "https://oss.example.test/private?signature=secret",
                    "question_text": "must never enter evidence",
                },
            },
        )

    transport = httpx.MockTransport(handler)
    async with AcceptanceHttpClient(
        "https://uat.example.test",
        timeout_seconds=5,
        transport=transport,
    ) as client:
        payload, evidence = await client.request(
            "GET", "/admin/quiz/imports/1/source-url", token="secret-token"
        )

    assert payload["data"]["url"].startswith("https://oss.example.test")
    assert evidence == {
        "http_status": 200,
        "business_code": 0,
        "request_id": "req-runner-1",
    }
    serialized = json.dumps(evidence)
    assert "secret-token" not in serialized
    assert "question_text" not in serialized
    assert "oss.example" not in serialized


def test_failure_report_keeps_partial_safe_evidence_without_exception_text() -> None:
    runner = object.__new__(QuizAcceptanceRunner)
    runner.state = AcceptanceState(
        categories={"root": {"id": 7}},
        import_jobs=[11],
        evidence=[
            sanitize_evidence(
                {
                    "scenario": "QF55-TEST",
                    "status": "succeeded",
                    "request_id": "req-safe",
                }
            )
        ],
    )
    report = build_failure_report(
        started_at="2026-08-12T00:00:00+00:00",
        runner=runner,
        preflight=None,
        failure_stage="acceptance_run",
        exception=RuntimeError("Bearer secret and signed URL must not persist"),
    )

    assert report["status"] == "failed"
    assert report["category_count"] == 1
    assert report["import_job_ids"] == [11]
    assert report["failure"] == {
        "stage": "acceptance_run",
        "exception_type": "RuntimeError",
    }
    serialized = json.dumps(report)
    assert "Bearer secret" not in serialized
    assert "signed URL" not in serialized


def test_timeout_checkpoint_binds_source_report_to_database(tmp_path: Path) -> None:
    fingerprint = hashlib.sha256(b"system/acceptance").hexdigest()
    report_path = tmp_path / "qf55-acceptance.json"
    report_path.write_text(
        json.dumps(
            {
                "status": "partial_pass_pending_manual_uat",
                "timeout_checkpoint": {
                    "exam_id": 42,
                    "timed_out_exam_count_before": 3,
                },
                "preflight": {
                    "database": {
                        "database": "wemini_app_acceptance",
                        "database_fingerprint_sha256": fingerprint,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    checkpoint = load_timeout_checkpoint(
        report_path,
        confirmed_database="wemini_app_acceptance",
        expected_database_fingerprint=fingerprint,
    )
    assert checkpoint["exam_id"] == 42
    assert checkpoint["timed_out_exam_count_before"] == 3
    assert len(checkpoint["source_report_sha256"]) == 64

    with pytest.raises(AcceptanceError, match="another database"):
        load_timeout_checkpoint(
            report_path,
            confirmed_database="another_acceptance",
            expected_database_fingerprint=fingerprint,
        )


@pytest.mark.asyncio
async def test_timeout_followup_requires_one_settled_exam_and_saves_no_answers() -> None:
    now = datetime.now(timezone.utc)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/quiz/exams/42":
            return httpx.Response(
                200,
                headers={"X-Request-ID": "req-exam"},
                json={
                    "code": 0,
                    "data": {
                        "id": 42,
                        "status": "timed_out",
                        "question_count": 10,
                        "finished_at": now.isoformat(),
                        "score": "0.0",
                        "questions": [
                            {
                                "correct_answer": "A",
                                "user_answer": None,
                                "explanation": "must not enter evidence",
                            }
                            for _ in range(10)
                        ],
                    },
                },
            )
        assert request.url.path == "/api/quiz/stats"
        return httpx.Response(
            200,
            headers={"X-Request-ID": "req-stats"},
            json={"code": 0, "data": {"exam": {"timed_out_exam_count": 4}}},
        )

    async with AcceptanceHttpClient(
        "https://uat.example.test",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    ) as client:
        report = await run_timeout_followup(
            client=client,
            user_token="secret-token",
            checkpoint={
                "exam_id": 42,
                "timed_out_exam_count_before": 3,
                "source_report_sha256": "a" * 64,
            },
        )

    assert report["status"] == "passed"
    assert report["evidence"][0]["timed_out_exam_delta"] == 1
    serialized = json.dumps(report)
    assert "secret-token" not in serialized
    assert "correct_answer" not in serialized
    assert "must not enter evidence" not in serialized


@pytest.mark.asyncio
async def test_private_signed_download_checks_anonymous_success_and_expiry() -> None:
    expired = False

    async def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.query:
            return httpx.Response(403, content=b"private")
        if expired:
            return httpx.Response(403, content=b"expired")
        return httpx.Response(200, content=b"frozen source")

    async def sleeper(_seconds: float) -> None:
        nonlocal expired
        expired = True

    async with AcceptanceHttpClient(
        "https://uat.example.test",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    ) as client:
        evidence = await verify_private_signed_download(
            client,
            signed_url="https://private-oss.example.test/source.json?signature=secret",
            expires_at=datetime.now(timezone.utc),
            expected_size=len(b"frozen source"),
            sleeper=sleeper,
        )

    assert evidence == {
        "anonymous_download_http_status": 403,
        "signed_download_http_status": 200,
        "expired_download_http_status": 403,
        "downloaded_bytes": len(b"frozen source"),
        "signed_link_expiry_verified": True,
    }
    assert "signature" not in json.dumps(evidence)
