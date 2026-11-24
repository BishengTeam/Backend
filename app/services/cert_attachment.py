"""认证附件读取：批次级 / 考场级 / 厂商级合并规则。"""
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.exam.src.index import CertAttachment


async def plan_belongs_to_vendor(db: AsyncSession, plan, vendor: str) -> bool:
    """判断批次是否属于指定厂商（兼容 H3C / 人社多产品批次）。"""
    from app.models.cert_product import CertProduct

    if plan.product_type:
        product = await db.scalar(
            select(CertProduct).where(CertProduct.code == plan.product_type)
        )
        return product is not None and product.type == vendor
    if vendor == "h3c":
        from app.domain.h3c.src.index import H3cBatchProduct, H3cExamBatch

        matched = await db.scalar(
            select(H3cBatchProduct.id)
            .join(H3cExamBatch, H3cExamBatch.id == H3cBatchProduct.batch_id)
            .join(CertProduct, CertProduct.id == H3cBatchProduct.product_id)
            .where(
                H3cExamBatch.plan_id == plan.id,
                CertProduct.type == "h3c",
            )
            .limit(1)
        )
        return matched is not None
    if vendor == "renshe":
        # 人社多项目批次 product_type 为空，项目归属落在 renshe_batch_project。
        from app.domain.renshe.src.index import RensheBatchProject

        matched = await db.scalar(
            select(RensheBatchProject.id)
            .join(CertProduct, CertProduct.id == RensheBatchProject.product_id)
            .where(
                RensheBatchProject.plan_id == plan.id,
                CertProduct.type == "renshe",
            )
            .limit(1)
        )
        return matched is not None
    return False


def attachment_payload(item: CertAttachment) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "file_url": item.file_url,
        "file_size": item.file_size,
        "sort_order": item.sort_order,
    }


def _dedupe(items: list[CertAttachment]) -> list[CertAttachment]:
    """同一 file_url 只保留排序靠前的一条。"""
    seen: set[str] = set()
    result: list[CertAttachment] = []
    for item in sorted(items, key=lambda row: (row.sort_order, row.id)):
        if item.file_url in seen:
            continue
        seen.add(item.file_url)
        result.append(item)
    return result


async def list_plan_attachments(
    db: AsyncSession, vendor: str, plan_id: int
) -> list[dict]:
    rows = (await db.scalars(
        select(CertAttachment).where(
            CertAttachment.vendor == vendor,
            CertAttachment.is_active.is_(True),
            or_(
                CertAttachment.plan_id == plan_id,
                and_(
                    CertAttachment.plan_id.is_(None),
                    CertAttachment.schedule_id.is_(None),
                ),
            ),
        )
    )).all()
    return [attachment_payload(item) for item in _dedupe(list(rows))]


async def list_schedule_attachments(
    db: AsyncSession, vendor: str, schedule_id: int
) -> list[dict]:
    rows = (await db.scalars(
        select(CertAttachment).where(
            CertAttachment.vendor == vendor,
            CertAttachment.is_active.is_(True),
            or_(
                CertAttachment.schedule_id == schedule_id,
                and_(
                    CertAttachment.plan_id.is_(None),
                    CertAttachment.schedule_id.is_(None),
                ),
            ),
        )
    )).all()
    return [attachment_payload(item) for item in _dedupe(list(rows))]
