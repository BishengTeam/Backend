from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.port.exceptions import NotFoundException
from app.models.agreement import Agreement
from app.schemas.agreement import AgreementCreate, AgreementResponse, AgreementSign
from app.schemas.common import PaginatedData


class AgreementService:

    async def list_agreements(
        self, user_id: int, type_filter: str | None, page: int, page_size: int
    ) -> PaginatedData[AgreementResponse]:
        async with get_db_ctx() as db:
            base = select(Agreement).where(Agreement.user_id == user_id)
            if type_filter:
                base = base.where(Agreement.type == type_filter)
            count_stmt = select(func.count()).select_from(base.subquery())
            total = (await db.execute(count_stmt)).scalar() or 0
            stmt = (
                base.order_by(Agreement.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            result = await db.execute(stmt)
            agreements = result.scalars().all()
            return PaginatedData[AgreementResponse](
                items=[AgreementResponse.model_validate(a) for a in agreements],
                total=total,
                page=page,
                page_size=page_size,
            )

    async def create(self, user_id: int, data: AgreementCreate) -> AgreementResponse:
        async with get_db_ctx() as db:
            agreement = Agreement(
                user_id=user_id,
                type=data.type,
                content=data.content,
                status="sent",
            )
            db.add(agreement)
            await db.commit()
            await db.refresh(agreement)
            return AgreementResponse.model_validate(agreement)

    async def sign(
        self, user_id: int, agreement_id: int, data: AgreementSign
    ) -> AgreementResponse:
        async with get_db_ctx() as db:
            agreement = await db.get(Agreement, agreement_id)
            if agreement is None:
                raise NotFoundException("协议")
            if agreement.user_id != user_id:
                raise NotFoundException("协议")
            agreement.signature_image = data.signature_image
            agreement.status = "signed"
            await db.commit()
            await db.refresh(agreement)
            return AgreementResponse.model_validate(agreement)
