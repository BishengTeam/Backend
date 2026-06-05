from app.adapter.database import get_db_ctx
from app.port.exceptions import NotFoundException
from app.domain.certification.src.index import Certification
from app.schemas.admin_certification import AdminCertificationCreate, AdminCertificationUpdate
from app.schemas.certification import CertificationResponse


class AdminCertificationService:

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
