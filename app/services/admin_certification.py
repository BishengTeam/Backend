from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.port.exceptions import NotFoundException
from app.domain.certification.src.index import Certification
from app.schemas.admin_certification import AdminCertificationCreate, AdminCertificationListItem, AdminCertificationUpdate
from app.schemas.certification import CertificationResponse
from app.schemas.common import PaginatedData


class AdminCertificationService:

    async def list_certifications(
        self, keyword: str | None, page: int, page_size: int
    ) -> PaginatedData[AdminCertificationListItem]:
        async with get_db_ctx() as db:
            base = select(Certification)
            if keyword:
                base = base.where(
                    Certification.name.ilike(f"%{keyword}%")
                    | Certification.chinese_name.ilike(f"%{keyword}%")
                )
            count_stmt = select(func.count()).select_from(base.subquery())
            total = (await db.execute(count_stmt)).scalar() or 0
            stmt = base.order_by(Certification.id.desc()).offset(
                (page - 1) * page_size
            ).limit(page_size)
            result = await db.execute(stmt)
            certs = result.scalars().all()
            return PaginatedData[AdminCertificationListItem](
                items=[AdminCertificationListItem.model_validate(c) for c in certs],
                total=total,
                page=page,
                page_size=page_size,
            )

    async def create(self, data: AdminCertificationCreate) -> CertificationResponse:
        async with get_db_ctx() as db:
            cert = Certification(**data.model_dump())
            db.add(cert)
            await db.commit()
            await db.refresh(cert)
            return CertificationResponse.model_validate(cert)

    async def update(self, cert_id: int, data: AdminCertificationUpdate) -> CertificationResponse:
        async with get_db_ctx() as db:
            cert = await db.get(Certification, cert_id)
            if cert is None:
                raise NotFoundException("认证类型")
            update_data = data.model_dump(exclude_unset=True)
            for key, value in update_data.items():
                setattr(cert, key, value)
            await db.commit()
            await db.refresh(cert)
            return CertificationResponse.model_validate(cert)

    async def deactivate(self, cert_id: int) -> None:
        async with get_db_ctx() as db:
            cert = await db.get(Certification, cert_id)
            if cert is None:
                raise NotFoundException("认证类型")
            cert.is_active = False
            await db.commit()
