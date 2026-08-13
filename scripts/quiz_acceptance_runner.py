#!/usr/bin/env python3
"""Run the deterministic, write-capable portion of the QF-55 quiz UAT.

The runner is intentionally fail-closed.  It validates the frozen fixture,
requires an explicit disposable database name and a verified pre-run backup
reference, then invokes the read-only QF-55 preflight before issuing any HTTP
write.  It never connects to PostgreSQL itself and never prints bearer tokens,
signed URLs, question text, answer bodies or object keys.

Cleanup remains an operator action: restore the verified pre-run database
snapshot and delete the isolated OSS prefix after collecting evidence.  The
runner does not offer a broad database or OSS delete command.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import urlsplit, urlunsplit

import httpx


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.check_migrations import static_check  # noqa: E402
from scripts.postgres_backup import (  # noqa: E402
    DatabaseTarget,
    _load_manifest as load_backup_manifest,
    _sha256 as sha256_file,
)
from scripts.quiz_acceptance_fixtures import (  # noqa: E402
    FIXTURE_PREFIX,
    check as check_fixtures,
)
from scripts.quiz_acceptance_preflight import (  # noqa: E402
    PreflightError,
    _load_contract_manifest,
    _read_secret,
    check_database,
    check_http_environment,
    validate_api_base,
    validate_database_target,
)


TERMINAL_IMPORT_STATUSES = {
    "validation_failed",
    "awaiting_category_confirmation",
    "succeeded",
    "failed",
    "cancelled",
    "expired",
}
BACKUP_REFERENCE_RE = re.compile(
    r"^(?P<archive>wemini-postgres-[^/#]+\.dump)#sha256=(?P<digest>[0-9a-f]{64})$"
)
SAFE_EVIDENCE_KEYS = {
    "actual_count",
    "admin",
    "alembic_head",
    "anonymous_download_http_status",
    "attempt_count",
    "audit_count",
    "business_code",
    "calculated_at",
    "category_count",
    "category_id",
    "checkin_days_after",
    "checkin_days_before",
    "completed_exam_delta",
    "created_count",
    "database",
    "database_fingerprint_sha256",
    "depth",
    "duration_seconds",
    "downloaded_bytes",
    "error_count",
    "errors",
    "exam_count_delta",
    "exam_id",
    "expected_error_count",
    "fixture_rows_before_run",
    "fixture_sha256",
    "fixture_version",
    "http_status",
    "import_job_id",
    "lock_version",
    "normal_admin",
    "practice_attempt_delta",
    "practice_first_attempt_delta",
    "question_count",
    "question_id",
    "quiz_operation_count",
    "quiz_oss",
    "quiz_table_count",
    "ready",
    "removed_operation_count",
    "report_error_count",
    "report_rows",
    "requested_count",
    "request_id",
    "retry_count",
    "scenario",
    "session_id",
    "source_type",
    "source_report_sha256",
    "status",
    "super_admin",
    "signed_link_ttl_seconds",
    "signed_download_http_status",
    "signed_link_expiry_verified",
    "timed_out_exam_delta",
    "timed_out_exam_count_after",
    "timed_out_exam_count_before",
    "total_rows",
    "updated_count",
    "user_accounts",
    "validated_rows",
    "worker_metrics_source",
    "storage_retention_seconds",
    "expired_download_http_status",
}
FORBIDDEN_EVIDENCE_FRAGMENTS = (
    "answer",
    "authorization",
    "content",
    "cookie",
    "credential",
    "explanation",
    "object_key",
    "openid",
    "option",
    "password",
    "question_text",
    "signed_url",
    "source_url",
    "token",
    "url",
)


class AcceptanceError(RuntimeError):
    """Safe-to-print UAT failure without credential or question content."""


@dataclass(frozen=True, slots=True)
class AcceptanceSecrets:
    database_url: str
    admin_token: str
    super_admin_token: str
    disabled_admin_token: str
    user_token: str
    other_user_token: str


@dataclass(frozen=True, slots=True)
class AcceptanceConfig:
    fixture_dir: Path
    api_base: str
    confirm_database: str
    backup_reference: str
    backup_manifest: Path
    report_dir: Path
    timeout_seconds: float = 30.0
    import_timeout_seconds: float = 3600.0
    poll_seconds: float = 2.0
    run_large_imports: bool = True
    run_user_flows: bool = True


@dataclass(slots=True)
class AcceptanceState:
    categories: dict[str, dict[str, Any]] = field(default_factory=dict)
    import_jobs: list[int] = field(default_factory=list)
    workflow_question_ids: dict[str, list[int]] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    timeout_checkpoint: "TimeoutCheckpoint | None" = None


@dataclass(frozen=True, slots=True)
class TimeoutCheckpoint:
    exam_id: int
    timed_out_exam_count_before: int


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AcceptanceError(f"cannot read acceptance artifact: {path.name}") from exc
    if not isinstance(value, dict):
        raise AcceptanceError(f"acceptance artifact is not a JSON object: {path.name}")
    return value


def validate_backup_reference(reference: str, manifest_path: Path) -> dict[str, Any]:
    """Verify the operator-supplied pre-run backup without opening the DB."""

    match = BACKUP_REFERENCE_RE.fullmatch(reference.strip())
    if match is None:
        raise AcceptanceError("backup reference must be archive.dump#sha256=<64 hex>")
    manifest_path = manifest_path.expanduser().resolve()
    manifest = load_backup_manifest(manifest_path)
    archive_name = match.group("archive")
    digest = match.group("digest")
    if manifest.get("archive") != archive_name or manifest.get("sha256") != digest:
        raise AcceptanceError("backup reference does not match its verified manifest")
    database_fingerprint = str(manifest.get("database_fingerprint") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", database_fingerprint):
        raise AcceptanceError("backup manifest has no valid database fingerprint")
    archive = manifest_path.parent / archive_name
    if not archive.is_file() or archive.is_symlink():
        raise AcceptanceError("verified pre-run backup archive is missing")
    if sha256_file(archive) != digest:
        raise AcceptanceError("verified pre-run backup checksum does not match")
    return {
        "archive": archive_name,
        "sha256": digest,
        "alembic_revision": str(manifest.get("alembic_revision") or ""),
        "database_fingerprint": database_fingerprint,
    }


def _sanitize_evidence_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:256]
    if isinstance(value, list):
        return [_sanitize_evidence_value(item) for item in value[:5000]]
    if isinstance(value, dict):
        return sanitize_evidence(value)
    raise AcceptanceError("evidence contains an unsupported value type")


def sanitize_evidence(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Allow-list evidence fields and reject secret/content-shaped keys."""

    result: dict[str, Any] = {}
    for raw_key, value in payload.items():
        key = str(raw_key)
        lowered = key.lower()
        if any(fragment in lowered for fragment in FORBIDDEN_EVIDENCE_FRAGMENTS):
            raise AcceptanceError(f"refusing sensitive evidence field: {key}")
        if key not in SAFE_EVIDENCE_KEYS:
            raise AcceptanceError(f"evidence field is not allow-listed: {key}")
        result[key] = _sanitize_evidence_value(value)
    return result


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    partial = path.with_name(f".{path.name}.{uuid.uuid4().hex}.partial")
    try:
        partial.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        partial.chmod(0o600)
        partial.replace(path)
    finally:
        if partial.exists():
            partial.unlink()


