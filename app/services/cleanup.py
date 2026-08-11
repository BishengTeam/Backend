import asyncio
import logging
import time

from sqlalchemy import delete, exists, select, func, text

from app.adapter.database import get_db_ctx
from app.domain.content.src.index import ActivityRegistration, Agreement, Ticket
from app.domain.community.src.index import Collection, Conversation, Share
from app.domain.certification.src.index import CompetitionReg, CourseEnrollment
from app.domain.order.src.index import InventoryRecord, Order, UserCoupon
from app.domain.renshe.src.index import RensheApplication
from app.domain.user.src.index import DeletedOpenid, PointsHistory, User, UserProfile, UserRealname, UserStudent, UserEnterprise, UserPoints

logger = logging.getLogger(__name__)

CLEANUP_DAYS = 30
CLEANUP_INTERVAL_SECONDS = 24 * 3600
ORDER_TIMEOUT_INTERVAL_SECONDS = 60


async def cleanup_loop():
    next_account_cleanup_at = 0.0
    while True:
        now = time.monotonic()
        if now >= next_account_cleanup_at:
            try:
                await _cleanup_expired_accounts()
            except Exception as exc:
                logger.error(
                    "定时清理账号失败: exception_type=%s", type(exc).__name__
                )
            next_account_cleanup_at = now + CLEANUP_INTERVAL_SECONDS
        try:
            await _close_expired_orders()
        except Exception as exc:
            logger.error(
                "定时关闭过期订单失败: exception_type=%s", type(exc).__name__
            )
        await asyncio.sleep(ORDER_TIMEOUT_INTERVAL_SECONDS)


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
                .where(
                    User.is_active == False,
                    User.updated_at < cutoff,
                    ~exists().where(RensheApplication.user_id == User.id),
                )
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

        # 2. CourseEnrollment may reference Order, so remove it first.
        await db.execute(
            delete(CourseEnrollment).where(CourseEnrollment.user_id.in_(user_ids))
        )

        # 3. 删除 Order
        await db.execute(delete(Order).where(Order.user_id.in_(user_ids)))

        # 4. 删除与 User 关联的叶子表
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
            Share,
        ]:
            await db.execute(delete(model).where(model.user_id.in_(user_ids)))

        # 5. 删除 User
        await db.execute(delete(User).where(User.id.in_(user_ids)))

        # 6. 删除 DeletedOpenid
        await db.execute(delete(DeletedOpenid).where(DeletedOpenid.openid.in_(openids)))

        await db.commit()
        logger.info("清理完成: 硬删除 %d 个过期账号", len(stale))
