"""Vendor-agnostic registration resolution for exam booking.

H3C keeps a dedicated registration table; Sangfor stores its application in
``Order.extra_data``. Exam booking only needs a small, uniform projection
(owner, status, product), so this module keeps vendor tables out of the
booking service.
"""
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cert_product import CertProduct
from app.port.exceptions import BusinessException

SUPPORTED_VENDORS = ("h3c", "sangfor")
APPROVED_STATUSES = ("approved", "final_approved")


@dataclass(frozen=True)
class VendorRegistrationInfo:
    """Uniform view over a vendor registration."""

    vendor: str
    registration_id: int
    user_id: int
    status: str
    product: CertProduct | None

    @property
    def approved(self) -> bool:
        return self.status in APPROVED_STATUSES


async def _resolve_h3c(
    db: AsyncSession, registration_id: int
) -> VendorRegistrationInfo | None:
    from app.domain.h3c.src.index import H3cBatchProduct, H3cExamBatch, H3cRegistration
    from app.domain.plan.src.index import Plan

    registration = await db.get(H3cRegistration, registration_id)
    if registration is None:
        return None

    product: CertProduct | None = None
    if registration.batch_product_id:
        batch_product = await db.get(H3cBatchProduct, registration.batch_product_id)
        if batch_product is not None:
            product = await db.get(CertProduct, batch_product.product_id)
    else:
        batch = await db.get(H3cExamBatch, registration.batch_id)
        plan = await db.get(Plan, batch.plan_id) if batch else None
        if plan is not None and plan.product_type:
            product = await db.scalar(
                select(CertProduct).where(CertProduct.code == plan.product_type)
            )

    return VendorRegistrationInfo(
        vendor="h3c",
        registration_id=registration.id,
        user_id=registration.user_id,
        status=registration.status,
        product=product,
    )


async def _resolve_sangfor(
    db: AsyncSession, registration_id: int
) -> VendorRegistrationInfo | None:
    from app.domain.order.src.index import Order
    from app.services.sangfor import effective_registration_status

    order = await db.get(Order, registration_id)
    if order is None or (order.extra_data or {}).get("business_type") != "sangfor":
        return None

    product = None
    if order.product_type:
        product = await db.scalar(
            select(CertProduct).where(CertProduct.code == order.product_type)
        )

    return VendorRegistrationInfo(
        vendor="sangfor",
        registration_id=order.id,
        user_id=order.user_id,
        status=await effective_registration_status(db, order),
        product=product,
    )


VENDOR_RESOLVERS = {
    "h3c": _resolve_h3c,
    "sangfor": _resolve_sangfor,
}


def normalize_vendor(vendor: str | None) -> str:
    value = vendor.strip().lower() if isinstance(vendor, str) and vendor else "h3c"
    if value not in VENDOR_RESOLVERS:
        raise BusinessException(f"暂不支持该厂商的考场预约：{vendor}")
    return value


async def resolve_registration(
    db: AsyncSession,
    vendor: str | None,
    registration_id: int,
    user_id: int | None = None,
) -> VendorRegistrationInfo | None:
    """Resolve a registration, returning ``None`` when it does not exist."""
    resolver = VENDOR_RESOLVERS[normalize_vendor(vendor)]
    info = await resolver(db, registration_id)
    if info is None:
        return None
    if user_id is not None and info.user_id != user_id:
        return None
    return info


async def approved_registration_ids(
    db: AsyncSession, vendor: str | None, user_id: int
) -> list[int]:
    """All approved registration ids of a user for the given vendor."""
    vendor = normalize_vendor(vendor)
    if vendor == "h3c":
        from app.domain.h3c.src.index import H3cRegistration

        rows = await db.scalars(
            select(H3cRegistration.id).where(
                H3cRegistration.user_id == user_id,
                H3cRegistration.status.in_(APPROVED_STATUSES),
            )
        )
        return list(rows.all())

    from app.domain.order.src.index import Order
    from app.models.offline_payment import OfflinePaymentAudit
    from app.services.sangfor import registration_status

    refund_approved = select(OfflinePaymentAudit.id).where(
        OfflinePaymentAudit.order_id == Order.id,
        OfflinePaymentAudit.action == "admin.generic_refund_approved",
    ).exists()

    orders = await db.scalars(
        select(Order).where(
            Order.user_id == user_id,
            Order.order_kind == "certification",
            Order.extra_data["business_type"].as_string() == "sangfor",
            Order.status.not_in(("closed", "refunded")),
            ~refund_approved,
        )
    )
    return [
        order.id for order in orders.all()
        if registration_status(order) in APPROVED_STATUSES
    ]


