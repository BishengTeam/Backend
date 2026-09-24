"""Certification order handler — registered via decorator."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.certification.src.index import Certification
from app.domain.user.src.index import UserRealname
from app.models.cert_product import CertProduct
from app.port.exceptions import BusinessException
from app.schemas.order import OrderCreate
from app.services.agreement_template import ensure_accepted
from app.domain.order.src.transition.inventory_transitions import lock_certification_inventory
from app.services.order_handlers.registry import order_handler_registry
from app.services.order_utils import resolve_price_tier
from app.domain.order.src.rule.order_rules import validate_extra_data


@order_handler_registry.register("certification")
class CertificationOrderHandler:

    order_kind = "certification"

    async def validate(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        data: OrderCreate,
    ) -> int:
        # Identity check
        identity = (
            await db.execute(
                select(UserRealname).where(
                    UserRealname.user_id == user_id,
                    UserRealname.status == "verified",
                )
            )
        ).scalar_one_or_none()
        if identity is None:
            raise BusinessException("请先完成实名认证")

        # Agreement
        await ensure_accepted(
            db,
            user_id,
            "cert_registration",
            message="请先阅读并同意认证报名信息处理授权协议",
        )

        # Product lookup: new cert_product first, fallback to legacy certification
        cert = (
            await db.execute(
                select(CertProduct).where(
                    CertProduct.code == data.product_type,
                    CertProduct.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if cert is None:
            cert = (
                await db.execute(
                    select(Certification).where(
                        Certification.code == data.product_type,
                        Certification.is_active.is_(True),
                    )
                )
            ).scalar_one_or_none()
        if cert is None:
            raise BusinessException("认证类型不存在或已下架")

        # Price
        from sqlalchemy import select as sa_select
        from app.domain.order.src.index import PriceConfig

        price_tier = resolve_price_tier(identity.user_type)
        price_rows = (
            await db.execute(
                sa_select(PriceConfig).where(
                    PriceConfig.product_type == data.product_type,
                    PriceConfig.user_type == price_tier,
                    PriceConfig.is_active.is_(True),
                ).limit(2)
            )
        ).scalars().all()
        if not price_rows:
            raise BusinessException("该认证类型暂未配置价格")
        if len(price_rows) > 1:
            from app.port.exceptions import ConflictException
            raise ConflictException("该认证类型价格配置重复，请联系管理员")

        # Validate product-specific registration fields
        validate_extra_data(data.product_type, data.extra_data)

        return price_rows[0].price

    async def lock_inventory(
        self,
        db: AsyncSession,
        *,
        product_type: str,
    ) -> tuple[int | None, object]:
        change = await lock_certification_inventory(db, product_type)
        return change.inventory_id, change
