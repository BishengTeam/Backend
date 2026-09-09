"""User-side agreement template service (P0 e-agreement)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapter.database import get_db_ctx
from app.domain.content.src.index import AgreementAcceptance, AgreementTemplate
from app.port.exceptions import BusinessException, NotFoundException, ValidationException
from app.schemas.agreement_template import (
    AgreementAcceptanceItem,
    AgreementAcceptRequest,
    AgreementTemplatePublic,
)


class AgreementTemplateService:
    async def get_active(self, agreement_type: str) -> AgreementTemplatePublic:
        async with get_db_ctx() as db:
            template = await self._active_template(db, agreement_type)
            return AgreementTemplatePublic.model_validate(template)

    async def accept(
        self, user_id: int, data: AgreementAcceptRequest
    ) -> list[AgreementAcceptanceItem]:
        """Record acceptances with content snapshot; idempotent per version."""
        async with get_db_ctx() as db:
            items: list[AgreementAcceptanceItem] = []
            seen_template_ids: set[int] = set()
            for req in data.items:
                template = await self._active_template(db, req.type)
                if template.version != req.version:
                    raise BusinessException("协议已更新，请刷新后重新阅读并签署")
                if template.id in seen_template_ids:
                    raise ValidationException("同一协议只需签署一次")
                seen_template_ids.add(template.id)
                acceptance = await db.scalar(
                    select(AgreementAcceptance).where(
                        AgreementAcceptance.user_id == user_id,
                        AgreementAcceptance.template_id == template.id,
                    )
                )
                if acceptance is None:
                    acceptance = AgreementAcceptance(
                        user_id=user_id,
                        template_id=template.id,
                        type=template.type,
                        title=template.title,
                        version=template.version,
                        content_snapshot=template.content,
                    )
                    db.add(acceptance)
                    await db.flush()
                items.append(AgreementAcceptanceItem.model_validate(acceptance))
            await db.commit()
            return items

    async def list_acceptances(self, user_id: int) -> list[AgreementAcceptanceItem]:
        async with get_db_ctx() as db:
            rows = (
                await db.scalars(
                    select(AgreementAcceptance)
                    .where(AgreementAcceptance.user_id == user_id)
                    .order_by(
                        AgreementAcceptance.accepted_at.desc(),
                        AgreementAcceptance.id.desc(),
                    )
                )
            ).all()
            return [AgreementAcceptanceItem.model_validate(row) for row in rows]

    @staticmethod
    async def _active_template(
        db: AsyncSession, agreement_type: str
    ) -> AgreementTemplate:
        template = await db.scalar(
            select(AgreementTemplate).where(
                AgreementTemplate.type == agreement_type,
                AgreementTemplate.status == "active",
            )
        )
        if template is None:
            raise NotFoundException("协议模板")
        return template


async def ensure_accepted(
    db: AsyncSession, user_id: int, agreement_type: str, *, message: str
) -> None:
    """Gate helper used by other services (e.g. realname submission).

    Runs inside the CALLER's session/transaction so the gate check and the
    guarded write cannot race. Raises when an active template exists for the
    type but the user has not accepted that exact version. When no active
    template is configured the gate stays open — rollout is data-driven, so
    environments without a configured template are never blocked.
    """
    template = await db.scalar(
        select(AgreementTemplate).where(
            AgreementTemplate.type == agreement_type,
            AgreementTemplate.status == "active",
        )
    )
    if template is None:
        return
    accepted_id = await db.scalar(
        select(AgreementAcceptance.id).where(
            AgreementAcceptance.user_id == user_id,
            AgreementAcceptance.template_id == template.id,
        )
    )
    if accepted_id is None:
        raise BusinessException(message)
