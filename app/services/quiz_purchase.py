"""题库单独购买：创建订单 + 支付成功后发放 QuizLibraryEntitlement。"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.adapter.database import get_db_ctx
from app.domain.community.src.index import QuizLibrary, QuizLibraryEntitlement
from app.domain.order.src.index import (
    ORDER_PAYMENT_EXPIRE_MINUTES,
    Order,
    close_expired_pending_order,
)
from app.port.exceptions import BusinessException, ConflictException, NotFoundException
from app.utils.payment import generate_out_trade_no


class QuizPurchaseService:

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    async def purchase(self, user_id: int, library_id: int) -> dict:
        """购买题库：创建 pending_payment 订单"""
        async with get_db_ctx() as db:
            async with db.begin():
                library = await db.get(QuizLibrary, library_id)
                if library is None:
                    raise NotFoundException("题库")
                if library.status != "published":
                    raise BusinessException("题库未上架或已下架")
                if library.access_mode != "paid":
                    raise BusinessException("该题库不支持单独购买")
                if library.price_cents <= 0:
                    raise BusinessException("该题库价格未配置")

                # 已有有效授权
                existing = (
                    await db.execute(
                        select(QuizLibraryEntitlement).where(
                            QuizLibraryEntitlement.user_id == user_id,
                            QuizLibraryEntitlement.library_id == library_id,
                            QuizLibraryEntitlement.status == "active",
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    raise ConflictException("您已拥有该题库的访问权限")

                # 复用未过期的待支付订单
                pending = (
                    await db.execute(
                        select(Order).where(
                            Order.user_id == user_id,
                            Order.order_kind == "quiz_order",
                            Order.product_type == f"quiz_library:{library_id}",
                            Order.status == "pending",
                        )
                    )
                ).scalar_one_or_none()
                if pending is not None:
                    if pending.expires_at and pending.expires_at <= self._now():
                        await close_expired_pending_order(db, pending)
                    else:
                        return {
                            "library_id": library_id,
                            "order_id": pending.id,
                            "status": "pending_payment",
                            "price_cents": pending.price,
                        }

                now = self._now()
                order = Order(
                    user_id=user_id,
                    order_kind="quiz_order",
                    product_type=f"quiz_library:{library_id}",
                    price=library.price_cents,
                    status="pending",
                    out_trade_no=generate_out_trade_no("QUIZ"),
                    expires_at=now + timedelta(minutes=ORDER_PAYMENT_EXPIRE_MINUTES),
                )
                db.add(order)
                await db.flush()

                return {
                    "library_id": library_id,
                    "order_id": order.id,
                    "status": "pending_payment",
                    "price_cents": library.price_cents,
                }

    @staticmethod
    async def fulfill(db, order: Order) -> QuizLibraryEntitlement:
        """支付回调：发放题库授权（幂等）。"""
        # 从 product_type 解析 library_id
        _, _, library_id_str = order.product_type.partition(":")
        library_id = int(library_id_str) if library_id_str else 0

        # 幂等：同一订单+题库只发一次
        existing = (
            await db.execute(
                select(QuizLibraryEntitlement).where(
                    QuizLibraryEntitlement.order_id == order.id,
                    QuizLibraryEntitlement.library_id == library_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        entitlement = QuizLibraryEntitlement(
            user_id=order.user_id,
            library_id=library_id,
            order_id=order.id,
            source_type="quiz_order",
            status="active",
        )
        db.add(entitlement)
        await db.flush()
        return entitlement
