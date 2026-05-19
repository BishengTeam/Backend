from __future__ import annotations

from datetime import date

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_ctx
from app.core.exceptions import BusinessException, NotFoundException
from app.models.points import PointsHistory, UserPoints
from app.models.user import User
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

CLAIM_SCENE_POINTS: dict[str, int] = {
    "daily_checkin": 5,
    "quiz_task": 10,
    "new_user": 20,
    "activity": 10,
}


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
        source_id = self._claim_source_id(data.scene, data.source_id)
        action_type = f"claim_{data.scene}"
        description = data.description or f"{data.scene} points claim"

        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
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

    async def redeem_points(
        self,
        user_id: int,
        data: PointsRedeemRequest,
    ) -> PointsRedeemResponse:
        action_type = self._redeem_action_type(data.redeem_type)
        description = data.description or f"{data.redeem_type} points redeem"

        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            account = await self._get_or_create_account_for_update(db, user_id)
            if account.balance < data.amount:
                raise BusinessException("积分余额不足")

            account.balance -= data.amount
            history = PointsHistory(
                user_id=user_id,
                action_type=action_type,
                amount=-data.amount,
                balance_after=account.balance,
                description=description,
                source_type=None,
                source_id=str(data.target_id) if data.target_id is not None else None,
            )
            db.add(history)
            await db.commit()
            await db.refresh(history)

        return PointsRedeemResponse(
            redeem_type=data.redeem_type,
            amount=data.amount,
            balance=history.balance_after,
            history_id=history.id,
        )

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
    def _claim_source_id(scene: str, source_id: str | None) -> str:
        if scene in {"daily_checkin", "quiz_task"}:
            return f"{scene}:{date.today().isoformat()}"
        if scene == "new_user":
            return "new_user:once"
        if scene == "activity":
            if not source_id:
                raise BusinessException("activity 场景必须提供 source_id")
            return f"activity:{source_id}"
        raise BusinessException("不支持的积分领取场景")

    @staticmethod
    def _redeem_action_type(redeem_type: PointsRedeemType) -> str:
        return f"redeem_{redeem_type}"
