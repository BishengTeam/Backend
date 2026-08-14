"""Public Admin contract for deployment and production-UAT acceptance."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


DeploymentAcceptanceStatus = Literal[
    "installed_pending_uat",
    "production_accepted",
]

DeploymentEvidenceType = Literal[
    "runtime_health",
    "runtime_readiness",
    "worker_heartbeat",
    "wechat_login",
    "uat_scope",
    "renshe_private_oss",
    "wechat_payment",
    "wechat_refund",
    "recovery_bundle",
    "backup_restore_config",
]


class DeploymentEvidenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    evidence_type: DeploymentEvidenceType
    label: str
    meaning: str
    passed: bool
    latest_result: Literal["passed", "failed"] | None = None
    source: str | None = None
    recorded_at: datetime | None = None
    evidence_sha256: str | None = None
    summary: dict[str, object] | None = None


class DeploymentAcceptanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    installation_id: str
    status: DeploymentAcceptanceStatus
    status_meaning: str
    backend_commit: str
    admin_commit: str
    release_manifest_sha256: str
    recovery_object_key: str
    recovery_sha256: str
    database_fingerprint_sha256: str
    evidence: list[DeploymentEvidenceResponse]
    missing_evidence: list[DeploymentEvidenceType]
    can_accept: bool
    accepted_by_admin_id: int | None = None
    accepted_at: datetime | None = None
    evidence_summary_sha256: str | None = None
    created_at: datetime
    updated_at: datetime


class DeploymentAcceptanceSignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation: Literal["PRODUCTION_ACCEPTED"]
    release_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
