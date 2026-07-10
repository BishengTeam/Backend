from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.domain.order.src.index import PriceConfig
from app.port.exceptions import NotFoundException
from app.domain.certification.src.index import Certification
from app.schemas.admin_certification import AdminCertificationCreate, AdminCertificationListItem, AdminCertificationUpdate
from app.schemas.common import PaginatedData

PRICE_TIER_NORMAL = "normal"
PRICE_TIER_STUDENT = "student"


class AdminCertificationService:
    def _display_name(self, code: str) -> str:
        return code.lower().replace("-", "_")

    def _build_item(
        self,
        cert: Certification,
        prices: dict[tuple[str, str], int],
    ) -> AdminCertificationListItem:
        return AdminCertificationListItem(
            id=cert.id,
            code=cert.code,
            vendor=cert.vendor,
            normal_price=prices.get((cert.code, PRICE_TIER_NORMAL)),
            student_price=prices.get((cert.code, PRICE_TIER_STUDENT)),
            is_active=cert.is_active,
            created_at=cert.created_at,
            updated_at=cert.updated_at,
        )

    async def _upsert_price(
        self,
        db,
        *,
        product_type: str,
        user_type: str,
        price: int,
    ) -> None:
        row = (
            await db.execute(
                select(PriceConfig).where(
                    PriceConfig.product_type == product_type,
                    PriceConfig.user_type == user_type,
                    PriceConfig.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            db.add(
                PriceConfig(
                    product_type=product_type,
                    user_type=user_type,
                    price=price,
                    is_active=True,
                )
            )
            return
        row.price = price

    async def _sync_prices_for_code_change(
        self,
        db,
        *,
        old_code: str,
        new_code: str,
    ) -> None:
        if old_code == new_code:
            return
        rows = (
            await db.execute(
                select(PriceConfig).where(
                    PriceConfig.product_type == old_code,
                    PriceConfig.is_active.is_(True),
                )
            )
        ).scalars().all()
        for row in rows:
            row.product_type = new_code

    async def list_certifications(
        self, keyword: str | None, page: int, page_size: int
    ) -> PaginatedData[AdminCertificationListItem]:
        async with get_db_ctx() as db:
            base = select(Certification)
            if keyword:
                base = base.where(
                    Certification.name.ilike(f"%{keyword}%")
                    | Certification.chinese_name.ilike(f"%{keyword}%")
                    | Certification.code.ilike(f"%{keyword}%")
                )
            count_stmt = select(func.count()).select_from(base.subquery())
            total = (await db.execute(count_stmt)).scalar() or 0
            stmt = base.order_by(Certification.id.desc()).offset(
                (page - 1) * page_size
            ).limit(page_size)
            result = await db.execute(stmt)
            certs = result.scalars().all()
            codes = [cert.code for cert in certs]
            prices: dict[tuple[str, str], int] = {}
            if codes:
                price_rows = (
                    await db.execute(
                        select(PriceConfig).where(
                            PriceConfig.product_type.in_(codes),
                            PriceConfig.user_type.in_([PRICE_TIER_NORMAL, PRICE_TIER_STUDENT]),
                            PriceConfig.is_active.is_(True),
                        )
                    )
                ).scalars().all()
                prices = {
                    (row.product_type, row.user_type): row.price
                    for row in price_rows
                }
            return PaginatedData[AdminCertificationListItem](
                items=[self._build_item(c, prices) for c in certs],
                total=total,
                page=page,
                page_size=page_size,
            )

    async def create(self, data: AdminCertificationCreate) -> AdminCertificationListItem:
        async with get_db_ctx() as db:
            async with db.begin():
                cert = Certification(
                    name=self._display_name(data.code),
                    chinese_name=data.code,
                    code=data.code,
                    vendor=data.vendor,
                    requires_xuexin=False,
                    pay_first=True,
                    is_active=data.is_active,
                )
                db.add(cert)
                await db.flush()
                await self._upsert_price(
                    db,
                    product_type=data.code,
                    user_type=PRICE_TIER_NORMAL,
                    price=data.normal_price,
                )
                await self._upsert_price(
                    db,
                    product_type=data.code,
                    user_type=PRICE_TIER_STUDENT,
                    price=data.student_price,
                )
                await db.refresh(cert)
            return self._build_item(
                cert,
                {
                    (cert.code, PRICE_TIER_NORMAL): data.normal_price,
                    (cert.code, PRICE_TIER_STUDENT): data.student_price,
                },
            )

    async def update(self, cert_id: int, data: AdminCertificationUpdate) -> AdminCertificationListItem:
        async with get_db_ctx() as db:
            async with db.begin():
                cert = await db.get(Certification, cert_id)
                if cert is None:
                    raise NotFoundException("认证类型")

                update_data = data.model_dump(exclude_unset=True)
                old_code = cert.code
                new_code = update_data.get("code", old_code)
                if "code" in update_data:
                    cert.code = new_code
                    cert.name = self._display_name(new_code)
                    cert.chinese_name = new_code
                if "vendor" in update_data:
                    cert.vendor = update_data["vendor"]
                if "is_active" in update_data:
                    cert.is_active = update_data["is_active"]

                await self._sync_prices_for_code_change(db, old_code=old_code, new_code=new_code)

                normal_price = update_data.get("normal_price")
                student_price = update_data.get("student_price")
                if normal_price is not None:
                    await self._upsert_price(
                        db,
                        product_type=new_code,
                        user_type=PRICE_TIER_NORMAL,
                        price=normal_price,
                    )
                if student_price is not None:
                    await self._upsert_price(
                        db,
                        product_type=new_code,
                        user_type=PRICE_TIER_STUDENT,
                        price=student_price,
                    )

                price_rows = (
                    await db.execute(
                        select(PriceConfig).where(
                            PriceConfig.product_type == new_code,
                            PriceConfig.user_type.in_([PRICE_TIER_NORMAL, PRICE_TIER_STUDENT]),
                            PriceConfig.is_active.is_(True),
                        )
                    )
                ).scalars().all()
                prices = {(row.product_type, row.user_type): row.price for row in price_rows}
                await db.refresh(cert)
            return self._build_item(cert, prices)

    async def deactivate(self, cert_id: int) -> None:
        async with get_db_ctx() as db:
            cert = await db.get(Certification, cert_id)
            if cert is None:
                raise NotFoundException("认证类型")
            cert.is_active = False
            await db.commit()
