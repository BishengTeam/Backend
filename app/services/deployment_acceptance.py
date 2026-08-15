"""Fail-closed deployment/UAT evidence aggregation and production signing."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.adapter.database import get_db_ctx
from app.models.deployment_acceptance import (
    DEPLOYMENT_EVIDENCE_RESULTS,
    DEPLOYMENT_EVIDENCE_TYPES,
    DeploymentAcceptance,
    DeploymentAcceptanceEvent,
)
from app.port.exceptions import ConflictException, NotFoundException
from app.schemas.deployment_acceptance import (
    DeploymentAcceptanceResponse,
    DeploymentAcceptanceSignRequest,
    DeploymentEvidenceResponse,
    DeploymentEvidenceType,
)
from app.utils.audit import sanitize_audit_summary


STATUS_MEANINGS = {
    "installed_pending_uat": "Backend 与 Admin 已安装，尚未完成全部真实环境验收",
    "production_accepted": "全部必需证据通过，超级管理员已签署生产验收",
}

EVIDENCE_DEFINITIONS: dict[str, tuple[str, str]] = {
    "runtime_health": ("运行健康", "Backend /health、数据库与 Redis 健康"),
    "runtime_readiness": ("运行就绪", "Backend /ready 的生产必需依赖全部可用"),
    "worker_heartbeat": ("Worker 心跳", "独立后台 Worker 心跳新鲜且可被就绪探针读取"),
    "wechat_login": ("真实微信登录", "正式 AppID/Secret 完成一次真实 code2session 登录"),
    "uat_scope": ("隔离 UAT 数据", "专用 UAT 用户和批次已标识且不计入正式统计"),
    "renshe_private_oss": (
        "人社材料存储",
        "私有 OSS 的材料链路已通过，或该可选能力已明确标记为未配置",
    ),
    "wechat_payment": (
        "0.01 元微信支付",
        "JSAPI 支付及回调或主动查单确认成功",
    ),
    "wechat_refund": (
        "全额微信退款",
        "退款及退款回调或主动查单确认成功",
    ),
    "recovery_bundle": (
        "加密恢复包",
        "恢复包安装 ID 与 SHA-256 已核对；恢复 OSS 未配置时仅保留本机加密副本",
    ),
    "backup_restore_config": (
        "数据库备份恢复",
        "生产备份和恢复命令已配置并形成可追溯证据",
    ),
}

EVIDENCE_SOURCES = {"bootstrap", "system", "uat_reconciler"}
MAX_EVIDENCE_SUMMARY_BYTES = 16 * 1024


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def normalize_evidence_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Redact structured evidence and reject unbounded/non-JSON payloads."""

    sanitized = sanitize_audit_summary(summary) or {}
    try:
        encoded = _canonical_json(sanitized)
    except (TypeError, ValueError) as exc:
        raise ValueError("evidence summary must be JSON serializable") from exc
    if len(encoded) > MAX_EVIDENCE_SUMMARY_BYTES:
        raise ValueError("evidence summary is too large")
    return sanitized


def evidence_digest(
    *,
    evidence_type: str,
    result: str,
    source: str,
    summary: Mapping[str, Any],
) -> str:
    return hashlib.sha256(
        _canonical_json(
            {
                "evidence_type": evidence_type,
                "result": result,
                "source": source,
                "summary": normalize_evidence_summary(summary),
            }
        )
    ).hexdigest()


