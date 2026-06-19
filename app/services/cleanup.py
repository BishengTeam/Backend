import asyncio
import logging

from sqlalchemy import delete, select, func, text

from app.adapter.database import get_db_ctx
from app.domain.content.src.index import ActivityRegistration, Agreement, Ticket
from app.domain.community.src.index import Collection, Conversation, Share
from app.domain.certification.src.index import CompetitionReg, CourseEnrollment
from app.domain.order.src.index import InventoryRecord, Order, UserCoupon
from app.domain.user.src.index import DeletedOpenid, PointsHistory, User, UserProfile, UserRealname, UserStudent, UserEnterprise, UserPoints

logger = logging.getLogger(__name__)

CLEANUP_DAYS = 30
CLEANUP_INTERVAL_SECONDS = 24 * 3600


async def cleanup_loop():
    while True:
        try:
            await _cleanup_expired_accounts()
        except Exception:
            logger.exception("定时清理账号失败")
        try:
            await _close_expired_orders()
        except Exception:
            logger.exception("定时关闭过期订单失败")
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)


async def _close_expired_orders():
    from app.services.order_timeout import OrderTimeoutCloseService
    result = await OrderTimeoutCloseService().close_expired_pending_orders()
    if result.closed > 0:
        logger.info("关闭过期订单: %d 笔", result.closed)


async def _cleanup_expired_accounts():
    async with get_db_ctx() as db:
        cutoff = func.now() - text(f"INTERVAL '{CLEANUP_DAYS} days'")
        stale = (
            await db.execute(
                select(User.id, User.openid)
                .where(User.is_active == False, User.updated_at < cutoff)
            )
        ).all()
        if not stale:
            return
        user_ids = [row.id for row in stale]
        openids = [row.openid for row in stale]

        # 1. 删除 Order 的叶子表 (InventoryRecord)
        order_sub = select(Order.id).where(Order.user_id.in_(user_ids)).scalar_subquery()
        await db.execute(
            delete(InventoryRecord).where(InventoryRecord.order_id.in_(order_sub))
        )

        # 2. 删除 Order
        await db.execute(delete(Order).where(Order.user_id.in_(user_ids)))

        # 3. 删除与 User 关联的叶子表
        for model in [
            PointsHistory,
            UserPoints,
            UserRealname,
            UserStudent,
            UserEnterprise,
            UserProfile,
            UserCoupon,
            Collection,
            Conversation,
            Ticket,
            Agreement,
            ActivityRegistration,
            CompetitionReg,
            CourseEnrollment,
            Share,
        ]:
            await db.execute(delete(model).where(model.user_id.in_(user_ids)))

        # 4. 删除 User
        await db.execute(delete(User).where(User.id.in_(user_ids)))

        # 5. 删除 DeletedOpenid
        await db.execute(delete(DeletedOpenid).where(DeletedOpenid.openid.in_(openids)))

        await db.commit()
        logger.info("清理完成: 硬删除 %d 个过期账号", len(stale))
