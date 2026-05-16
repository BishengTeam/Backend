import asyncio
import logging

from sqlalchemy import delete, select, func

from app.core.database import get_db_ctx
from app.models.deleted_openid import DeletedOpenid
from app.models.order import Order
from app.models.user import User

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
        cutoff = func.now() - func.make_interval(days=CLEANUP_DAYS)
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
        await db.execute(delete(Order).where(Order.user_id.in_(user_ids)))
        await db.execute(delete(User).where(User.id.in_(user_ids)))
        await db.execute(delete(DeletedOpenid).where(DeletedOpenid.openid.in_(openids)))
        await db.commit()
        logger.info("清理完成: 硬删除 %d 个过期账号", len(stale))
