from sqlalchemy import select

from app.core.database import get_db_ctx
from app.models.certification import Certification
from app.schemas.certification import CertificationFilter, CertificationResponse


class CertificationService:

    async def list_certifications(self, filters: CertificationFilter | None = None) -> list[CertificationResponse]:
        async with get_db_ctx() as db:
            stmt = select(Certification).where(Certification.is_active == True)
            if filters and filters.vendor:
                stmt = stmt.where(Certification.vendor == filters.vendor)
            result = await db.execute(stmt.order_by(Certification.id))
            certs = result.scalars().all()
            return [CertificationResponse.model_validate(c) for c in certs]
