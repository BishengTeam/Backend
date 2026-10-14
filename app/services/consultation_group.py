from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapter.database import get_db_ctx
from app.domain.plan.src.index import Plan
from app.models.cert_product import CertProduct
from app.models.consultation_group import CertConsultationGroup
from app.port.exceptions import ConflictException, NotFoundException
from app.schemas.consultation_group import (
    ConsultationGroupAdminResponse,
    ConsultationGroupEmptyResponse,
    ConsultationGroupUpdate,
    ConsultationGroupUserResponse,
)


USER_VISIBLE_PLAN_STATUSES = {"published", "registration_closed", "finalized"}

USER_VISIBLE_REGISTRATION_STATUSES = {
    # H3C / NISP shared dedicated-flow statuses
    "pending_initial_review",
    "initial_rejected",
    "pending_review",
    "rejected_awaiting_resubmission",
    "pending_external_review",
    "external_rejected",
    "approved",
    "final_approved",
    # Refund follow-up remains visible.
    "pending_refund_confirmation",
    "refund_processing",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_expired(row: CertConsultationGroup, now: datetime | None = None) -> bool:
    current = _utc(now or _now())
    expires_at = _utc(row.expires_at)
    return expires_at is not None and expires_at <= current


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class ConsultationGroupService:
    async def get_admin(
        self, plan_id: int
    ) -> ConsultationGroupAdminResponse | ConsultationGroupEmptyResponse:
        async with get_db_ctx() as db:
            plan = await db.get(Plan, plan_id)
            if plan is None:
                raise NotFoundException("认证批次")
            row = await db.get(CertConsultationGroup, plan_id)
            if row is None:
                return ConsultationGroupEmptyResponse(plan_id=plan_id)
            return ConsultationGroupAdminResponse(
                plan_id=plan_id,
                qrcode_url=row.qrcode_url,
                join_instructions=row.join_instructions,
                expires_at=row.expires_at,
                version=row.version,
                status="expired" if _is_expired(row) else "configured",
                updated_by=row.updated_by,
                updated_at=row.updated_at,
            )

    async def update(
        self, *, plan_id: int, admin_id: int, data: ConsultationGroupUpdate
    ) -> ConsultationGroupAdminResponse | ConsultationGroupEmptyResponse:
        async with get_db_ctx() as db:
            async with db.begin():
                plan = await db.get(Plan, plan_id, with_for_update=True)
                if plan is None:
                    raise NotFoundException("认证批次")
                row = await db.get(CertConsultationGroup, plan_id, with_for_update=True)
                current_version = row.version if row is not None else 0
                if current_version != data.version:
                    raise ConflictException("咨询群配置已更新，请刷新后重试")

                if data.qrcode_url is None:
                    if row is not None:
                        await db.delete(row)
                    return ConsultationGroupEmptyResponse(plan_id=plan_id)

                if row is None:
                    row = CertConsultationGroup(
                        plan_id=plan_id,
                        qrcode_url=data.qrcode_url,
                        join_instructions=data.join_instructions,
                        expires_at=_utc(data.expires_at),
                        version=1,
                        updated_by=admin_id,
                    )
                    db.add(row)
                else:
                    row.qrcode_url = data.qrcode_url
                    row.join_instructions = data.join_instructions
                    row.expires_at = _utc(data.expires_at)
                    row.version += 1
                    row.updated_by = admin_id
                await db.flush()
                await db.refresh(row)
                return ConsultationGroupAdminResponse(
                    plan_id=plan_id,
                    qrcode_url=row.qrcode_url,
                    join_instructions=row.join_instructions,
                    expires_at=row.expires_at,
                    version=row.version,
                    status="expired" if _is_expired(row) else "configured",
                    updated_by=row.updated_by,
                    updated_at=row.updated_at,
                )

    @staticmethod
    def legacy_nisp_group(
        *,
        batch,
        plan: Plan,
        registration_status: str,
    ) -> ConsultationGroupUserResponse | None:
        """Temporary compatibility for NISP clients that still write the old field."""
        if plan.status not in USER_VISIBLE_PLAN_STATUSES:
            return None
        if registration_status not in USER_VISIBLE_REGISTRATION_STATUSES:
            return None
        qrcode_url = (batch.consultation_group_qrcode_url or "").strip()
        if not qrcode_url:
            return None
        return ConsultationGroupUserResponse(
            qrcode_url=qrcode_url,
            contact_name=plan.contact_name,
            contact_phone=plan.contact_phone,
        )

    @staticmethod
    async def get_user_group(
        db: AsyncSession,
        *,
        plan: Plan,
        registration_status: str,
    ) -> ConsultationGroupUserResponse | None:
        """Return a valid consultation group only to eligible applicants."""
        if plan.status not in USER_VISIBLE_PLAN_STATUSES:
            return None
        if registration_status not in USER_VISIBLE_REGISTRATION_STATUSES:
            return None
        row = await db.get(CertConsultationGroup, plan.id)
        if row is None or _is_expired(row):
            return None
        return ConsultationGroupUserResponse(
            qrcode_url=row.qrcode_url,
            join_instructions=row.join_instructions,
            expires_at=row.expires_at,
            contact_name=plan.contact_name,
            contact_phone=plan.contact_phone,
        )

    @staticmethod
    async def required_permission(db: AsyncSession, plan_id: int) -> str:
        plan = await db.get(Plan, plan_id)
        if plan is None:
            raise NotFoundException("认证批次")
        product = await db.scalar(
            select(CertProduct).where(CertProduct.code == plan.product_type)
        )
        product_type = product.type if product is not None else ""
        normalized = plan.product_type.strip().upper()
        if product_type == "nisp" or normalized.startswith("NISP-"):
            return "nisp:batch_manage"
        if product_type == "h3c" or normalized.startswith("H3C"):
            return "h3c:batch_manage"
        if product_type == "renshe" or normalized == "RS-ZY":
            return "user:write"
        return "content:write"