def _new_report_path(report_dir: Path, label: str) -> Path:
    timestamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
    return report_dir / f"qf55-{label}-{timestamp}-{uuid.uuid4().hex[:8]}.json"


def _preflight_report(preflight: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "fixture_version": preflight["fixture_version"],
        "fixture_sha256": preflight["fixture_sha256"],
        "database": preflight["database"],
        "http": preflight["http"],
    }


def build_failure_report(
    *,
    started_at: str,
    runner: "QuizAcceptanceRunner | None",
    preflight: Mapping[str, Any] | None,
    failure_stage: str,
    exception: BaseException,
) -> dict[str, Any]:
    """Build a bounded partial report without persisting exception text."""

    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "failed",
        "started_at": started_at,
        "finished_at": _iso_now(),
        "failure": {
            "stage": failure_stage,
            "exception_type": type(exception).__name__[:128],
        },
        "category_count": 0,
        "import_job_ids": [],
        "evidence": [],
        "cleanup_required": True,
    }
    if runner is not None:
        report["category_count"] = len(runner.state.categories)
        report["import_job_ids"] = list(runner.state.import_jobs)
        report["evidence"] = list(runner.state.evidence)
        if runner.state.timeout_checkpoint is not None:
            report["timeout_checkpoint"] = {
                "exam_id": runner.state.timeout_checkpoint.exam_id,
                "timed_out_exam_count_before": (
                    runner.state.timeout_checkpoint.timed_out_exam_count_before
                ),
            }
    if preflight is not None:
        report["preflight"] = _preflight_report(preflight)
    return report


