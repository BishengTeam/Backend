"""Identity and product gates shared by dedicated certification flows."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.certification.src.index import Certification
from app.domain.h3c.src.index import H3cExamBatch
from app.domain.nisp.src.index import NispExamBatch
from app.domain.plan.src.index import Plan
from app.domain.user.src.index import UserRealname
from app.models.cert_product import CertProduct
from app.port.exceptions import (
    BusinessException,
    ConflictException,
    DedicatedCertificationOrderRequiredException,
)


async def lock_verified_identity(
    db: AsyncSession, *, user_id: int
) -> UserRealname:
    identity = (
        await db.scalar(
            select(UserRealname)
            .where(
                UserRealname.user_id == user_id,
                UserRealname.status == "verified",
            )
            .with_for_update()
        )
    )
    if identity is None:
        raise BusinessException("请先完成实名认证")
    return identity


def assert_submitted_identity(
    identity: UserRealname, *, submitted_name: str, submitted_id_card: str
) -> None:
    name_matched = submitted_name.strip() == identity.real_name.strip()
    id_card_matched = submitted_id_card.strip().upper() == identity.id_card_number.upper()
    if not name_matched or not id_card_matched:
        raise ConflictException("报名人信息与已实名认证本人不一致，请先完成实名纠错")


def verified_identity_snapshot(identity: UserRealname) -> dict:
    """Freeze the verified identity basis used by a registration submission."""
    return {
        "source_table": "user_realname",
        "user_id": identity.user_id,
        "status": identity.status,
        "verified_at": identity.verified_at,
        "id_card_hash": identity.id_card_hash,
        "real_name": identity.real_name,
        "id_card_number": identity.id_card_number,
        "last_name_zh": identity.last_name_zh,
        "first_name_zh": identity.first_name_zh,
        "last_name_en": identity.last_name_en,
        "first_name_en": identity.first_name_en,
        "gender": identity.gender,
        "age": identity.age,
        "birth_date": identity.birth_date,
        "zip_code": identity.zip_code,
    }


async def resolve_dedicated_certification_vendor(
    db: AsyncSession, *, product_type: str
) -> str | None:
    """Return H3C/NISP when a product is owned by a dedicated certification flow."""
    normalized = product_type.strip().upper()
    if normalized.startswith("NISP-"):
        return "NISP"

    product = await db.scalar(
        select(CertProduct).where(CertProduct.code == product_type)
    )
    if product is not None:
        if product.type == "h3c":
            return "H3C"
        if product.type == "nisp":
            return "NISP"
    else:
        legacy = await db.scalar(
            select(Certification).where(Certification.code == product_type)
        )
        if legacy is not None:
            vendor = legacy.vendor.strip().upper()
            if vendor == "H3C":
                return "H3C"
            if vendor == "NISP":
                return "NISP"

    linked_h3c = await db.scalar(
        select(H3cExamBatch.id)
        .join(Plan, Plan.id == H3cExamBatch.plan_id)
        .where(Plan.product_type == product_type)
        .limit(1)
    )
    if linked_h3c is not None:
        return "H3C"
    linked_nisp = await db.scalar(
        select(NispExamBatch.id)
        .join(Plan, Plan.id == NispExamBatch.plan_id)
        .where(Plan.product_type == product_type)
        .limit(1)
    )
    if linked_nisp is not None:
        return "NISP"
    return None


async def reject_dedicated_certification_order(
    db: AsyncSession, *, product_type: str
) -> None:
    vendor = await resolve_dedicated_certification_vendor(
        db, product_type=product_type
    )
    if vendor is not None:
        raise DedicatedCertificationOrderRequiredException(vendor)


async def validate_nisp_batch_product(
    db: AsyncSession,
    *,
    plan: Plan,
    level: str,
    require_active: bool = True,
) -> CertProduct:
    """Ensure a NISP batch belongs to the active product for its level."""
    product = await db.scalar(
        select(CertProduct)
        .where(CertProduct.code == plan.product_type)
        .with_for_update()
    )
    expected_code = f"NISP-{level}"
    if product is None or product.type != "nisp" or product.code != expected_code:
        raise BusinessException("NISP 批次必须关联对应级别的上架 NISP 认证产品")
    if require_active and not product.is_active:
        raise BusinessException("NISP 认证产品已下架，不能继续报名")
    return product
