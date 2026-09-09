"""Admin agreement template service (P0 e-agreement)."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapter.database import get_db_ctx
from app.domain.content.src.index import AgreementTemplate
from app.port.exceptions import BusinessException, NotFoundException
from app.schemas.admin_agreement_template import (
    AdminAgreementTemplateCreate,
    AdminAgreementTemplateItem,
    AdminAgreementTemplateUpdate,
)
from app.schemas.common import PaginatedData


class AdminAgreementTemplateService:
    async def list_templates(
        self,
        type_filter: str | None,
        status_filter: str | None,
        page: int,
        page_size: int,
    ) -> PaginatedData[AdminAgreementTemplateItem]:
        async with get_db_ctx() as db:
            base = select(AgreementTemplate)
            if type_filter:
                base = base.where(AgreementTemplate.type == type_filter)
            if status_filter:
                base = base.where(AgreementTemplate.status == status_filter)
            count_stmt = select(func.count()).select_from(base.subquery())
            total = (await db.execute(count_stmt)).scalar() or 0
            stmt = (
                base.order_by(
                    AgreementTemplate.type.asc(),
                    AgreementTemplate.version.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            rows = (await db.scalars(stmt)).all()
            return PaginatedData[AdminAgreementTemplateItem](
                items=[AdminAgreementTemplateItem.model_validate(r) for r in rows],
                total=total,
                page=page,
                page_size=page_size,
            )

    async def create(self, data: AdminAgreementTemplateCreate) -> AdminAgreementTemplateItem:
        async with get_db_ctx() as db:
            template = await self._new_version(
                db,
                agreement_type=data.type,
                title=data.title,
                content=data.content,
            )
            await db.commit()
            await db.refresh(template)
            return AdminAgreementTemplateItem.model_validate(template)

    async def update(
        self, template_id: int, data: AdminAgreementTemplateUpdate
    ) -> AdminAgreementTemplateItem:
        """Editing archives the old row and creates version+1 (append-only)."""
        async with get_db_ctx() as db:
            old = await db.get(AgreementTemplate, template_id)
            if old is None:
                raise NotFoundException("协议模板")
            old.status = "archived"
            template = await self._new_version(
                db,
                agreement_type=old.type,
                title=data.title,
                content=data.content,
            )
            await db.commit()
            await db.refresh(template)
            return AdminAgreementTemplateItem.model_validate(template)

    async def archive(self, template_id: int) -> AdminAgreementTemplateItem:
        async with get_db_ctx() as db:
            template = await db.get(AgreementTemplate, template_id)
            if template is None:
                raise NotFoundException("协议模板")
            if template.status != "active":
                raise BusinessException("仅生效中的模板可以归档")
            template.status = "archived"
            await db.commit()
            await db.refresh(template)
            return AdminAgreementTemplateItem.model_validate(template)

    @staticmethod
    async def _new_version(
        db: AsyncSession, *, agreement_type: str, title: str, content: str
    ) -> AgreementTemplate:
        active = await db.scalar(
            select(AgreementTemplate).where(
                AgreementTemplate.type == agreement_type,
                AgreementTemplate.status == "active",
            )
        )
        if active is not None:
            active.status = "archived"
        max_version = await db.scalar(
            select(func.max(AgreementTemplate.version)).where(
                AgreementTemplate.type == agreement_type
            )
        )
        template = AgreementTemplate(
            type=agreement_type,
            title=title,
            content=content,
            version=(max_version or 0) + 1,
            status="active",
        )
        db.add(template)
        await db.flush()
        return template
