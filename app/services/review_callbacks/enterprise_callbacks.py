"""企业信息审核回调 — 薄封装层，调用 AdminUserService"""

from app.schemas.admin import AdminIdentityReview
from app.services.admin_user import AdminUserService


async def approve(target_id: int, comment: str | None) -> None:
    """企业审核通过"""
    await AdminUserService().review_enterprise(
        user_id=target_id,
        data=AdminIdentityReview(status="verified", comment=comment),
    )


async def reject(target_id: int, comment: str | None) -> None:
    """企业审核驳回"""
    await AdminUserService().review_enterprise(
        user_id=target_id,
        data=AdminIdentityReview(status="rejected", comment=comment),
    )