class DeploymentAcceptanceService:
    """Admin read/sign API plus internal-only evidence writer."""

    def __init__(self, db_context_factory=None) -> None:
        self._db_context_factory = db_context_factory or get_db_ctx

    async def get_status(self) -> DeploymentAcceptanceResponse:
        async with self._db_context_factory() as db:
            acceptance = await db.scalar(
                select(DeploymentAcceptance).order_by(DeploymentAcceptance.id.desc())
            )
            if acceptance is None:
                raise NotFoundException("部署验收记录")
            events = (
                await db.execute(
                    select(DeploymentAcceptanceEvent)
                    .where(
                        DeploymentAcceptanceEvent.acceptance_id == acceptance.id,
                        DeploymentAcceptanceEvent.event_type == "evidence_recorded",
                    )
                    .order_by(DeploymentAcceptanceEvent.id)
                )
            ).scalars().all()
            return self._response(acceptance, events)

    async def accept(
        self,
        *,
        admin_id: int,
        request: DeploymentAcceptanceSignRequest,
    ) -> DeploymentAcceptanceResponse:
        async with self._db_context_factory() as db:
            acceptance = await db.scalar(
                select(DeploymentAcceptance)
                .order_by(DeploymentAcceptance.id.desc())
                .with_for_update()
            )
            if acceptance is None:
                raise NotFoundException("部署验收记录")
            if request.release_manifest_sha256 != acceptance.release_manifest_sha256:
                raise ConflictException("发布清单摘要已变化，请刷新后重新确认")
            events = (
                await db.execute(
                    select(DeploymentAcceptanceEvent)
                    .where(
                        DeploymentAcceptanceEvent.acceptance_id == acceptance.id,
                        DeploymentAcceptanceEvent.event_type == "evidence_recorded",
                    )
                    .order_by(DeploymentAcceptanceEvent.id)
                )
            ).scalars().all()
            current = self._latest_evidence(events)
            missing = [
                evidence_type
                for evidence_type in DEPLOYMENT_EVIDENCE_TYPES
                if current.get(evidence_type) is None
                or current[evidence_type].result != "passed"
            ]
            if acceptance.status == "production_accepted":
                return self._response(acceptance, events)
            if missing:
                labels = "、".join(EVIDENCE_DEFINITIONS[item][0] for item in missing)
                raise ConflictException(f"仍有验收项未通过：{labels}")

            evidence_summary = {
                "installation_id": acceptance.installation_id,
                "release_manifest_sha256": acceptance.release_manifest_sha256,
                "evidence": {
                    name: current[name].evidence_sha256
                    for name in DEPLOYMENT_EVIDENCE_TYPES
                },
            }
            summary_digest = hashlib.sha256(
                _canonical_json(evidence_summary)
            ).hexdigest()
            accepted_at = datetime.now(timezone.utc)
            acceptance.status = "production_accepted"
            acceptance.accepted_by_admin_id = admin_id
            acceptance.accepted_at = accepted_at
            acceptance.evidence_summary_sha256 = summary_digest
            db.add(
                DeploymentAcceptanceEvent(
                    acceptance_id=acceptance.id,
                    event_type="acceptance_signed",
                    evidence_type=None,
                    result=None,
                    source="system",
                    actor_admin_id=admin_id,
                    evidence_sha256=summary_digest,
                    summary=evidence_summary,
                )
            )
            await db.commit()
            await db.refresh(acceptance)
            return self._response(acceptance, events)

    async def record_evidence(
        self,
        *,
        installation_id: str,
        evidence_type: str,
        result: str,
        source: str,
        summary: Mapping[str, Any],
    ) -> str:
        """Write verified system evidence; intentionally not exposed as an API."""

        if evidence_type not in DEPLOYMENT_EVIDENCE_TYPES:
            raise ValueError("unsupported deployment evidence type")
        if result not in DEPLOYMENT_EVIDENCE_RESULTS:
            raise ValueError("unsupported deployment evidence result")
        if source not in EVIDENCE_SOURCES:
            raise ValueError("unsupported deployment evidence source")
        safe_summary = normalize_evidence_summary(summary)
        digest = evidence_digest(
            evidence_type=evidence_type,
            result=result,
            source=source,
            summary=safe_summary,
        )
        async with self._db_context_factory() as db:
            acceptance = await db.scalar(
                select(DeploymentAcceptance)
                .where(DeploymentAcceptance.installation_id == installation_id)
                .with_for_update()
            )
            if acceptance is None:
                raise NotFoundException("部署验收记录")
            if acceptance.status != "installed_pending_uat":
                raise ConflictException("生产验收已经签署，不能追加证据")
            existing = await db.scalar(
                select(DeploymentAcceptanceEvent.id).where(
                    DeploymentAcceptanceEvent.acceptance_id == acceptance.id,
                    DeploymentAcceptanceEvent.event_type == "evidence_recorded",
                    DeploymentAcceptanceEvent.evidence_sha256 == digest,
                )
            )
            if existing is None:
                db.add(
                    DeploymentAcceptanceEvent(
                        acceptance_id=acceptance.id,
                        event_type="evidence_recorded",
                        evidence_type=evidence_type,
                        result=result,
                        source=source,
                        actor_admin_id=None,
                        evidence_sha256=digest,
                        summary=safe_summary,
                    )
                )
                try:
                    await db.commit()
                except IntegrityError:
                    await db.rollback()
                    # An identical concurrent system observation is idempotent.
                    duplicate = await db.scalar(
                        select(DeploymentAcceptanceEvent.id).where(
                            DeploymentAcceptanceEvent.acceptance_id == acceptance.id,
                            DeploymentAcceptanceEvent.event_type == "evidence_recorded",
                            DeploymentAcceptanceEvent.evidence_sha256 == digest,
                        )
                    )
                    if duplicate is None:
                        raise
            return digest

    @staticmethod
    def _latest_evidence(
        events: list[DeploymentAcceptanceEvent],
    ) -> dict[str, DeploymentAcceptanceEvent]:
        latest: dict[str, DeploymentAcceptanceEvent] = {}
        for event in events:
            if event.evidence_type in DEPLOYMENT_EVIDENCE_TYPES:
                latest[event.evidence_type] = event
        return latest

    @classmethod
    def _response(
        cls,
        acceptance: DeploymentAcceptance,
        events: list[DeploymentAcceptanceEvent],
    ) -> DeploymentAcceptanceResponse:
        latest = cls._latest_evidence(events)
        evidence: list[DeploymentEvidenceResponse] = []
        missing: list[DeploymentEvidenceType] = []
        for name in DEPLOYMENT_EVIDENCE_TYPES:
            event = latest.get(name)
            passed = event is not None and event.result == "passed"
            if not passed:
                missing.append(name)  # type: ignore[arg-type]
            label, meaning = EVIDENCE_DEFINITIONS[name]
            evidence.append(
                DeploymentEvidenceResponse(
                    evidence_type=name,  # type: ignore[arg-type]
                    label=label,
                    meaning=meaning,
                    passed=passed,
                    latest_result=event.result if event is not None else None,
                    source=event.source if event is not None else None,
                    recorded_at=event.created_at if event is not None else None,
                    evidence_sha256=(
                        event.evidence_sha256 if event is not None else None
                    ),
                    summary=event.summary if event is not None else None,
                )
            )
        return DeploymentAcceptanceResponse(
            installation_id=acceptance.installation_id,
            status=acceptance.status,  # type: ignore[arg-type]
            status_meaning=STATUS_MEANINGS[acceptance.status],
            backend_commit=acceptance.backend_commit,
            admin_commit=acceptance.admin_commit,
            release_manifest_sha256=acceptance.release_manifest_sha256,
            recovery_object_key=acceptance.recovery_object_key,
            recovery_sha256=acceptance.recovery_sha256,
            database_fingerprint_sha256=acceptance.database_fingerprint_sha256,
            evidence=evidence,
            missing_evidence=missing,
            can_accept=(
                acceptance.status == "installed_pending_uat" and not missing
            ),
            accepted_by_admin_id=acceptance.accepted_by_admin_id,
            accepted_at=acceptance.accepted_at,
            evidence_summary_sha256=acceptance.evidence_summary_sha256,
            created_at=acceptance.created_at,
            updated_at=acceptance.updated_at,
        )