async def user_registration_ids(
    db: AsyncSession, vendor: str | None, user_id: int
) -> list[int]:
    """All registration ids (any status) the user owns for a vendor."""
    vendor = normalize_vendor(vendor)
    if vendor == "h3c":
        from app.domain.h3c.src.index import H3cRegistration

        rows = await db.scalars(
            select(H3cRegistration.id).where(H3cRegistration.user_id == user_id)
        )
        return list(rows.all())

    from app.domain.order.src.index import Order

    rows = await db.scalars(
        select(Order.id).where(
            Order.user_id == user_id,
            Order.order_kind == "certification",
            Order.extra_data["business_type"].as_string() == "sangfor",
        )
    )
    return list(rows.all())


async def registration_matches_schedule(
    db: AsyncSession, vendor: str | None, user_id: int, schedule_id: int
) -> bool:
    """一次存在性查询判断用户是否有「已通过且科目在该计划中」的报名。"""
    from app.domain.exam.src.index import ExamScheduleSubject

    vendor = normalize_vendor(vendor)
    if vendor == "h3c":
        from app.domain.h3c.src.index import H3cBatchProduct, H3cExamBatch, H3cRegistration
        from app.domain.plan.src.index import Plan

        matched = await db.scalar(
            select(ExamScheduleSubject.id)
            .join(
                H3cBatchProduct,
                H3cBatchProduct.product_id == ExamScheduleSubject.product_id,
            )
            .join(
                H3cRegistration,
                H3cRegistration.batch_product_id == H3cBatchProduct.id,
            )
            .where(
                ExamScheduleSubject.schedule_id == schedule_id,
                H3cRegistration.user_id == user_id,
                H3cRegistration.status.in_(APPROVED_STATUSES),
            )
            .limit(1)
        )
        if matched is not None:
            return True

        # 旧数据：无 batch_product_id，经 batch → plan.product_type 解析。
        legacy = await db.scalar(
            select(ExamScheduleSubject.id)
            .join(CertProduct, CertProduct.id == ExamScheduleSubject.product_id)
            .join(Plan, Plan.product_type == CertProduct.code)
            .join(H3cExamBatch, H3cExamBatch.plan_id == Plan.id)
            .join(H3cRegistration, H3cRegistration.batch_id == H3cExamBatch.id)
            .where(
                ExamScheduleSubject.schedule_id == schedule_id,
                H3cRegistration.user_id == user_id,
                H3cRegistration.status.in_(APPROVED_STATUSES),
                H3cRegistration.batch_product_id.is_(None),
            )
            .limit(1)
        )
        return legacy is not None

    from app.domain.order.src.index import Order
    from app.models.offline_payment import OfflinePaymentAudit

    refund_approved = select(OfflinePaymentAudit.id).where(
        OfflinePaymentAudit.order_id == Order.id,
        OfflinePaymentAudit.action == "admin.generic_refund_approved",
    ).exists()

    matched = await db.scalar(
        select(ExamScheduleSubject.id)
        .join(CertProduct, CertProduct.id == ExamScheduleSubject.product_id)
        .join(Order, Order.product_type == CertProduct.code)
        .where(
            ExamScheduleSubject.schedule_id == schedule_id,
            Order.user_id == user_id,
            Order.order_kind == "certification",
            Order.extra_data["business_type"].as_string() == "sangfor",
            Order.status.not_in(("closed", "refunded")),
            Order.extra_data["registration_state"].as_string().in_(APPROVED_STATUSES),
            ~refund_approved,
        )
        .limit(1)
    )
    return matched is not None
