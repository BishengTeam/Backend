"""Register immutable post-start evidence in the migrated production database."""

from __future__ import annotations

import hashlib
import json
import re
import stat
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import asyncpg

from bootstrap_app.runtime import DatabaseTarget
from bootstrap_app.state import BootstrapState


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40,64}$")
MAX_PROBE_BYTES = 1024 * 1024


class BootstrapAcceptanceError(RuntimeError):
    """Runtime evidence or the migrated acceptance target is inconsistent."""


@dataclass(frozen=True, slots=True)
class RuntimeAcceptanceEvidence:
    database_fingerprint_sha256: str
    summaries: dict[str, dict[str, Any]]


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _private_regular_bytes(path: Path, *, max_bytes: int) -> bytes:
    try:
        info = path.lstat()
    except OSError as exc:
        raise BootstrapAcceptanceError("release manifest is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise BootstrapAcceptanceError("release manifest path is unsafe")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise BootstrapAcceptanceError("release manifest permissions are unsafe")
    if info.st_size > max_bytes:
        raise BootstrapAcceptanceError("release manifest is too large")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise BootstrapAcceptanceError("release manifest cannot be read") from exc


def validate_release_manifest(
    control_dir: Path,
    state: BootstrapState,
) -> dict[str, Any]:
    manifest_bytes = _private_regular_bytes(
        control_dir / "release-manifest.json",
        max_bytes=1024 * 1024,
    )
    if not state.release_manifest_sha256 or not SHA256_RE.fullmatch(
        state.release_manifest_sha256
    ):
        raise BootstrapAcceptanceError("release manifest state is incomplete")
    if hashlib.sha256(manifest_bytes).hexdigest() != state.release_manifest_sha256:
        raise BootstrapAcceptanceError("release manifest checksum mismatch")
    try:
        manifest = json.loads(manifest_bytes)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BootstrapAcceptanceError("release manifest is invalid") from exc
    if not isinstance(manifest, dict) or manifest.get("installation_id") != state.installation_id:
        raise BootstrapAcceptanceError("release manifest installation mismatch")
    source = manifest.get("source")
    if not isinstance(source, dict):
        raise BootstrapAcceptanceError("release manifest source is invalid")
    for name, expected in (
        ("backend", state.backend_commit),
        ("admin", state.admin_commit),
    ):
        item = source.get(name)
        if (
            not isinstance(item, dict)
            or not isinstance(expected, str)
            or not COMMIT_RE.fullmatch(expected)
            or item.get("commit") != expected
        ):
            raise BootstrapAcceptanceError("release manifest source mismatch")
    return manifest


def _read_json_url(url: str, *, timeout: float = 10.0) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "application/json", "User-Agent": "wemini-bootstrap/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise BootstrapAcceptanceError("runtime probe returned a failure status")
            payload = response.read(MAX_PROBE_BYTES + 1)
    except BootstrapAcceptanceError:
        raise
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        raise BootstrapAcceptanceError("runtime probe failed") from exc
    if len(payload) > MAX_PROBE_BYTES:
        raise BootstrapAcceptanceError("runtime probe response is too large")
    try:
        decoded = json.loads(payload)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BootstrapAcceptanceError("runtime probe response is invalid") from exc
    if not isinstance(decoded, dict):
        raise BootstrapAcceptanceError("runtime probe response is invalid")
    return decoded


def _statuses(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise BootstrapAcceptanceError("runtime dependency checks are invalid")
    result: dict[str, str] = {}
    for key, status in value.items():
        if isinstance(key, str) and isinstance(status, str):
            result[key] = status
    return result


def collect_runtime_evidence(
    backend_base_url: str,
    *,
    reader: Callable[[str], dict[str, Any]] | None = None,
) -> RuntimeAcceptanceEvidence:
    base_url = backend_base_url.rstrip("/")
    if not base_url.startswith("http://"):
        raise BootstrapAcceptanceError("internal Backend probe URL must use http")
    read = reader or _read_json_url
    health = read(f"{base_url}/health")
    ready = read(f"{base_url}/ready")

    health_data = health.get("data")
    if (
        health.get("code") != 0
        or not isinstance(health_data, Mapping)
        or health_data.get("status") != "ok"
    ):
        raise BootstrapAcceptanceError("Backend health evidence did not pass")
    health_checks = _statuses(health_data.get("checks"))
    if any(health_checks.get(name) != "ok" for name in ("database", "redis")):
        raise BootstrapAcceptanceError("Backend health dependencies did not pass")

    if ready.get("status") != "ready":
        raise BootstrapAcceptanceError("Backend readiness evidence did not pass")
    ready_checks = _statuses(ready.get("checks"))
    details = ready.get("details")
    if not isinstance(details, Mapping):
        raise BootstrapAcceptanceError("Backend readiness details are invalid")
    required = [
        "database",
        "admin_identity",
        "redis",
        "quiz_worker",
        "wechat_login",
        "wechat_payment",
    ]
    disabled_optional: set[str] = set()
    for name in ("oss", "quiz_oss"):
        detail = details.get(name)
        if (
            isinstance(detail, Mapping)
            and detail.get("mode") == "disabled"
            and detail.get("configured") is False
        ):
            if ready_checks.get(name) != "not_configured":
                raise BootstrapAcceptanceError(
                    "disabled optional dependency state is invalid"
                )
            disabled_optional.add(name)
        else:
            required.append(name)
    if any(ready_checks.get(name) != "ok" for name in required):
        raise BootstrapAcceptanceError("Backend production dependencies did not pass")
    database = details.get("database")
    quiz_tasks = details.get("quiz_tasks")
    if not isinstance(database, Mapping):
        raise BootstrapAcceptanceError("database readiness identity is missing")
    fingerprint = database.get("fingerprint_sha256")
    if not isinstance(fingerprint, str) or not SHA256_RE.fullmatch(fingerprint):
        raise BootstrapAcceptanceError("database readiness identity is invalid")
    worker_summary: dict[str, Any] = {"status": ready_checks["quiz_worker"]}
    if isinstance(quiz_tasks, Mapping):
        source = quiz_tasks.get("source")
        heartbeat_at = quiz_tasks.get("heartbeat_at")
        if isinstance(source, str):
            worker_summary["source"] = source
        if isinstance(heartbeat_at, str):
            worker_summary["heartbeat_at"] = heartbeat_at

    summaries = {
        "runtime_health": {
            "checks": {name: health_checks[name] for name in ("database", "redis")}
        },
        "runtime_readiness": {
            "checks": {
                name: ready_checks[name]
                for name in (*required, *sorted(disabled_optional))
            },
            "database_fingerprint_sha256": fingerprint,
        },
        "worker_heartbeat": worker_summary,
    }
    if "oss" in disabled_optional:
        summaries["renshe_private_oss"] = {
            "status": "not_configured",
            "capability": "disabled",
            "verification": "runtime_configuration",
        }

    return RuntimeAcceptanceEvidence(
        database_fingerprint_sha256=fingerprint,
        summaries=summaries,
    )


def _evidence_digest(
    evidence_type: str,
    summary: Mapping[str, Any],
) -> str:
    return hashlib.sha256(
        _canonical_json(
            {
                "evidence_type": evidence_type,
                "result": "passed",
                "source": "bootstrap",
                "summary": summary,
            }
        )
    ).hexdigest()


async def register_installed_acceptance(
    *,
    installation_dir: Path,
    control_dir: Path,
    state: BootstrapState,
    runtime_evidence: RuntimeAcceptanceEvidence,
) -> int:
    validate_release_manifest(control_dir, state)
    if (
        not state.backend_commit
        or not state.admin_commit
        or not state.recovery_object_key
        or not state.recovery_sha256
        or not SHA256_RE.fullmatch(state.recovery_sha256)
    ):
        raise BootstrapAcceptanceError("bootstrap acceptance state is incomplete")
    recovery_summary = {
        "recovery_sha256": state.recovery_sha256,
        "recovery_object_key_sha256": hashlib.sha256(
            state.recovery_object_key.encode("utf-8")
        ).hexdigest(),
        "storage": (
            "local_only"
            if state.recovery_object_key.startswith("local-only:")
            else "recovery_oss"
        ),
        "release_manifest_sha256": state.release_manifest_sha256,
    }
    summaries = dict(runtime_evidence.summaries)
    summaries["recovery_bundle"] = recovery_summary
    target = DatabaseTarget.from_installation(installation_dir)
    connection = None
    try:
        connection = await asyncpg.connect(
            host=target.host,
            port=target.port,
            user=target.user,
            password=target.password,
            database=target.database,
            timeout=10,
            command_timeout=15,
        )
        async with connection.transaction():
            await connection.execute("SELECT pg_advisory_xact_lock($1)", 836_642_002)
            tables_ready = await connection.fetchval(
                "SELECT to_regclass('public.deployment_acceptance') IS NOT NULL "
                "AND to_regclass('public.deployment_acceptance_event') IS NOT NULL"
            )
            if not tables_ready:
                raise BootstrapAcceptanceError("deployment acceptance tables are not migrated")
            existing_other = await connection.fetchval(
                "SELECT installation_id FROM deployment_acceptance "
                "WHERE installation_id <> $1 LIMIT 1",
                state.installation_id,
            )
            if existing_other is not None:
                raise BootstrapAcceptanceError("database belongs to another installation")
            existing = await connection.fetchrow(
                """
                SELECT id, status, backend_commit, admin_commit,
                       release_manifest_sha256, recovery_object_key,
                       recovery_sha256, database_fingerprint_sha256
                FROM deployment_acceptance
                WHERE installation_id = $1
                FOR UPDATE
                """,
                state.installation_id,
            )
            identity = (
                state.backend_commit,
                state.admin_commit,
                state.release_manifest_sha256,
                state.recovery_object_key,
                state.recovery_sha256,
                runtime_evidence.database_fingerprint_sha256,
            )
            if existing is None:
                acceptance_id = await connection.fetchval(
                    """
                    INSERT INTO deployment_acceptance (
                        installation_id, status, backend_commit, admin_commit,
                        release_manifest_sha256, recovery_object_key,
                        recovery_sha256, database_fingerprint_sha256
                    ) VALUES ($1, 'installed_pending_uat', $2, $3, $4, $5, $6, $7)
                    RETURNING id
                    """,
                    state.installation_id,
                    *identity,
                )
            else:
                existing_identity = tuple(
                    existing[name]
                    for name in (
                        "backend_commit",
                        "admin_commit",
                        "release_manifest_sha256",
                        "recovery_object_key",
                        "recovery_sha256",
                        "database_fingerprint_sha256",
                    )
                )
                if existing_identity != identity:
                    raise BootstrapAcceptanceError("deployment acceptance identity mismatch")
                if existing["status"] not in {
                    "installed_pending_uat",
                    "production_accepted",
                }:
                    raise BootstrapAcceptanceError("deployment acceptance status is invalid")
                acceptance_id = existing["id"]
            if not isinstance(acceptance_id, int):
                raise BootstrapAcceptanceError("deployment acceptance registration failed")

            if existing is None or existing["status"] == "installed_pending_uat":
                for evidence_type, summary in summaries.items():
                    digest = _evidence_digest(evidence_type, summary)
                    await connection.execute(
                        """
                        INSERT INTO deployment_acceptance_event (
                            acceptance_id, event_type, evidence_type, result,
                            source, actor_admin_id, evidence_sha256, summary
                        ) VALUES (
                            $1, 'evidence_recorded', $2, 'passed',
                            'bootstrap', NULL, $3, $4::jsonb
                        )
                        ON CONFLICT (
                            acceptance_id, event_type, evidence_sha256
                        ) DO NOTHING
                        """,
                        acceptance_id,
                        evidence_type,
                        digest,
                        _canonical_json(summary).decode("utf-8"),
                    )
            return acceptance_id
    except BootstrapAcceptanceError:
        raise
    except (asyncpg.PostgresError, OSError, TimeoutError) as exc:
        raise BootstrapAcceptanceError("deployment acceptance database operation failed") from exc
    finally:
        if connection is not None:
            await connection.close()


async def read_database_acceptance_status(installation_dir: Path, installation_id: str) -> str:
    target = DatabaseTarget.from_installation(installation_dir)
    connection = None
    try:
        connection = await asyncpg.connect(
            host=target.host,
            port=target.port,
            user=target.user,
            password=target.password,
            database=target.database,
            timeout=10,
            command_timeout=15,
        )
        status = await connection.fetchval(
            "SELECT status FROM deployment_acceptance WHERE installation_id = $1",
            installation_id,
        )
        if status not in {"installed_pending_uat", "production_accepted"}:
            raise BootstrapAcceptanceError("deployment acceptance status is unavailable")
        return status
    except BootstrapAcceptanceError:
        raise
    except (asyncpg.PostgresError, OSError, TimeoutError) as exc:
        raise BootstrapAcceptanceError("deployment acceptance database operation failed") from exc
    finally:
        if connection is not None:
            await connection.close()
