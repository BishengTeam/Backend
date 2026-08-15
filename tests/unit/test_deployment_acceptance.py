from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.deployment_acceptance import DEPLOYMENT_EVIDENCE_TYPES
from app.schemas.deployment_acceptance import DeploymentAcceptanceSignRequest
from app.services.deployment_acceptance import (
    DeploymentAcceptanceService,
    evidence_digest,
    normalize_evidence_summary,
)
from bootstrap_app.acceptance import (
    BootstrapAcceptanceError,
    collect_runtime_evidence,
    validate_release_manifest,
)
from bootstrap_app.state import BootstrapPhase, BootstrapStateStore


def _runtime_documents() -> dict[str, dict]:
    fingerprint = "d" * 64
    checks = {
        "database": "ok",
        "admin_identity": "ok",
        "redis": "ok",
        "oss": "ok",
        "quiz_oss": "ok",
        "quiz_worker": "ok",
        "wechat_login": "ok",
        "wechat_payment": "ok",
    }
    return {
        "/health": {
            "code": 0,
            "data": {
                "status": "ok",
                "checks": {"database": "ok", "redis": "ok"},
            },
        },
        "/ready": {
            "status": "ready",
            "checks": checks,
            "details": {
                "database": {"status": "ok", "fingerprint_sha256": fingerprint},
                "admin_identity": {"status": "ok"},
                "quiz_tasks": {
                    "source": "redis",
                    "heartbeat_at": "2026-08-14T08:00:00+00:00",
                },
            },
        },
    }


def test_runtime_evidence_requires_all_production_checks_and_database_identity():
    documents = _runtime_documents()

    def reader(url: str) -> dict:
        return documents["/ready" if url.endswith("/ready") else "/health"]

    result = collect_runtime_evidence("http://app:8000", reader=reader)
    assert result.database_fingerprint_sha256 == "d" * 64
    assert set(result.summaries) == {
        "runtime_health",
        "runtime_readiness",
        "worker_heartbeat",
    }
    assert result.summaries["worker_heartbeat"]["source"] == "redis"

    documents["/ready"]["checks"]["quiz_worker"] = "unavailable"
    with pytest.raises(BootstrapAcceptanceError, match="dependencies"):
        collect_runtime_evidence("http://app:8000", reader=reader)


def test_runtime_evidence_records_explicitly_disabled_optional_oss() -> None:
    documents = _runtime_documents()
    for name in ("oss", "quiz_oss"):
        documents["/ready"]["checks"][name] = "not_configured"
        documents["/ready"]["details"][name] = {
            "status": "not_configured",
            "configured": False,
            "required": False,
            "mode": "disabled",
            "reason": "feature_not_configured",
        }

    def reader(url: str) -> dict:
        return documents["/ready" if url.endswith("/ready") else "/health"]

    result = collect_runtime_evidence("http://app:8000", reader=reader)

    assert result.summaries["runtime_readiness"]["checks"]["oss"] == (
        "not_configured"
    )
    assert result.summaries["renshe_private_oss"] == {
        "status": "not_configured",
        "capability": "disabled",
        "verification": "runtime_configuration",
    }


