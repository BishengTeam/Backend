from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.port.exceptions import NotFoundException
from app.domain.content.src.index import Agreement
from app.schemas.admin_agreement import AdminAgreementCreate, AdminAgreementListItem, AdminAgreementReview
from app.schemas.common import PaginatedData


class AdminAgreementService:

    async def list_agreements(
        self, page: int, page_size: int
    ) -> PaginatedData[AdminAgreementListItem]:
        async with get_db_ctx() as db:
            base = select(Agreement)
            count_stmt = select(func.count()).select_from(base.subquery())
            total = (await db.execute(count_stmt)).scalar() or 0
            stmt = base.order_by(Agreement.id.desc()).offset((page - 1) * page_size).limit(page_size)
            result = await db.execute(stmt)
            agreements = result.scalars().all()
            return PaginatedData[AdminAgreementListItem](
                items=[AdminAgreementListItem.model_validate(a) for a in agreements],
                total=total,
                page=page,
                page_size=page_size,
            )

    async def create(self, data: AdminAgreementCreate) -> AdminAgreementListItem:
        async with get_db_ctx() as db:
            agreement = Agreement(**data.model_dump())
            db.add(agreement)
            await db.commit()
            await db.refresh(agreement)
            return AdminAgreementListItem.model_validate(agreement)

    async def review(self, agreement_id: int, data: AdminAgreementReview) -> AdminAgreementListItem:
        async with get_db_ctx() as db:
            agreement = await db.get(Agreement, agreement_id)
            if agreement is None:
                raise NotFoundException("协议")
            agreement.status = data.status
            if data.signature_image is not None:
                agreement.signature_image = data.signature_image
            await db.commit()
            await db.refresh(agreement)
            return AdminAgreementListItem.model_validate(agreement)
