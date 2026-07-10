"""H3C 认证报名服务"""
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.adapter.database import get_db_ctx
from app.domain.certification.src.index import Certification
from app.domain.order.src.index import (
    INVENTORY_LOCK_ACTION,
    ORDER_PAYMENT_EXPIRE_MINUTES,
    Order,
    PriceConfig,
    add_inventory_record,
    lock_certification_inventory,
)
from app.domain.user.src.index import User, UserRealname
from app.port.exceptions import BusinessException, ConflictException, NotFoundException, ValidationException
from app.schemas.h3c import H3cOrderCreate, H3cOrderResponse, H3cProfileDefaults
from app.services.order import resolve_price_tier
from app.services.user import UserService

H3C_COUNTRY = "CHN"
H3C_LANGUAGE = "CHS"


def _build_extra_data(data: H3cOrderCreate) -> dict:
    """将 H3C 专用字段打包为 extra_data"""
    extra = {
        "exam_code": data.exam_code,
        "coupon_code": data.coupon_code,
        "verify_code": data.verify_code,
        "identity_tag": data.identity_tag,
        "exam_datetime": data.exam_datetime,
        "gender": data.gender,
        "school": data.school,
        "address": data.address,
        "email": data.email,
        "education": data.education,
        "first_name_en": data.first_name_en,
        "last_name_en": data.last_name_en,
        "country": H3C_COUNTRY,
        "language": H3C_LANGUAGE,
    }
    if data.birth_date:
        extra["birth_date"] = data.birth_date
    if data.candidate_no:
        extra["candidate_no"] = data.candidate_no
    if data.training_org:
        extra["training_org"] = data.training_org
    if data.training_address:
        extra["training_address"] = data.training_address
    if data.training_start:
        extra["training_start"] = data.training_start
    if data.training_end:
        extra["training_end"] = data.training_end
    return extra


class H3cOrderService:

    def _validate_user(self, user: User) -> None:
        if not user.is_active:
            raise ValidationException("用户已被禁用")

    async def get_profile_defaults(self, user_id: int) -> H3cProfileDefaults:
        """从个人资料获取 H3C 报名预填值"""
        profile = await UserService().get_profile(user_id)
        return H3cProfileDefaults(
            candidate_name=profile.real_name,
            gender=profile.gender,
            candidate_idcard=profile.id_card,
            school=profile.school,
            address=f"{profile.province or ''}{profile.city or ''}{profile.address or ''}".strip() or None,
            phone=profile.phone,
            email=profile.email,
            education=profile.education,
            first_name_en=profile.first_name_en,
            last_name_en=profile.last_name_en,
            birth_date=profile.birth_date,
            degree_cert_oss=profile.degree_cert_oss,
        )

    async def create_order(self, user_id: int, data: H3cOrderCreate) -> H3cOrderResponse:
        async with get_db_ctx() as db:
            async with db.begin():
                user = await db.get(User, user_id)
                if user is None:
                    raise NotFoundException("用户")
                self._validate_user(user)

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

                product_type = data.exam_code
                cert = (
                    await db.execute(
                        select(Certification).where(
                            Certification.code == product_type,
                            Certification.vendor == "H3C",
                            Certification.is_active.is_(True),
                        )
                    )
                ).scalar_one_or_none()
                if cert is None:
                    raise BusinessException("H3C 认证类型不存在或已下架")

                price_tier = resolve_price_tier(identity.user_type)
                price_rows = (
                    await db.execute(
                        select(PriceConfig).where(
                            PriceConfig.product_type == product_type,
                            PriceConfig.user_type == price_tier,
                            PriceConfig.is_active.is_(True),
                        ).limit(2)
                    )
                ).scalars().all()
                if not price_rows:
                    raise BusinessException("该 H3C 认证类型暂未配置价格")
                if len(price_rows) > 1:
                    raise ConflictException("该 H3C 认证类型价格配置重复，请联系管理员")

                inventory_change = await lock_certification_inventory(db, product_type)
                extra = _build_extra_data(data)
                attachments = [oss for oss in (data.coupon_proof_oss, data.degree_cert_oss) if oss]
                expires_at = datetime.now(timezone.utc) + timedelta(minutes=ORDER_PAYMENT_EXPIRE_MINUTES)

                order = Order(
                    user_id=user_id,
                    order_kind="certification",
                    product_type=product_type,
                    inventory_id=inventory_change.inventory_id,
                    candidate_name=data.candidate_name,
                    candidate_phone=data.phone,
                    candidate_idcard=data.candidate_idcard,
                    price=price_rows[0].price,
                    status="pending",
                    out_trade_no=f"{user_id}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}",
                    extra_data=extra,
                    attachments=attachments,
                    expires_at=expires_at,
                )
                db.add(order)
                await db.flush()
                add_inventory_record(
                    db,
                    change=inventory_change,
                    order_id=order.id,
                    action=INVENTORY_LOCK_ACTION,
                    reason="order_created",
                )
                await db.refresh(order)

            return H3cOrderResponse(
                id=order.id,
                order_kind=order.order_kind,
                product_type=order.product_type,
                candidate_name=order.candidate_name,
                candidate_phone=order.candidate_phone,
                candidate_idcard=order.candidate_idcard,
                price=order.price,
                status=order.status,
                out_trade_no=order.out_trade_no,
                inventory_id=order.inventory_id,
                expires_at=order.expires_at,
                created_at=order.created_at,
                extra_data=order.extra_data,
                attachments=order.attachments,
            )