def load_timeout_checkpoint(
    report_path: Path,
    *,
    confirmed_database: str,
    expected_database_fingerprint: str,
) -> dict[str, Any]:
    """Load only the bounded fields needed for the real-clock follow-up."""

    path = report_path.expanduser().resolve()
    if not path.is_file() or path.is_symlink():
        raise AcceptanceError("acceptance source report must be a real file")
    report = _read_json(path)
    if report.get("status") != "partial_pass_pending_manual_uat":
        raise AcceptanceError("acceptance source report is not eligible for follow-up")
    checkpoint = report.get("timeout_checkpoint")
    preflight = report.get("preflight")
    database = preflight.get("database") if isinstance(preflight, dict) else None
    if not isinstance(checkpoint, dict) or not isinstance(database, dict):
        raise AcceptanceError("acceptance source report has no timeout checkpoint")
    try:
        exam_id = int(checkpoint["exam_id"])
        count_before = int(checkpoint["timed_out_exam_count_before"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AcceptanceError("acceptance timeout checkpoint is invalid") from exc
    if exam_id < 1 or count_before < 0:
        raise AcceptanceError("acceptance timeout checkpoint is invalid")
    if database.get("database") != confirmed_database:
        raise AcceptanceError("acceptance source report belongs to another database")
    if database.get("database_fingerprint_sha256") != expected_database_fingerprint:
        raise AcceptanceError("acceptance source report database fingerprint differs")
    return {
        "exam_id": exam_id,
        "timed_out_exam_count_before": count_before,
        "source_report_sha256": sha256_file(path),
    }


async def run_timeout_followup(
    *,
    client: AcceptanceHttpClient,
    user_token: str,
    checkpoint: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify the pending exam after its real 60-minute server deadline."""

    started_at = _iso_now()
    exam_id = int(checkpoint["exam_id"])
    payload, detail_http = await client.request(
        "GET",
        f"/api/quiz/exams/{exam_id}",
        token=user_token,
        request_id=f"qf55-timeout-followup-{exam_id}",
    )
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("status") != "timed_out":
        raise AcceptanceError("pending exam has not settled as timed_out")
    if data.get("score") is None or data.get("finished_at") is None:
        raise AcceptanceError("timed-out exam has no settled result")
    questions = data.get("questions")
    if not isinstance(questions, list) or len(questions) != int(
        data.get("question_count") or 0
    ):
        raise AcceptanceError("timed-out exam result is incomplete")

    stats_payload, stats_http = await client.request(
        "GET",
        "/api/quiz/stats",
        token=user_token,
        request_id=f"qf55-timeout-followup-stats-{exam_id}",
    )
    exam_stats = (stats_payload.get("data") or {}).get("exam") or {}
    try:
        count_after = int(exam_stats["timed_out_exam_count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AcceptanceError("timeout follow-up statistics response is invalid") from exc
    count_before = int(checkpoint["timed_out_exam_count_before"])
    if count_after - count_before != 1:
        raise AcceptanceError("timed-out exam statistics did not increase exactly once")
    return {
        "schema_version": 1,
        "status": "passed",
        "started_at": started_at,
        "finished_at": _iso_now(),
        "source_report_sha256": str(checkpoint["source_report_sha256"]),
        "evidence": [
            sanitize_evidence(
                {
                    "scenario": "QF55-EXAM-REAL-CLOCK-TIMEOUT",
                    "exam_id": exam_id,
                    "status": "timed_out",
                    "timed_out_exam_count_before": count_before,
                    "timed_out_exam_count_after": count_after,
                    "timed_out_exam_delta": 1,
                    **detail_http,
                }
            ),
            sanitize_evidence(
                {
                    "scenario": "QF55-EXAM-REAL-CLOCK-TIMEOUT",
                    "exam_id": exam_id,
                    "status": "statistics_consistent",
                    **stats_http,
                }
            ),
        ],
    }


def _safe_report_directory(raw: Path) -> Path:
    path = raw.expanduser().resolve()
    forbidden = {Path("/").resolve(), Path.home().resolve(), ROOT.resolve()}
    if path in forbidden:
        raise AcceptanceError("refusing broad acceptance report directory")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir() or path.is_symlink():
        raise AcceptanceError("acceptance report directory must be a real directory")
    return path


class AcceptanceHttpClient:
    """Small strict API client that never returns headers or URLs as evidence."""

    def __init__(
        self,
        api_base: str,
        *,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=api_base,
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            transport=transport,
            headers={"Accept": "application/json", "User-Agent": "qf55-runner/1"},
        )

    async def __aenter__(self) -> "AcceptanceHttpClient":
        await self._client.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._client.__aexit__(exc_type, exc, tb)

    async def request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        json_body: object | None = None,
        data: Mapping[str, str] | None = None,
        files: Mapping[str, tuple[str, bytes, str]] | None = None,
        expected_statuses: tuple[int, ...] = (200,),
        expected_codes: tuple[int, ...] = (0,),
        request_id: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if request_id:
            headers["X-Request-ID"] = request_id
        try:
            response = await self._client.request(
                method,
                path,
                headers=headers,
                json=json_body,
                data=data,
                files=files,
            )
        except httpx.HTTPError as exc:
            raise AcceptanceError(
                f"HTTP request failed: {method.upper()} {path} ({type(exc).__name__})"
            ) from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise AcceptanceError(
                f"HTTP response is not JSON: {method.upper()} {path}"
            ) from exc
        if not isinstance(payload, dict):
            raise AcceptanceError(f"HTTP response is not an object: {method.upper()} {path}")
        code = payload.get("code")
        if response.status_code not in expected_statuses or code not in expected_codes:
            raise AcceptanceError(
                f"unexpected HTTP result: {method.upper()} {path} "
                f"status={response.status_code} code={code}"
            )
        observed_request_id = response.headers.get("X-Request-ID") or request_id
        evidence = sanitize_evidence(
            {
                "http_status": response.status_code,
                "business_code": int(code) if isinstance(code, int) else -1,
                "request_id": observed_request_id,
            }
        )
        return payload, evidence

    async def download_bytes(
        self,
        absolute_url: str,
        *,
        expected_statuses: tuple[int, ...],
        max_bytes: int = 10 * 1024 * 1024,
    ) -> tuple[bytes, int]:
        """Fetch an OSS object without ever echoing its signed URL."""

        parsed = urlsplit(absolute_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise AcceptanceError("signed import object URL is invalid")
        try:
            async with self._client.stream("GET", absolute_url) as response:
                status = response.status_code
                if status not in expected_statuses:
                    raise AcceptanceError(
                        f"import object returned unexpected HTTP status: {status}"
                    )
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > max_bytes:
                        raise AcceptanceError("import object response exceeds 10 MiB")
        except AcceptanceError:
            raise
        except httpx.HTTPError as exc:
            raise AcceptanceError(
                f"import object request failed ({type(exc).__name__})"
            ) from exc
        return bytes(content), status


async def verify_private_signed_download(
    client: AcceptanceHttpClient,
    *,
    signed_url: str,
    expires_at: datetime,
    expected_size: int,
    sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[str, Any]:
    """Verify private denial, signed download and expiration in one process."""

    if expires_at.tzinfo is None:
        raise AcceptanceError("signed import object expiry must include timezone")
    parsed = urlsplit(signed_url)
    unsigned_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    _anonymous_body, anonymous_status = await client.download_bytes(
        unsigned_url,
        expected_statuses=(400, 401, 403, 404),
    )
    content, signed_status = await client.download_bytes(
        signed_url,
        expected_statuses=(200,),
    )
    if expected_size < 1 or len(content) != expected_size:
        raise AcceptanceError("signed import source size differs from its job metadata")
    wait_seconds = (
        expires_at.astimezone(timezone.utc) - _utc_now()
    ).total_seconds() + 2.0
    if not 0 < wait_seconds <= 305:
        raise AcceptanceError("signed import object expiry wait is outside the safe bound")
    await sleeper(wait_seconds)
    _expired_body, expired_status = await client.download_bytes(
        signed_url,
        expected_statuses=(400, 401, 403, 404),
    )
    return {
        "anonymous_download_http_status": anonymous_status,
        "signed_download_http_status": signed_status,
        "expired_download_http_status": expired_status,
        "downloaded_bytes": len(content),
        "signed_link_expiry_verified": True,
    }


async def _run_read_only_preflight(
    config: AcceptanceConfig,
    secrets: AcceptanceSecrets,
) -> dict[str, Any]:
    fixture_manifest = check_fixtures(config.fixture_dir)
    migration = static_check()
    heads = migration.get("heads")
    if not isinstance(heads, list) or len(heads) != 1:
        raise AcceptanceError("repository must have exactly one Alembic head")
    target = DatabaseTarget.from_url(secrets.database_url)
    validate_database_target(target, confirmed_database=config.confirm_database)
    backup = validate_backup_reference(config.backup_reference, config.backup_manifest)
    if backup["alembic_revision"] != str(heads[0]):
        raise AcceptanceError("pre-run backup Alembic revision is not the repository head")
    database = check_database(
        target,
        expected_head=str(heads[0]),
        expected_backup_fingerprint=backup["database_fingerprint"],
    )
    http = await asyncio.to_thread(
        check_http_environment,
        config.api_base,
        confirmed_database=config.confirm_database,
        confirmed_database_fingerprint=str(database["database_fingerprint_sha256"]),
        admin_token=secrets.admin_token,
        super_admin_token=secrets.super_admin_token,
        disabled_admin_token=secrets.disabled_admin_token,
        user_token=secrets.user_token,
        other_user_token=secrets.other_user_token,
        contract_manifest=_load_contract_manifest(),
        timeout=min(config.timeout_seconds, 30.0),
    )
    return {
        "fixture_version": fixture_manifest["fixture_version"],
        "fixture_sha256": fixture_manifest["definition_sha256"],
        "database": database,
        "http": http,
    }


class QuizAcceptanceRunner:
    def __init__(
        self,
        config: AcceptanceConfig,
        secrets: AcceptanceSecrets,
        client: AcceptanceHttpClient,
        *,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.config = config
        self.secrets = secrets
        self.client = client
        self.sleeper = sleeper
        self.state = AcceptanceState()

    def _record(self, scenario: str, **payload: Any) -> None:
        event = {"scenario": scenario, **payload}
        self.state.evidence.append(sanitize_evidence(event))

    async def _request(self, *args, **kwargs):
        return await self.client.request(*args, **kwargs)

    async def run_permission_checks(self) -> None:
        _payload, http = await self._request(
            "GET",
            "/admin/quiz/categories",
            expected_statuses=(401,),
            expected_codes=(40100,),
            request_id="qf55-permission-admin-anonymous",
        )
        self._record("QF55-PERMISSIONS", status="anonymous_admin_rejected", **http)
        _payload, http = await self._request(
            "GET",
            "/api/quiz/questions",
            expected_statuses=(401,),
            expected_codes=(40100,),
            request_id="qf55-permission-user-anonymous",
        )
        self._record("QF55-PERMISSIONS", status="anonymous_questions_rejected", **http)
        _payload, http = await self._request(
            "GET",
            "/admin/quiz/categories",
            token=self.secrets.super_admin_token,
            request_id="qf55-permission-super-admin",
        )
        self._record("QF55-PERMISSIONS", status="super_admin_allowed", **http)
        _payload, http = await self._request(
            "GET",
            "/admin/quiz/categories",
            token=self.secrets.disabled_admin_token,
            expected_statuses=(401,),
            expected_codes=(40100,),
            request_id="qf55-permission-disabled-admin",
        )
        self._record("QF55-PERMISSIONS", status="disabled_admin_rejected", **http)
        _payload, http = await self._request(
            "GET",
            "/admin/quiz/categories",
            token=self.secrets.user_token,
            expected_statuses=(401,),
            expected_codes=(40100,),
            request_id="qf55-permission-user-token-on-admin",
        )
        self._record("QF55-PERMISSIONS", status="user_token_rejected", **http)

    async def create_categories(self) -> None:
        source = _read_json(self.config.fixture_dir / "categories.json")
        items = source.get("categories")
        if not isinstance(items, list) or len(items) != 7:
            raise AcceptanceError("categories fixture must contain seven categories")
        for item in items:
            if not isinstance(item, dict):
                raise AcceptanceError("category fixture row is invalid")
            parent = self.state.categories.get(str(item.get("parent_ref")))
            body = {
                "name": item["name"],
                "parent_id": parent["id"] if parent else None,
                "sort_order": item["sort_order"],
            }
            payload, http = await self._request(
                "POST",
                "/admin/quiz/categories",
                token=self.secrets.admin_token,
                json_body=body,
                request_id=f"qf55-category-{item['ref']}",
            )
            data = payload.get("data")
            if not isinstance(data, dict) or data.get("depth") not in {1, 2, 3}:
                raise AcceptanceError("category creation response is invalid")
            self.state.categories[str(item["ref"])] = data
            self._record(
                "QF55-CATEGORY-THREE-LEVELS",
                category_id=int(data["id"]),
                depth=int(data["depth"]),
                lock_version=int(data["lock_version"]),
                **http,
            )

        for ref in ("import_leaf", "practice_leaf", "exam_leaf"):
            if self.state.categories[ref]["depth"] != 3:
                raise AcceptanceError(f"fixture category is not at depth three: {ref}")
        await self._category_negative_checks()
        disabled = self.state.categories["disabled_leaf"]
        payload, http = await self._request(
            "POST",
            f"/admin/quiz/categories/{disabled['id']}/status",
            token=self.secrets.admin_token,
            json_body={"status": "disabled", "lock_version": disabled["lock_version"]},
            request_id="qf55-category-disable",
        )
        data = payload.get("data")
        if not isinstance(data, dict) or data.get("status") != "disabled":
            raise AcceptanceError("fixture disabled category did not transition")
        self.state.categories["disabled_leaf"] = data
        self._record(
            "QF55-CATEGORY-THREE-LEVELS",
            category_id=int(data["id"]),
            status="disabled",
            lock_version=int(data["lock_version"]),
            **http,
        )

    async def _category_negative_checks(self) -> None:
        leaf = self.state.categories["import_leaf"]
        root = self.state.categories["root"]
        group = self.state.categories["import_group"]
        for suffix, body in (
            ("self", {"lock_version": root["lock_version"], "parent_id": root["id"]}),
            ("cycle", {"lock_version": root["lock_version"], "parent_id": leaf["id"]}),
        ):
            _payload, http = await self._request(
                "PUT",
                f"/admin/quiz/categories/{root['id']}",
                token=self.secrets.admin_token,
                json_body=body,
                expected_statuses=(422,),
                expected_codes=(40200,),
                request_id=f"qf55-category-reject-{suffix}",
            )
            self._record(
                "QF55-CATEGORY-THREE-LEVELS",
                category_id=int(root["id"]),
                status="rejected",
                **http,
            )
        _payload, http = await self._request(
            "POST",
            "/admin/quiz/categories",
            token=self.secrets.admin_token,
            json_body={"name": f"{FIXTURE_PREFIX}-FOURTH-LEVEL", "parent_id": leaf["id"]},
            expected_statuses=(422,),
            expected_codes=(40200,),
            request_id="qf55-category-reject-fourth",
        )
        self._record(
            "QF55-CATEGORY-THREE-LEVELS",
            category_id=int(group["id"]),
            status="rejected",
            **http,
        )

    async def _create_json_import(
        self,
        artifact_name: str,
        *,
        scenario: str,
    ) -> dict[str, Any]:
        body = _read_json(self.config.fixture_dir / artifact_name)
        payload, http = await self._request(
            "POST",
            "/admin/quiz/imports/json",
            token=self.secrets.admin_token,
            json_body=body,
            request_id=f"qf55-import-{uuid.uuid4().hex[:16]}",
        )
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("id"), int):
            raise AcceptanceError("JSON import creation response is invalid")
        self.state.import_jobs.append(int(data["id"]))
        self._record(
            scenario,
            import_job_id=int(data["id"]),
            source_type="json",
            status=str(data.get("status")),
            **http,
        )
        return await self._wait_import_job(int(data["id"]), scenario=scenario)

    async def _create_csv_import(
        self,
        artifact_name: str,
        *,
        scenario: str,
    ) -> dict[str, Any]:
        content = (self.config.fixture_dir / artifact_name).read_bytes()
        payload, http = await self._request(
            "POST",
            "/admin/quiz/imports/csv",
            token=self.secrets.admin_token,
            data={"filename": artifact_name, "size_bytes": str(len(content))},
            files={"file": (artifact_name, content, "text/csv")},
            request_id=f"qf55-import-{uuid.uuid4().hex[:16]}",
        )
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("id"), int):
            raise AcceptanceError("CSV import creation response is invalid")
        self.state.import_jobs.append(int(data["id"]))
        self._record(
            scenario,
            import_job_id=int(data["id"]),
            source_type="csv",
            status=str(data.get("status")),
            **http,
        )
        return await self._wait_import_job(int(data["id"]), scenario=scenario)

    async def _wait_import_job(self, job_id: int, *, scenario: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.config.import_timeout_seconds
        while True:
            payload, http = await self._request(
                "GET",
                f"/admin/quiz/imports/{job_id}",
                token=self.secrets.admin_token,
                request_id=f"qf55-import-poll-{job_id}",
            )
            data = payload.get("data")
            if not isinstance(data, dict):
                raise AcceptanceError("import job response is invalid")
            status = str(data.get("status"))
            if status in TERMINAL_IMPORT_STATUSES:
                retention_seconds = self._import_retention_seconds(data)
                self._record(
                    scenario,
                    import_job_id=job_id,
                    source_type=str(data.get("source_type")),
                    status=status,
                    total_rows=int(data.get("total_rows") or 0),
                    validated_rows=int(data.get("validated_rows") or 0),
                    created_count=int(data.get("created_count") or 0),
                    error_count=int(data.get("error_count") or 0),
                    retry_count=int(data.get("retry_count") or 0),
                    storage_retention_seconds=retention_seconds,
                    **http,
                )
                return data
            if time.monotonic() >= deadline:
                raise AcceptanceError(f"import job did not finish before timeout: {job_id}")
            await self.sleeper(self.config.poll_seconds)

    @staticmethod
    def _import_retention_seconds(job: Mapping[str, Any]) -> int:
        try:
            finished = datetime.fromisoformat(
                str(job["finished_at"]).replace("Z", "+00:00")
            )
            expires = datetime.fromisoformat(
                str(job["expires_at"]).replace("Z", "+00:00")
            )
        except (KeyError, ValueError) as exc:
            raise AcceptanceError("terminal import retention timestamps are invalid") from exc
        if finished.tzinfo is None or expires.tzinfo is None:
            raise AcceptanceError("terminal import retention timestamps must include timezone")
        seconds = int(
            (
                expires.astimezone(timezone.utc)
                - finished.astimezone(timezone.utc)
            ).total_seconds()
        )
        if seconds != 7 * 24 * 60 * 60:
            raise AcceptanceError("terminal import retention is not exactly seven days")
        return seconds

    @staticmethod
    def _assert_import(
        job: Mapping[str, Any],
        *,
        status: str,
        total_rows: int,
        created_count: int,
    ) -> None:
        if job.get("status") != status:
            raise AcceptanceError(f"import job ended in unexpected status: {job.get('status')}")
        if int(job.get("total_rows") or 0) != total_rows:
            raise AcceptanceError("import job total_rows does not match the fixture")
        if int(job.get("created_count") or 0) != created_count:
            raise AcceptanceError("import job created_count violates atomicity")
        if status == "succeeded" and int(job.get("validated_rows") or 0) != total_rows:
            raise AcceptanceError("successful import did not validate every row")

    async def import_and_publish_workflow(self) -> None:
        job = await self._create_json_import(
            "workflow-questions.json",
            scenario="QF55-WORKFLOW-QUESTIONS",
        )
        self._assert_import(job, status="succeeded", total_rows=20, created_count=20)
        questions: list[dict[str, Any]] = []
        for marker in (f"{FIXTURE_PREFIX}-PRACTICE-", f"{FIXTURE_PREFIX}-EXAM-"):
            payload, _http = await self._request(
                "GET",
                f"/admin/quiz/questions?keyword={marker}&status=draft&page=1&page_size=100",
                token=self.secrets.admin_token,
            )
            data = payload.get("data")
            items = data.get("items") if isinstance(data, dict) else None
            if not isinstance(items, list) or len(items) != 10:
                raise AcceptanceError("workflow import did not create exactly ten draft questions per flow")
            questions.extend(item for item in items if isinstance(item, dict))
        if len(questions) != 20:
            raise AcceptanceError("workflow draft question count is not 20")
        batch_items = [
            {"question_id": int(item["id"]), "lock_version": int(item["lock_version"])}
            for item in questions
        ]
        payload, http = await self._request(
            "POST",
            "/admin/quiz/questions/batch-publish",
            token=self.secrets.admin_token,
            json_body={"items": batch_items},
            request_id="qf55-workflow-publish",
        )
        data = payload.get("data")
        if not isinstance(data, dict) or data.get("succeeded") is not True or data.get("updated_count") != 20:
            raise AcceptanceError("workflow question batch publish failed")
        self.state.workflow_question_ids = {
            "practice": [
                int(item["id"])
                for item in questions
                if int(item["category_id"]) == int(self.state.categories["practice_leaf"]["id"])
            ],
            "exam": [
                int(item["id"])
                for item in questions
                if int(item["category_id"]) == int(self.state.categories["exam_leaf"]["id"])
            ],
        }
        if any(len(ids) != 10 for ids in self.state.workflow_question_ids.values()):
            raise AcceptanceError("published workflow questions are assigned to the wrong category")
        self._record(
            "QF55-WORKFLOW-QUESTIONS",
            updated_count=20,
            status="published",
            **http,
        )

    async def run_large_imports(self) -> None:
        json_success = await self._create_json_import(
            "import-success-5000.json",
            scenario="QF55-IMPORT-JSON-5000",
        )
        self._assert_import(json_success, status="succeeded", total_rows=5000, created_count=5000)
        await self._verify_source_url_and_audit(json_success)
        csv_success = await self._create_csv_import(
            "import-success-5000.csv",
            scenario="QF55-IMPORT-CSV-5000",
        )
        self._assert_import(csv_success, status="succeeded", total_rows=5000, created_count=5000)

        invalid_jobs = (
            await self._create_json_import(
                "import-validation-errors.json",
                scenario="QF55-IMPORT-ATOMIC-ERRORS",
            ),
            await self._create_csv_import(
                "import-validation-errors.csv",
                scenario="QF55-IMPORT-ATOMIC-ERRORS",
            ),
        )
        expected = ((3, {1, 2, 4}), (4, {2, 3, 4, 6}))
        for job, (expected_errors, expected_rows) in zip(
            invalid_jobs, expected, strict=True
        ):
            total_rows = int(job.get("total_rows") or 0)
            self._assert_import(
                job,
                status="validation_failed",
                total_rows=total_rows,
                created_count=0,
            )
            if int(job.get("error_count") or 0) != expected_errors:
                raise AcceptanceError("validation report error count does not match frozen fixture")
            await self._verify_error_report(
                int(job["id"]),
                expected_error_count=expected_errors,
                expected_rows=expected_rows,
            )
            _payload, http = await self._request(
                "POST",
                f"/admin/quiz/imports/{job['id']}/retry",
                token=self.secrets.admin_token,
                expected_statuses=(422,),
                expected_codes=(40200,),
                request_id=f"qf55-import-retry-rejected-{job['id']}",
            )
            self._record(
                "QF55-IMPORT-ATOMIC-ERRORS",
                import_job_id=int(job["id"]),
                status="retry_rejected",
                **http,
            )

    async def _verify_source_url_and_audit(self, job: Mapping[str, Any]) -> None:
        job_id = int(job["id"])
        request_id = f"qf55-import-source-{job_id}"
        payload, http = await self._request(
            "GET",
            f"/admin/quiz/imports/{job_id}/source-url",
            token=self.secrets.admin_token,
            request_id=request_id,
        )
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("url"), str):
            raise AcceptanceError("import source signed URL response is invalid")
        try:
            expires_at = datetime.fromisoformat(str(data["expires_at"]).replace("Z", "+00:00"))
        except (KeyError, ValueError) as exc:
            raise AcceptanceError("import source signed URL expiry is invalid") from exc
        if expires_at.tzinfo is None:
            raise AcceptanceError("import source signed URL expiry must include timezone")
        ttl_seconds = int((expires_at.astimezone(timezone.utc) - _utc_now()).total_seconds())
        if not 1 <= ttl_seconds <= 300:
            raise AcceptanceError("import source signed URL lifetime exceeds 300 seconds")
        download_evidence = await verify_private_signed_download(
            self.client,
            signed_url=str(data["url"]),
            expires_at=expires_at,
            expected_size=int(job.get("source_size_bytes") or 0),
            sleeper=self.sleeper,
        )
        audit_payload, _ = await self._request(
            "GET",
            f"/admin/quiz/audit-logs?request_id={request_id}&page=1&page_size=20",
            token=self.secrets.admin_token,
        )
        audit_data = audit_payload.get("data")
        audit_items = audit_data.get("items") if isinstance(audit_data, dict) else None
        if not isinstance(audit_items, list) or not any(
            isinstance(item, dict)
            and item.get("action") == "import.source_download_url"
            and item.get("object_id") == job_id
            and item.get("result") == "succeeded"
            for item in audit_items
        ):
            raise AcceptanceError("import source signed URL audit row is missing")
        self._record(
            "QF55-OSS-SEVEN-DAY-LIFECYCLE",
            import_job_id=job_id,
            signed_link_ttl_seconds=ttl_seconds,
            audit_count=len(audit_items),
            status="source_url_audited",
            **download_evidence,
            **http,
        )

    async def _verify_error_report(
        self,
        job_id: int,
        *,
        expected_error_count: int,
        expected_rows: set[int],
    ) -> None:
        payload, http = await self._request(
            "GET",
            f"/admin/quiz/imports/{job_id}/report-url",
            token=self.secrets.admin_token,
            request_id=f"qf55-import-report-{job_id}",
        )
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("url"), str):
            raise AcceptanceError("validation report signed URL response is invalid")
        signed_url = str(data["url"])
        try:
            response = await self.client._client.get(signed_url)
            report = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AcceptanceError("validation report download failed") from exc
        if response.status_code != 200 or not isinstance(report, dict):
            raise AcceptanceError("validation report download returned an invalid response")
        errors = report.get("errors")
        if not isinstance(errors, list) or len(errors) != expected_error_count:
            raise AcceptanceError("validation report does not contain every row-level error")
        rows = sorted(
            int(item["row"])
            for item in errors
            if isinstance(item, dict) and isinstance(item.get("row"), int)
        )
        if set(rows) != expected_rows:
            raise AcceptanceError("validation report rows do not match frozen fixture")
        self._record(
            "QF55-IMPORT-ATOMIC-ERRORS",
            import_job_id=job_id,
            report_error_count=len(errors),
            report_rows=rows,
            expected_error_count=expected_error_count,
            **http,
        )

    @staticmethod
    def _wrong_answer(question: Mapping[str, Any]) -> str | list[str]:
        question_type = question.get("question_type")
        if question_type == "multiple_choice":
            return ["B", "D"]
        return "B"

    @staticmethod
    def _correct_workflow_answer(question: Mapping[str, Any]) -> str | list[str]:
        question_type = question.get("question_type")
        if question_type == "multiple_choice":
            return ["A", "C"]
        if question_type == "judge":
            return "B"
        return "A"

    async def _abandon_current_practice_if_any(self) -> None:
        payload, _ = await self._request(
            "GET", "/api/quiz/practice-sessions/current", token=self.secrets.user_token
        )
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("id"), int):
            await self._request(
                "POST",
                f"/api/quiz/practice-sessions/{data['id']}/abandon",
                token=self.secrets.user_token,
                request_id="qf55-practice-preexisting-abandon",
            )

    async def run_practice_flow(self) -> None:
        await self._abandon_current_practice_if_any()
        before_payload, _ = await self._request(
            "GET", "/api/quiz/stats", token=self.secrets.user_token
        )
        before = before_payload.get("data") or {}
        before_practice = before.get("practice") if isinstance(before, dict) else {}

        payload, http = await self._request(
            "POST",
            "/api/quiz/practice-sessions",
            token=self.secrets.user_token,
            json_body={
                "mode": "normal",
                "category_id": self.state.categories["practice_leaf"]["id"],
                "question_count": 10,
            },
            request_id="qf55-practice-create",
        )
        session = payload.get("data")
        questions = session.get("questions") if isinstance(session, dict) else None
        if not isinstance(questions, list) or len(questions) != 10:
            raise AcceptanceError("practice session did not contain ten questions")
        target = questions[0]
        session_id = int(session["id"])
        target_id = int(target["id"])
        self._record(
            "QF55-PRACTICE-WRONG-CLEAR",
            session_id=session_id,
            requested_count=10,
            actual_count=len(questions),
            status=str(session["status"]),
            **http,
        )

        await self._submit_practice(
            session_id,
            int(target["session_question_id"]),
            self._wrong_answer(target),
            key="qf55-wrong-0001",
        )
        await self._submit_practice(
            session_id,
            int(target["session_question_id"]),
            self._correct_workflow_answer(target),
            key="qf55-right-0002",
        )
        wrong_payload, _ = await self._request(
            "GET", "/api/quiz/wrong-book?page=1&page_size=100", token=self.secrets.user_token
        )
        wrong_data = wrong_payload.get("data")
        wrong_items = wrong_data.get("items") if isinstance(wrong_data, dict) else None
        if not isinstance(wrong_items, list) or target_id not in {
            int(item.get("question_id")) for item in wrong_items if isinstance(item, dict)
        }:
            raise AcceptanceError("same-session correct answer incorrectly cleared the wrong item")

        for index, question in enumerate(questions[1:], start=3):
            await self._submit_practice(
                session_id,
                int(question["session_question_id"]),
                self._correct_workflow_answer(question),
                key=f"qf55-practice-{index:04d}",
            )
        completed_payload, _ = await self._request(
            "GET",
            f"/api/quiz/practice-sessions/{session_id}",
            token=self.secrets.user_token,
        )
        completed = completed_payload.get("data")
        if not isinstance(completed, dict) or completed.get("status") != "completed":
            raise AcceptanceError("practice session did not auto-complete")

        next_payload, _ = await self._request(
            "POST",
            "/api/quiz/practice-sessions",
            token=self.secrets.user_token,
            json_body={"mode": "wrong"},
            request_id="qf55-wrong-practice-create",
        )
        next_session = next_payload.get("data")
        next_questions = next_session.get("questions") if isinstance(next_session, dict) else None
        if not isinstance(next_questions, list):
            raise AcceptanceError("wrong-practice session response is invalid")
        if len(next_questions) != 1:
            raise AcceptanceError("wrong-practice session did not contain exactly one active wrong item")
        target_snapshot = next(
            (item for item in next_questions if int(item.get("id")) == target_id),
            None,
        )
        if target_snapshot is None:
            raise AcceptanceError("active wrong item was not selected in the next session")
        await self._submit_practice(
            int(next_session["id"]),
            int(target_snapshot["session_question_id"]),
            self._correct_workflow_answer(target_snapshot),
            key="qf55-clear-0001",
        )
        cleared_payload, _ = await self._request(
            "GET", "/api/quiz/wrong-book?page=1&page_size=100", token=self.secrets.user_token
        )
        cleared_data = cleared_payload.get("data")
        cleared_items = cleared_data.get("items") if isinstance(cleared_data, dict) else None
        if not isinstance(cleared_items, list) or target_id in {
            int(item.get("question_id")) for item in cleared_items if isinstance(item, dict)
        }:
            raise AcceptanceError("later-session first correct answer did not clear the wrong item")

        after_payload, _ = await self._request(
            "GET", "/api/quiz/stats", token=self.secrets.user_token
        )
        checkin_payload, _ = await self._request(
            "GET", "/api/quiz/checkin", token=self.secrets.user_token
        )
        after = after_payload.get("data") or {}
        after_practice = after.get("practice") if isinstance(after, dict) else {}
        checkin = checkin_payload.get("data") or {}
        if checkin.get("checked_in") is not True:
            raise AcceptanceError("ordinary practice did not create today's check-in")
        attempt_delta = int(after_practice.get("total_attempts") or 0) - int(
            (before_practice or {}).get("total_attempts") or 0
        )
        first_attempt_delta = int(after_practice.get("first_attempts") or 0) - int(
            (before_practice or {}).get("first_attempts") or 0
        )
        if attempt_delta != 12 or first_attempt_delta != 11:
            raise AcceptanceError("practice statistics did not update immediately")
        history_payload, _ = await self._request(
            "GET",
            f"/api/quiz/practice-history?category_id={self.state.categories['practice_leaf']['id']}"
            "&page=1&page_size=100",
            token=self.secrets.user_token,
        )
        history_data = history_payload.get("data")
        if not isinstance(history_data, dict) or int(history_data.get("total") or 0) != 12:
            raise AcceptanceError("practice history did not retain every attempt snapshot")
        self._record(
            "QF55-PRACTICE-WRONG-CLEAR",
            session_id=session_id,
            question_id=target_id,
            status="completed_and_cleared",
            practice_attempt_delta=attempt_delta,
            practice_first_attempt_delta=first_attempt_delta,
            checkin_days_before=int((before_practice or {}).get("checkin_days") or 0),
            checkin_days_after=int(after_practice.get("checkin_days") or 0),
        )

    async def _submit_practice(
        self,
        session_id: int,
        session_question_id: int,
        answer: str | list[str],
        *,
        key: str,
    ) -> None:
        payload, _ = await self._request(
            "POST",
            f"/api/quiz/practice-sessions/{session_id}/attempts",
            token=self.secrets.user_token,
            json_body={
                "session_question_id": session_question_id,
                "idempotency_key": key,
                "user_answer": answer,
            },
        )
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("attempt_id"), int):
            raise AcceptanceError("practice attempt response is invalid")

    async def _abandon_current_exam(self, token: str, label: str) -> None:
        payload, _ = await self._request(
            "GET", "/api/quiz/exams/current", token=token
        )
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("id"), int):
            await self._request(
                "POST",
                f"/api/quiz/exams/{data['id']}/abandon",
                token=token,
                request_id=f"qf55-{label}-preexisting-exam-abandon",
            )

    async def _create_exam(self, token: str, label: str) -> dict[str, Any]:
        payload, http = await self._request(
            "POST",
            "/api/quiz/exams",
            token=token,
            json_body={
                "category_id": self.state.categories["exam_leaf"]["id"],
                "question_count": 10,
            },
            request_id=f"qf55-{label}-exam-create",
        )
        data = payload.get("data")
        if not isinstance(data, dict) or data.get("status") != "in_progress":
            raise AcceptanceError("exam creation response is invalid")
        questions = data.get("questions")
        if not isinstance(questions, list) or len(questions) != 10:
            raise AcceptanceError("exam did not contain ten questions")
        self._record(
            "QF55-EXAM-FOUR-STATES-AND-DISCONNECT",
            exam_id=int(data["id"]),
            status="in_progress",
            question_count=len(questions),
            duration_seconds=int(data["duration_seconds"]),
            **http,
        )
        return data

    async def run_exam_flow(self) -> None:
        await self._abandon_current_exam(self.secrets.user_token, "primary")
        await self._abandon_current_exam(self.secrets.other_user_token, "secondary")
        before_payload, _ = await self._request(
            "GET", "/api/quiz/stats", token=self.secrets.user_token
        )
        before_exam = (before_payload.get("data") or {}).get("exam") or {}

        resumable = await self._create_exam(self.secrets.user_token, "resume")
        first = resumable["questions"][0]
        saved_payload, _ = await self._request(
            "PUT",
            f"/api/quiz/exams/{resumable['id']}/answers/{first['exam_question_id']}",
            token=self.secrets.user_token,
            json_body={
                "user_answer": self._correct_workflow_answer(first),
                "lock_version": 0,
            },
            request_id="qf55-exam-answer-save",
        )
        saved = saved_payload.get("data") or {}
        await self._request(
            "PUT",
            f"/api/quiz/exams/{resumable['id']}/answers/{first['exam_question_id']}",
            token=self.secrets.user_token,
            json_body={
                "user_answer": self._wrong_answer(first),
                "lock_version": 0,
            },
            expected_statuses=(409,),
            expected_codes=(40201,),
            request_id="qf55-exam-answer-stale",
        )
        current_payload, _ = await self._request(
            "GET", "/api/quiz/exams/current", token=self.secrets.user_token
        )
        current = current_payload.get("data")
        if not isinstance(current, dict) or current.get("id") != resumable.get("id"):
            raise AcceptanceError("fresh client did not resume the active exam")
        original_order = [int(item["exam_question_id"]) for item in resumable["questions"]]
        resumed_order = [int(item["exam_question_id"]) for item in current["questions"]]
        if original_order != resumed_order:
            raise AcceptanceError("resumed exam question order changed")
        resumed_first = current["questions"][0]
        if int(resumed_first.get("answer_lock_version") or 0) != int(saved.get("lock_version") or 0):
            raise AcceptanceError("resumed exam did not retain the latest answer version")
        if "correct_answer" in resumed_first or "explanation" in resumed_first:
            raise AcceptanceError("in-progress exam leaked answer or explanation")

        submitted_payload, http = await self._request(
            "POST",
            f"/api/quiz/exams/{resumable['id']}/submit",
            token=self.secrets.user_token,
            request_id="qf55-exam-submit",
        )
        submitted = submitted_payload.get("data") or {}
        if submitted.get("status") != "completed":
            raise AcceptanceError("exam did not settle as completed")
        duplicate_payload, _ = await self._request(
            "POST",
            f"/api/quiz/exams/{resumable['id']}/submit",
            token=self.secrets.user_token,
            request_id="qf55-exam-submit-duplicate",
        )
        duplicate = duplicate_payload.get("data") or {}
        if duplicate.get("status") != "completed" or duplicate.get("score") != submitted.get("score"):
            raise AcceptanceError("duplicate exam submission is not idempotent")
        self._record(
            "QF55-EXAM-FOUR-STATES-AND-DISCONNECT",
            exam_id=int(resumable["id"]),
            status="completed",
            **http,
        )

        abandoned_exam = await self._create_exam(self.secrets.user_token, "abandon")
        abandoned_question = abandoned_exam["questions"][0]
        await self._request(
            "PUT",
            f"/api/quiz/exams/{abandoned_exam['id']}/answers/"
            f"{abandoned_question['exam_question_id']}",
            token=self.secrets.user_token,
            json_body={
                "user_answer": self._correct_workflow_answer(abandoned_question),
                "lock_version": 0,
            },
            request_id="qf55-exam-abandon-answer-save",
        )
        abandoned_payload, http = await self._request(
            "POST",
            f"/api/quiz/exams/{abandoned_exam['id']}/abandon",
            token=self.secrets.user_token,
            request_id="qf55-exam-abandon",
        )
        abandoned = abandoned_payload.get("data") or {}
        if abandoned.get("status") != "abandoned" or abandoned.get("score") is not None:
            raise AcceptanceError("abandoned exam lifecycle response is invalid")
        detail_payload, _ = await self._request(
            "GET",
            f"/api/quiz/exams/{abandoned_exam['id']}",
            token=self.secrets.user_token,
        )
        detail = detail_payload.get("data") or {}
        answered_count = sum(
            1
            for question in detail.get("questions") or []
            if isinstance(question, dict) and question.get("answered") is True
        )
        if answered_count != 1:
            raise AcceptanceError("abandoned exam did not retain the saved-answer marker")
        for question in detail.get("questions") or []:
            if any(key in question for key in ("user_answer", "correct_answer", "explanation")):
                raise AcceptanceError("abandoned exam leaked answer data")
        self._record(
            "QF55-EXAM-FOUR-STATES-AND-DISCONNECT",
            exam_id=int(abandoned_exam["id"]),
            status="abandoned",
            **http,
        )

        pending = await self._create_exam(self.secrets.other_user_token, "timeout-pending")
        after_payload, _ = await self._request(
            "GET", "/api/quiz/stats", token=self.secrets.user_token
        )
        after_exam = (after_payload.get("data") or {}).get("exam") or {}
        completed_delta = int(after_exam.get("completed_exam_count") or 0) - int(
            before_exam.get("completed_exam_count") or 0
        )
        timed_out_delta = int(after_exam.get("timed_out_exam_count") or 0) - int(
            before_exam.get("timed_out_exam_count") or 0
        )
        if completed_delta != 1 or timed_out_delta != 0:
            raise AcceptanceError("completed or abandoned exam statistics are inconsistent")
        self._record(
            "QF55-EXAM-FOUR-STATES-AND-DISCONNECT",
            exam_id=int(pending["id"]),
            status="timed_out_pending_real_clock",
            completed_exam_delta=completed_delta,
            timed_out_exam_delta=timed_out_delta,
        )
        other_stats_payload, _ = await self._request(
            "GET", "/api/quiz/stats", token=self.secrets.other_user_token
        )
        other_exam_stats = (other_stats_payload.get("data") or {}).get("exam") or {}
        self.state.timeout_checkpoint = TimeoutCheckpoint(
            exam_id=int(pending["id"]),
            timed_out_exam_count_before=int(
                other_exam_stats.get("timed_out_exam_count") or 0
            ),
        )

    async def run(self) -> dict[str, Any]:
        started_at = _iso_now()
        await self.run_permission_checks()
        await self.create_categories()
        await self.import_and_publish_workflow()
        if self.config.run_large_imports:
            await self.run_large_imports()
        if self.config.run_user_flows:
            await self.run_practice_flow()
            await self.run_exam_flow()
        report = {
            "schema_version": 1,
            "status": "partial_pass_pending_manual_uat",
            "fixture_prefix": FIXTURE_PREFIX,
            "started_at": started_at,
            "finished_at": _iso_now(),
            "category_count": len(self.state.categories),
            "import_job_ids": list(self.state.import_jobs),
            "evidence": self.state.evidence,
            "manual_remaining": [
                "timed_out exam after the real 60-minute deadline with independent Worker",
                "two-Worker claim/restart contention",
                "OSS seven-day cleanup failure and retry",
                "permissionless administrator denial (not representable by the frozen two-role model)",
                "Admin and Platform real-backend E2E",
                "restore the verified pre-run database snapshot and delete the isolated OSS prefix",
            ],
        }
        if self.state.timeout_checkpoint is not None:
            report["timeout_checkpoint"] = {
                "exam_id": self.state.timeout_checkpoint.exam_id,
                "timed_out_exam_count_before": (
                    self.state.timeout_checkpoint.timed_out_exam_count_before
                ),
            }
        return report


def _load_secrets(args: argparse.Namespace) -> AcceptanceSecrets:
    return AcceptanceSecrets(
        database_url=_read_secret(
            environment_name="QF55_DATABASE_URL", file_path=args.database_url_file
        ),
        admin_token=_read_secret(
            environment_name="QF55_ADMIN_TOKEN", file_path=args.admin_token_file
        ),
        super_admin_token=_read_secret(
            environment_name="QF55_SUPER_ADMIN_TOKEN",
            file_path=args.super_admin_token_file,
        ),
        disabled_admin_token=_read_secret(
            environment_name="QF55_DISABLED_ADMIN_TOKEN",
            file_path=args.disabled_admin_token_file,
        ),
        user_token=_read_secret(
            environment_name="QF55_USER_TOKEN", file_path=args.user_token_file
        ),
        other_user_token=_read_secret(
            environment_name="QF55_OTHER_USER_TOKEN",
            file_path=args.other_user_token_file,
        ),
    )


async def async_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-dir", type=Path, required=True)
    parser.add_argument("--database-url-file", type=Path)
    parser.add_argument(
        "--confirm-database", default=os.getenv("QF55_CONFIRM_DATABASE", "")
    )
    parser.add_argument("--api-base", default=os.getenv("QF55_API_BASE", ""))
    parser.add_argument("--admin-token-file", type=Path)
    parser.add_argument("--super-admin-token-file", type=Path)
    parser.add_argument("--disabled-admin-token-file", type=Path)
    parser.add_argument("--user-token-file", type=Path)
    parser.add_argument("--other-user-token-file", type=Path)
    parser.add_argument(
        "--backup-reference", default=os.getenv("QF55_BACKUP_REFERENCE", "")
    )
    parser.add_argument("--backup-manifest", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--import-timeout", type=float, default=3600.0)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--skip-large-imports", action="store_true")
    parser.add_argument("--skip-user-flows", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)

    started_at = _iso_now()
    failure_stage = "argument_validation"
    report_dir: Path | None = None
    preflight: dict[str, Any] | None = None
    runner: QuizAcceptanceRunner | None = None
    try:
        if not args.execute:
            raise AcceptanceError("write-capable acceptance requires the explicit --execute flag")
        if not 0.5 <= args.timeout <= 120:
            raise AcceptanceError("HTTP timeout must be between 0.5 and 120 seconds")
        if not 30 <= args.import_timeout <= 24 * 60 * 60:
            raise AcceptanceError("import timeout must be between 30 seconds and 24 hours")
        if not 0.1 <= args.poll_seconds <= 30:
            raise AcceptanceError("poll interval must be between 0.1 and 30 seconds")
        report_dir = _safe_report_directory(args.report_dir)
        config = AcceptanceConfig(
            fixture_dir=args.fixture_dir.expanduser().resolve(),
            api_base=validate_api_base(args.api_base),
            confirm_database=args.confirm_database.strip(),
            backup_reference=args.backup_reference.strip(),
            backup_manifest=args.backup_manifest,
            report_dir=report_dir,
            timeout_seconds=args.timeout,
            import_timeout_seconds=args.import_timeout,
            poll_seconds=args.poll_seconds,
            run_large_imports=not args.skip_large_imports,
            run_user_flows=not args.skip_user_flows,
        )
        secrets = _load_secrets(args)
        failure_stage = "read_only_preflight"
        preflight = await _run_read_only_preflight(config, secrets)
        failure_stage = "acceptance_run"
        async with AcceptanceHttpClient(
            config.api_base, timeout_seconds=config.timeout_seconds
        ) as client:
            runner = QuizAcceptanceRunner(config, secrets, client)
            report = await runner.run()
        report["preflight"] = _preflight_report(preflight)
        report_path = _new_report_path(config.report_dir, "acceptance")
        _write_json_atomic(report_path, report)
        print(
            "quiz_acceptance_runner=partial_pass_pending_manual_uat "
            f"report={report_path} categories={report['category_count']} "
            f"imports={len(report['import_job_ids'])}"
        )
        return 0
    except (AcceptanceError, PreflightError, RuntimeError) as exc:
        failure_path: Path | None = None
        if report_dir is not None:
            failure_report = build_failure_report(
                started_at=started_at,
                runner=runner,
                preflight=preflight,
                failure_stage=failure_stage,
                exception=exc,
            )
            failure_path = _new_report_path(report_dir, "acceptance-failed")
            _write_json_atomic(failure_path, failure_report)
        safe_message = (
            str(exc)
            if isinstance(exc, (AcceptanceError, PreflightError))
            else type(exc).__name__
        )
        suffix = f" report={failure_path}" if failure_path is not None else ""
        print(f"quiz acceptance runner failed: {safe_message}{suffix}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
