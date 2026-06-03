import asyncio
import logging

from sqlalchemy import delete, select, func, text

from app.core.database import get_db_ctx
from app.models.activity import ActivityRegistration
from app.models.agreement import Agreement
from app.models.collection import Collection
from app.models.competition import CompetitionReg
from app.models.conversation import Conversation
from app.models.coupon import UserCoupon
from app.models.course import CourseEnrollment
from app.models.deleted_openid import DeletedOpenid
from app.models.inventory import InventoryRecord
from app.models.order import Order
from app.models.points import PointsHistory, UserPoints
from app.models.share import Share
from app.models.ticket import Ticket
from app.models.user import User
from app.models.user_identity import UserIdentity

logger = logging.getLogger(__name__)

CLEANUP_DAYS = 30
CLEANUP_INTERVAL_SECONDS = 24 * 3600


async def cleanup_loop():
    while True:
        try:
            await _cleanup_expired_accounts()
        except Exception:
            logger.exception("定时清理账号失败")
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)


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
            UserIdentity,
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