def test_release_manifest_is_bound_to_signed_state(tmp_path: Path):
    os.chmod(tmp_path, 0o700)
    control = tmp_path / "control"
    control.mkdir(mode=0o700)
    store = BootstrapStateStore(control, b"t" * 64)
    state = store.initialize()
    state = store.transition(state.phase, BootstrapPhase.CONFIGURED)
    state = store.transition(state.phase, BootstrapPhase.QUALITY_RUNNING)
    manifest = {
        "installation_id": state.installation_id,
        "source": {
            "backend": {"commit": "a" * 40},
            "admin": {"commit": "b" * 40},
        },
    }
    manifest_bytes = (
        json.dumps(manifest, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode()
    manifest_path = control / "release-manifest.json"
    manifest_path.write_bytes(manifest_bytes)
    manifest_path.chmod(0o600)
    state = store.transition(
        state.phase,
        BootstrapPhase.QUALITY_PASSED,
        backend_commit="a" * 40,
        admin_commit="b" * 40,
        release_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
    )

    assert validate_release_manifest(control, state) == manifest
    manifest_path.write_bytes(manifest_bytes + b" ")
    with pytest.raises(BootstrapAcceptanceError, match="checksum"):
        validate_release_manifest(control, state)


def test_evidence_summary_redacts_secrets_and_digest_is_stable():
    summary = {
        "status": "ok",
        "token": "must-not-survive",
        "nested": {"phone": "13800138000"},
    }
    normalized = normalize_evidence_summary(summary)
    assert normalized["token"] == "[REDACTED]"
    assert normalized["nested"]["phone"] == "[REDACTED]"
    first = evidence_digest(
        evidence_type="runtime_health",
        result="passed",
        source="system",
        summary=summary,
    )
    second = evidence_digest(
        evidence_type="runtime_health",
        result="passed",
        source="system",
        summary=summary,
    )
    assert first == second
    assert len(first) == 64


def test_response_is_fail_closed_until_every_latest_evidence_passes():
    now = datetime.now(timezone.utc)
    acceptance = SimpleNamespace(
        installation_id="1" * 32,
        status="installed_pending_uat",
        backend_commit="a" * 40,
        admin_commit="b" * 40,
        release_manifest_sha256="c" * 64,
        recovery_object_key="recovery/object",
        recovery_sha256="d" * 64,
        database_fingerprint_sha256="e" * 64,
        accepted_by_admin_id=None,
        accepted_at=None,
        evidence_summary_sha256=None,
        created_at=now,
        updated_at=now,
    )

    def event(identifier: int, evidence_type: str, result: str):
        return SimpleNamespace(
            id=identifier,
            evidence_type=evidence_type,
            result=result,
            source="system",
            created_at=now,
            evidence_sha256=f"{identifier:064x}",
            summary={"status": result},
        )

    events = [
        event(index, evidence_type, "passed")
        for index, evidence_type in enumerate(DEPLOYMENT_EVIDENCE_TYPES, start=1)
    ]
    events.append(event(99, "wechat_refund", "failed"))
    response = DeploymentAcceptanceService._response(acceptance, events)
    assert response.can_accept is False
    assert response.missing_evidence == ["wechat_refund"]

    events.append(event(100, "wechat_refund", "passed"))
    response = DeploymentAcceptanceService._response(acceptance, events)
    assert response.can_accept is True
    assert response.missing_evidence == []


def test_sign_request_requires_literal_confirmation_and_sha256():
    request = DeploymentAcceptanceSignRequest(
        confirmation="PRODUCTION_ACCEPTED",
        release_manifest_sha256="a" * 64,
    )
    assert request.confirmation == "PRODUCTION_ACCEPTED"
    with pytest.raises(ValueError):
        DeploymentAcceptanceSignRequest(
            confirmation="PRODUCTION_ACCEPTED",
            release_manifest_sha256="not-a-digest",
        )


def test_admin_contract_has_no_generic_evidence_write_route():
    from app.main import app

    paths = app.openapi()["paths"]
    assert "/admin/deployment-acceptance" in paths
    assert "/admin/deployment-acceptance/accept" in paths
    assert not any(
        method in path_item
        for path, path_item in paths.items()
        if path.startswith("/admin/deployment-acceptance/evidence")
        for method in ("post", "put", "patch", "delete")
    )


def test_migration_is_linear_and_database_enforces_append_only_events():
    source = Path(
        "alembic/versions/deploy001_add_deployment_acceptance.py"
    ).read_text(encoding="utf-8")
    assert 'down_revision: str | Sequence[str] | None = "quiz007"' in source
    assert "BEFORE UPDATE OR DELETE ON deployment_acceptance_event" in source
    assert "invalid deployment acceptance transition" in source
