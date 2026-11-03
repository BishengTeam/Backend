from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapter.database import get_db_ctx
from app.port.exceptions import BusinessException, NotFoundException
from app.utils.quiz_helpers import today
from app.domain.user.src.index import PointsBatch, PointsHistory, User, UserPoints
from app.domain.content.src.index import Activity, ActivityRegistration
from app.schemas.common import PaginatedData
from app.schemas.points import (
    PointsBalanceResponse,
    PointsClaimRequest,
    PointsClaimResponse,
    PointsHistoryResponse,
    PointsRedeemRequest,
    PointsRedeemResponse,
    PointsRedeemType,
)
from app.services.points_rules import POINTS_VALIDITY_DAYS

CLAIM_SCENE_POINTS: dict[str, int] = {
    "daily_checkin": 1,
    "activity": 10,
}

logger = logging.getLogger(__name__)


def batch_expiry(now: datetime | None = None) -> datetime:
    """批次过期时间：自获得起 12 个月（365 天，口径见 points_rules）。"""
    return (now or datetime.now(timezone.utc)) + timedelta(days=POINTS_VALIDITY_DAYS)


class PointsService:
    async def get_balance(self, user_id: int) -> PointsBalanceResponse:
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            account = await self._get_or_create_account_for_update(db, user_id)
            await db.commit()
            return PointsBalanceResponse(balance=account.balance)

    async def list_history(
        self,
        user_id: int,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedData[PointsHistoryResponse]:
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            base = select(PointsHistory).where(PointsHistory.user_id == user_id)
            total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
            records = (
                await db.execute(
                    base.order_by(PointsHistory.created_at.desc(), PointsHistory.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()

        return PaginatedData[PointsHistoryResponse](
            items=[PointsHistoryResponse.model_validate(record) for record in records],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def grant_points(
        self,
        user_id: int,
        *,
        amount: int,
        action_type: str,
        description: str | None = None,
        source_type: str | None = None,
        source_id: str | None = None,
    ) -> PointsHistory:
        if amount <= 0:
            raise BusinessException("发放积分必须大于 0")

        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            history, _ = await self._grant_points_in_session(
                db,
                user_id=user_id,
                amount=amount,
                action_type=action_type,
                description=description,
                source_type=source_type,
                source_id=source_id,
            )
            await db.commit()
            await db.refresh(history)
            return history

    async def claim_points(
        self,
        user_id: int,
        data: PointsClaimRequest,
    ) -> PointsClaimResponse:
        amount = CLAIM_SCENE_POINTS[data.scene]
        source_id = self._claim_source_id(data.scene, data.source_id, user_id=user_id)
        action_type = f"claim_{data.scene}"
        description = data.description or f"{data.scene} points claim"

        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            activity = None
            if data.scene == "activity":
                activity = await self._validate_activity_claim(
                    db, user_id=user_id, source_id=data.source_id
                )
            history, claimed = await self._grant_points_in_session(
                db,
                user_id=user_id,
                amount=amount,
                action_type=action_type,
                description=description,
                source_type="points_claim",
                source_id=source_id,
            )
            await db.commit()
            await db.refresh(history)
        return PointsClaimResponse(
            claimed=claimed,
            scene=data.scene,
            amount=history.amount,
            balance=history.balance_after,
            history_id=history.id,
        )

    async def grant_milestones_in_session(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        action_type: str,
        amount: int,
        milestones: tuple[int, ...],
        source_prefix: str,
        describe_milestone: Callable[[int], str],
    ) -> None:
        """结算里程碑奖励：逐档按幂等键补发，SAVEPOINT 隔离，失败只记日志不阻断业务。

        调用方可重复触发（每次业务动作都全量结算），已发过的档位由
        _grant_points_in_session 的 source 去重自动跳过。
        """
        for milestone in milestones:
            try:
                async with db.begin_nested():
                    await self._grant_points_in_session(
                        db,
                        user_id=user_id,
                        amount=amount,
                        action_type=action_type,
                        description=describe_milestone(milestone),
                        source_type="points_claim",
                        source_id=f"{source_prefix}:{user_id}:{milestone}",
                    )
            except Exception:
                logger.warning(
                    "points milestone grant failed: user_id=%s rule=%s milestone=%s",
                    user_id,
                    action_type,
                    milestone,
                    exc_info=True,
                )

    @staticmethod
    async def _validate_activity_claim(
        db: AsyncSession, *, user_id: int, source_id: str | None
    ) -> Activity:
        if source_id is None or not source_id.isdecimal():
            raise BusinessException("活动积分必须使用服务端活动 ID")
        activity_id = int(source_id)
        activity = await db.get(Activity, activity_id)
        if activity is None or not activity.is_active:
            raise BusinessException("活动不存在或已下线")
        registration = (
            await db.execute(
                select(ActivityRegistration.id)
                .where(
                    ActivityRegistration.activity_id == activity_id,
                    ActivityRegistration.user_id == user_id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if registration is None:
            raise BusinessException("未报名该活动，不能领取奖励")
        now = datetime.now(timezone.utc)
        if activity.end_time is None:
            raise BusinessException("活动未配置结束时间，暂不能领取奖励")
        if now < activity.end_time:
            raise BusinessException("活动尚未结束，不能领取奖励")
        return activity

    async def redeem_points(
        self,
        user_id: int,
        data: PointsRedeemRequest,
    ) -> PointsRedeemResponse:
        action_type = self._redeem_action_type(data.redeem_type)
        description = data.description or f"{data.redeem_type} points redeem"

        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            # 与商城兑换共用 FIFO 扣减；沿用旧行为不带幂等键（允许重复兑换）。
            history = await self._debit_points_in_session(
                db,
                user_id=user_id,
                amount=data.amount,
                action_type=action_type,
                description=description,
                source_type=None,
                source_id=None,
            )
            await db.commit()
            await db.refresh(history)

        return PointsRedeemResponse(
            redeem_type=data.redeem_type,
            amount=data.amount,
            balance=history.balance_after,
            history_id=history.id,
        )

    async def settle_expired_points(self) -> int:
        """清零所有已过期批次的剩余积分，逐用户事务结算；返回清零总积分。

        幂等性靠批次状态迁移保证（remaining>0 → 0，行锁串行化），
        重复触发只会找到空集。
        """
        async with get_db_ctx() as db:
            rows = (
                await db.execute(
                    select(PointsBatch.user_id, func.sum(PointsBatch.remaining).label("expired"))
                    .where(
                        PointsBatch.remaining > 0,
                        PointsBatch.expires_at <= func.now(),
                    )
                    .group_by(PointsBatch.user_id)
                )
            ).all()
            # 聚合是只读快照：先结束它开启的自动事务，再逐用户开独立短事务。
            await db.rollback()
            total = 0
            for user_id, _expired in rows:
                async with db.begin():
                    total += await self._expire_user_batches(db, user_id=user_id)
        return total

    async def _expire_user_batches(self, db: AsyncSession, *, user_id: int) -> int:
        # 先锁账户（与发放/扣减路径一致的加锁顺序，避免死锁），再核销批次。
        account = await self._get_or_create_account_for_update(db, user_id)
        batches = (
            await db.execute(
                select(PointsBatch)
                .where(
                    PointsBatch.user_id == user_id,
                    PointsBatch.remaining > 0,
                    PointsBatch.expires_at <= func.now(),
                )
                .with_for_update()
            )
        ).scalars().all()
        if not batches:
            return 0
        expired_total = sum(batch.remaining for batch in batches)
        for batch in batches:
            batch.remaining = 0
        account.balance -= expired_total
        history = PointsHistory(
            user_id=user_id,
            action_type="points_expire",
            amount=-expired_total,
            balance_after=account.balance,
            description="积分到期清零",
            source_type="points_expire",
            source_id=None,
        )
        db.add(history)
        await db.flush()
        return expired_total

    async def _debit_points_in_session(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        amount: int,
        action_type: str,
        description: str,
        source_type: str | None,
        source_id: str | None,
        allow_negative: bool = False,
    ) -> PointsHistory:
        """会话内扣减积分：批次按先进先出核销，账户余额同步减少。

        allow_negative=False（兑换/商城/手工扣分）：余额不足直接拒绝。
        allow_negative=True（退款追扣）：批次与余额不足时扣至负数，
        欠分不过期，后续获得先补亏空；兑换凭余额校验自然被拦截。
        """
        if amount <= 0:
            raise BusinessException("消耗积分必须大于 0")
        existing = await self._get_source_history(
            db,
            user_id=user_id,
            action_type=action_type,
            source_type=source_type,
            source_id=source_id,
        )
        if existing is not None:
            return existing
        account = await self._get_or_create_account_for_update(db, user_id)
        if not allow_negative and account.balance < amount:
            raise BusinessException("积分余额不足")
        await self._consume_batches_fifo(db, user_id=user_id, amount=amount)
        account.balance -= amount
        history = PointsHistory(
            user_id=user_id,
            action_type=action_type,
            amount=-amount,
            balance_after=account.balance,
            description=description,
            source_type=source_type,
            source_id=source_id,
        )
        db.add(history)
        await db.flush()
        return history

    async def _consume_batches_fifo(self, db: AsyncSession, *, user_id: int, amount: int) -> None:
        """按过期时间从早到晚核销批次；批次不足以覆盖时由欠分（负余额）承接。"""
        remaining = amount
        batches = (
            await db.execute(
                select(PointsBatch)
                .where(PointsBatch.user_id == user_id, PointsBatch.remaining > 0)
                .order_by(PointsBatch.expires_at.asc(), PointsBatch.id.asc())
                .with_for_update()
            )
        ).scalars().all()
        for batch in batches:
            if remaining <= 0:
                break
            used = min(batch.remaining, remaining)
            batch.remaining -= used
            remaining -= used

    async def _grant_points_in_session(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        amount: int,
        action_type: str,
        description: str | None,
        source_type: str | None,
        source_id: str | None,
    ) -> tuple[PointsHistory, bool]:
        account = await self._get_or_create_account_for_update(db, user_id)
        existing = await self._get_source_history(
            db,
            user_id=user_id,
            action_type=action_type,
            source_type=source_type,
            source_id=source_id,
        )
        if existing is not None:
            return existing, False

        account.balance += amount
        history = PointsHistory(
            user_id=user_id,
            action_type=action_type,
            amount=amount,
            balance_after=account.balance,
            description=description,
            source_type=source_type,
            source_id=source_id,
        )
        db.add(history)
        db.add(PointsBatch(
            user_id=user_id,
            amount=amount,
            remaining=amount,
            expires_at=batch_expiry(),
            action_type=action_type,
            description=description,
            source_type=source_type,
            source_id=source_id,
        ))
        await db.flush()
        return history, True

    async def _get_or_create_account_for_update(
        self,
        db: AsyncSession,
        user_id: int,
    ) -> UserPoints:
        await db.execute(
            text(
                """
                INSERT INTO user_points (user_id)
                VALUES (:user_id)
                ON CONFLICT (user_id) DO NOTHING
                """
            ),
            {"user_id": user_id},
        )
        account = (
            await db.execute(
                select(UserPoints)
                .where(UserPoints.user_id == user_id)
                .with_for_update()
            )
        ).scalar_one()
        return account

    async def _get_source_history(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        action_type: str,
        source_type: str | None,
        source_id: str | None,
    ) -> PointsHistory | None:
        if source_type is None or source_id is None:
            return None
        return (
            await db.execute(
                select(PointsHistory).where(
                    PointsHistory.user_id == user_id,
                    PointsHistory.action_type == action_type,
                    PointsHistory.source_type == source_type,
                    PointsHistory.source_id == source_id,
                )
            )
        ).scalar_one_or_none()

    async def _require_user(self, db: AsyncSession, user_id: int) -> User:
        user = await db.get(User, user_id)
        if user is None or not user.is_active:
            raise NotFoundException("用户")
        return user

    @staticmethod
    def _claim_source_id(scene: str, source_id: str | None, *, user_id: int) -> str:
        if scene == "daily_checkin":
            # 必须与 quiz_practice 自动打卡发分的 source_id 格式完全一致（按用户 + 业务时区自然日），
            # 否则同一天自动打卡与手动领取会绕过幂等检查重复发放积分。
            return f"{scene}:{user_id}:{today().isoformat()}"
        if scene == "activity":
            if not source_id:
                raise BusinessException("activity 场景必须提供 source_id")
            return f"activity:{source_id}"
        raise BusinessException("不支持的积分领取场景")

    @staticmethod
    def _redeem_action_type(redeem_type: PointsRedeemType) -> str:
        return f"redeem_{redeem_type}"
