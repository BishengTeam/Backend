"""Transactional points redemption for virtual content entitlements."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapter.database import get_db_ctx
from app.domain.certification.src.index import Course, CourseEnrollment
from app.domain.community.src.index import QuizLibrary, QuizLibraryEntitlement
from app.domain.points_mall.src import (
    PointsVirtualOrder,
    PointsVirtualProduct,
    PointsVirtualRefund,
)
from app.domain.user.src.index import (
    AdminUser,
    PointsHistory,
    User,
    UserPoints,
    UserProfile,
)
from app.port.exceptions import BusinessException, ConflictException, NotFoundException
from app.schemas.common import PaginatedData
from app.schemas.points_virtual import (
    PointsVirtualPrecheckRequest,
    PointsVirtualPrecheckResponse,
    PointsVirtualProductCreate,
    PointsVirtualProductResponse,
    PointsVirtualProductUpdate,
    PointsVirtualOrderResponse,
    PointsVirtualOrderDetailResponse,
    PointsHistoryBriefResponse,
    PointsVirtualRedeemRequest,
    PointsVirtualRedeemResponse,
    PointsVirtualRefundRequest,
    PointsVirtualRefundResponse,
)
from app.services.course_entitlement import CourseEntitlementService
from app.services.points import PointsService
from app.utils.payment import generate_out_trade_no


ACTIVE_COURSE_STATUSES = ("pending_payment", "enrolled", "completed")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PointsVirtualService:
    # ── Admin: products ──

    async def admin_create_product(
        self, data: PointsVirtualProductCreate
    ) -> PointsVirtualProductResponse:
        async with get_db_ctx() as db:
            async with db.begin():
                content = await self._locked_content(
                    db,
                    product_type=data.product_type,
                    course_id=data.course_id,
                    library_id=data.library_id,
                )
                content_key = (
                    f"course:{data.course_id}"
                    if data.product_type == "course"
                    else f"quiz_library:{data.library_id}"
                )
                existing = await db.scalar(
                    select(PointsVirtualProduct.id).where(
                        PointsVirtualProduct.content_key == content_key
                    )
                )
                if existing is not None:
                    raise ConflictException("该内容已配置积分商品")
                product = PointsVirtualProduct(
                    **data.model_dump(),
                    content_key=content_key,
                    content_snapshot=self._content_snapshot(data.product_type, content),
                    version=1,
                )
                db.add(product)
                await db.flush()
                return (await self._product_responses(db, [product]))[0]

    async def admin_update_product(
        self, product_id: int, data: PointsVirtualProductUpdate
    ) -> PointsVirtualProductResponse:
        async with get_db_ctx() as db:
            async with db.begin():
                product = await db.scalar(
                    select(PointsVirtualProduct)
                    .where(PointsVirtualProduct.id == product_id)
                    .with_for_update()
                )
                if product is None:
                    raise NotFoundException("积分商品")
                updates = data.model_dump(exclude_unset=True)
                if "stock_limit" in updates and updates["stock_limit"] is not None:
                    if updates["stock_limit"] < product.total_redeemed:
                        raise BusinessException("库存不能小于已兑换数量")
                for key, value in updates.items():
                    setattr(product, key, value)
                if updates:
                    product.version += 1
                await db.flush()
                return (await self._product_responses(db, [product]))[0]

    async def admin_list_products(
        self,
        *,
        status: str | None = None,
        product_type: str | None = None,
        keyword: str | None = None,
        availability: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedData[PointsVirtualProductResponse]:
        async with get_db_ctx() as db:
            base = select(PointsVirtualProduct)
            if status:
                base = base.where(PointsVirtualProduct.status == status)
            if product_type:
                base = base.where(PointsVirtualProduct.product_type == product_type)
            if keyword:
                pattern = f"%{keyword.strip()}%"
                base = base.where(
                    or_(
                        PointsVirtualProduct.name.ilike(pattern),
                        PointsVirtualProduct.content_key.ilike(pattern),
                    )
                )
            now = _now()
            if availability == "not_started":
                base = base.where(PointsVirtualProduct.available_from > now)
            elif availability == "ended":
                base = base.where(
                    PointsVirtualProduct.available_to.is_not(None),
                    PointsVirtualProduct.available_to <= now,
                )
            elif availability == "sold_out":
                base = base.where(
                    PointsVirtualProduct.stock_limit.is_not(None),
                    PointsVirtualProduct.total_redeemed
                    >= PointsVirtualProduct.stock_limit,
                )
            total = await db.scalar(
                select(func.count()).select_from(base.subquery())
            ) or 0
            rows = (
                await db.execute(
                    base.order_by(PointsVirtualProduct.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            return PaginatedData(
                items=await self._product_responses(db, rows),
                total=int(total),
                page=page,
                page_size=page_size,
            )

    async def admin_get_product(
        self, product_id: int
    ) -> PointsVirtualProductResponse:
        async with get_db_ctx() as db:
            product = await db.get(PointsVirtualProduct, product_id)
            if product is None:
                raise NotFoundException("积分商品")
            return (await self._product_responses(db, [product]))[0]

    async def admin_list_orders(
        self,
        *,
        user_id: int | None = None,
        status: str | None = None,
        keyword: str | None = None,
        product_id: int | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ):
        from app.schemas.points_virtual import PointsVirtualOrderResponse

        async with get_db_ctx() as db:
            base = (
                select(PointsVirtualOrder)
                .outerjoin(
                    UserProfile,
                    UserProfile.user_id == PointsVirtualOrder.user_id,
                )
                .outerjoin(User, User.id == PointsVirtualOrder.user_id)
            )
            if user_id is not None:
                base = base.where(PointsVirtualOrder.user_id == user_id)
            if status:
                base = base.where(PointsVirtualOrder.status == status)
            if product_id is not None:
                base = base.where(PointsVirtualOrder.product_id == product_id)
            if keyword:
                pattern = f"%{keyword.strip()}%"
                keyword_conditions = [
                    PointsVirtualOrder.order_no.ilike(pattern),
                    UserProfile.nickname.ilike(pattern),
                    User.phone.ilike(pattern),
                ]
                # User IDs are small integers in normal administration input.
                # An 11-digit phone number must stay as a string and must not
                # be sent to asyncpg as an int32 query parameter.
                if len(keyword.strip()) <= 9 and keyword.strip().isdecimal():
                    keyword_conditions.append(
                        PointsVirtualOrder.user_id == int(keyword.strip())
                    )
                base = base.where(or_(*keyword_conditions))
            if created_from:
                base = base.where(PointsVirtualOrder.created_at >= created_from)
            if created_to:
                base = base.where(PointsVirtualOrder.created_at < created_to)
            total = await db.scalar(
                select(func.count()).select_from(base.subquery())
            ) or 0
            rows = (
                await db.execute(
                    base.order_by(PointsVirtualOrder.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            user_ids = [row.user_id for row in rows]
            profiles = (
                (
                    await db.execute(
                        select(UserProfile).where(UserProfile.user_id.in_(user_ids))
                    )
                )
                .scalars()
                .all()
                if user_ids
                else []
            )
            profile_map = {row.user_id: row for row in profiles}
            users = (
                (
                    await db.execute(
                        select(User.id, User.phone).where(User.id.in_(user_ids))
                    )
                ).all()
                if user_ids
                else []
            )
            user_phone_map = {row.id: row.phone for row in users}
            items = []
            for row in rows:
                payload = PointsVirtualOrderResponse.model_validate(row)
                profile = profile_map.get(row.user_id)
                payload.user_nickname = profile.nickname if profile else None
                payload.user_phone = (
                    self._mask_phone(
                        profile.phone if profile else user_phone_map.get(row.user_id)
                    )
                )
                items.append(payload)
            return PaginatedData(
                items=items,
                total=int(total),
                page=page,
                page_size=page_size,
            )

    async def admin_get_order(self, order_id: int) -> PointsVirtualOrderDetailResponse:
        async with get_db_ctx() as db:
            order = await db.get(PointsVirtualOrder, order_id)
            if order is None:
                raise NotFoundException("积分兑换订单")
            profile = await db.scalar(
                select(UserProfile).where(UserProfile.user_id == order.user_id)
            )
            user = await db.get(User, order.user_id)
            payload = PointsVirtualOrderDetailResponse.model_validate(order)
            payload.user_nickname = profile.nickname if profile else None
            payload.user_phone = self._mask_phone(
                profile.phone if profile else user.phone if user else None
            )
            payload.spend_history = (
                PointsHistoryBriefResponse.model_validate(history)
                if order.points_history_id
                and (history := await db.get(PointsHistory, order.points_history_id))
                else None
            )
            refund = await db.scalar(
                select(PointsVirtualRefund).where(
                    PointsVirtualRefund.order_id == order.id
                )
            )
            if refund is not None:
                payload.refund = PointsVirtualRefundResponse.model_validate(refund)
                refund_history = await db.get(
                    PointsHistory, refund.points_history_id
                )
                payload.refund_history = (
                    PointsHistoryBriefResponse.model_validate(refund_history)
                    if refund_history
                    else None
                )
                admin = await db.get(AdminUser, refund.refunded_by_admin_id)
                payload.refunded_by_admin_name = admin.display_name if admin else None
            return payload

    # ── User: catalog and redemption ──

    async def list_products_for_user(
        self, user_id: int, *, page: int = 1, page_size: int = 20
    ) -> PaginatedData[PointsVirtualProductResponse]:
        async with get_db_ctx() as db:
            base = select(PointsVirtualProduct).where(
                PointsVirtualProduct.status == "published"
            )
            total = await db.scalar(
                select(func.count()).select_from(base.subquery())
            ) or 0
            rows = (
                await db.execute(
                    base.order_by(PointsVirtualProduct.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            return PaginatedData(
                items=await self._product_responses(db, rows),
                total=int(total),
                page=page,
                page_size=page_size,
            )

    async def precheck(
        self, user_id: int, data: PointsVirtualPrecheckRequest
    ) -> PointsVirtualPrecheckResponse:
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            product = await db.get(PointsVirtualProduct, data.product_id)
            if product is None:
                raise NotFoundException("积分商品")
            balance = (
                await db.scalar(
                    select(UserPoints.balance).where(UserPoints.user_id == user_id)
                )
            ) or 0
            already_owned = await self._has_active_entitlement(
                db, user_id=user_id, product=product, now=_now()
            )
            blocked_reason = await self._blocked_reason(
                db,
                user_id=user_id,
                product=product,
                now=_now(),
                check_balance=True,
                balance=balance,
            )
            return PointsVirtualPrecheckResponse(
                product=(await self._product_responses(db, [product]))[0],
                balance=balance,
                balance_after=(
                    balance - product.points_price
                    if blocked_reason is None and not already_owned
                    else None
                ),
                points_shortage=(
                    max(0, product.points_price - balance)
                    if balance < product.points_price
                    else None
                ),
                can_redeem=blocked_reason is None and not already_owned,
                blocked_reason=blocked_reason,
                already_owned=already_owned,
            )

    async def redeem(
        self, user_id: int, data: PointsVirtualRedeemRequest
    ) -> PointsVirtualRedeemResponse:
        async with get_db_ctx() as db:
            async with db.begin():
                # A user-level lock serializes concurrent redemptions and keeps
                # the same account from overdrafting across different products.
                await self._lock_user(db, user_id)

                existing = await db.scalar(
                    select(PointsVirtualOrder)
                    .where(
                        PointsVirtualOrder.user_id == user_id,
                        PointsVirtualOrder.idempotency_key == data.idempotency_key,
                    )
                    .with_for_update()
                )
                fingerprint = self._fingerprint(
                    product_id=data.product_id,
                    product_version=data.product_version,
                )
                if existing is not None:
                    if existing.request_fingerprint != fingerprint:
                        raise ConflictException("同一幂等请求的商品版本不一致")
                    balance = await db.scalar(
                        select(UserPoints.balance).where(UserPoints.user_id == user_id)
                    ) or 0
                    return self._redeem_response(existing, balance=balance, idempotent=True)

                product = await db.scalar(
                    select(PointsVirtualProduct)
                    .where(PointsVirtualProduct.id == data.product_id)
                    .with_for_update()
                )
                if product is None:
                    raise NotFoundException("积分商品")
                now = _now()
                blocked_reason = await self._blocked_reason(
                    db,
                    user_id=user_id,
                    product=product,
                    now=now,
                    check_balance=False,
                    balance=0,
                )
                if blocked_reason is not None:
                    raise BusinessException(blocked_reason)
                if product.version != data.product_version:
                    raise ConflictException("商品规则已变化，请刷新后重新确认")
                if await self._has_active_entitlement(db, user_id=user_id, product=product, now=now):
                    raise ConflictException("您已拥有该内容的有效权益")

                ends_at = (
                    now + timedelta(days=product.entitlement_days)
                    if product.entitlement_days is not None
                    else None
                )
                order = PointsVirtualOrder(
                    order_no=generate_out_trade_no("PVO"),
                    user_id=user_id,
                    product_id=product.id,
                    idempotency_key=data.idempotency_key,
                    request_fingerprint=fingerprint,
                    product_type=product.product_type,
                    product_name=product.name,
                    course_id=product.course_id,
                    library_id=product.library_id,
                    points_cost=product.points_price,
                    entitlement_days=product.entitlement_days,
                    starts_at=now,
                    ends_at=ends_at,
                    status="completed",
                    completed_at=now,
                )
                db.add(order)
                await db.flush()

                history = await PointsService()._debit_points_in_session(
                    db,
                    user_id=user_id,
                    amount=product.points_price,
                    action_type="redeem_points_virtual",
                    description=f"积分兑换：{product.name}",
                    source_type="points_virtual_order",
                    source_id=order.order_no,
                )
                order.points_history_id = history.id
                product.total_redeemed += 1

                if product.product_type == "course":
                    await self._grant_course(db, order=order, product=product, now=now, ends_at=ends_at)
                else:
                    await self._grant_quiz(db, order=order, product=product, now=now, ends_at=ends_at)

                await db.flush()
                await db.refresh(order)
                return self._redeem_response(
                    order,
                    balance=history.balance_after,
                    idempotent=False,
                )

    async def list_user_orders(
        self, user_id: int, *, page: int = 1, page_size: int = 20
    ):
        from app.schemas.points_virtual import PointsVirtualOrderResponse

        async with get_db_ctx() as db:
            base = select(PointsVirtualOrder).where(PointsVirtualOrder.user_id == user_id)
            total = await db.scalar(
                select(func.count()).select_from(base.subquery())
            ) or 0
            rows = (
                await db.execute(
                    base.order_by(PointsVirtualOrder.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            return PaginatedData(
                items=[PointsVirtualOrderResponse.model_validate(row) for row in rows],
                total=int(total),
                page=page,
                page_size=page_size,
            )

    async def refund(
        self,
        *,
        admin_id: int,
        order_id: int,
        data: PointsVirtualRefundRequest,
    ) -> PointsVirtualRefundResponse:
        async with get_db_ctx() as db:
            async with db.begin():
                unresolved = await db.get(PointsVirtualOrder, order_id)
                if unresolved is None:
                    raise NotFoundException("积分兑换订单")
                await self._lock_user(db, unresolved.user_id)
                order = await db.scalar(
                    select(PointsVirtualOrder)
                    .where(PointsVirtualOrder.id == order_id)
                    .with_for_update()
                )
                if order is None:
                    raise NotFoundException("积分兑换订单")
                existing_refund = await db.scalar(
                    select(PointsVirtualRefund.id).where(
                        PointsVirtualRefund.order_id == order.id
                    )
                )
                if existing_refund is not None or order.status != "completed":
                    raise ConflictException("该兑换订单已退还")

                now = _now()
                if order.product_type == "course":
                    changed_libraries = await self._revoke_course(db, order=order, now=now)
                else:
                    changed_libraries = await self._revoke_quiz(db, order=order, now=now)

                history, _ = await PointsService()._grant_points_in_session(
                    db,
                    user_id=order.user_id,
                    amount=order.points_cost,
                    action_type="refund_points_virtual_order",
                    description=f"积分兑换退还：{order.product_name}",
                    source_type="points_virtual_refund",
                    source_id=order.order_no,
                )
                refund = PointsVirtualRefund(
                    order_id=order.id,
                    user_id=order.user_id,
                    points_returned=order.points_cost,
                    reason_code=data.reason_code,
                    reason_detail=data.reason_detail,
                    refunded_by_admin_id=admin_id,
                    points_history_id=history.id,
                    refunded_at=now,
                )
                db.add(refund)
                order.status = "refunded"
                order.refunded_at = now
                product = await db.scalar(
                    select(PointsVirtualProduct)
                    .where(PointsVirtualProduct.id == order.product_id)
                    .with_for_update()
                )
                if product is None:
                    raise NotFoundException("积分商品")
                product.total_redeemed = max(0, product.total_redeemed - 1)
                for library_id in changed_libraries:
                    from app.services.quiz_v2 import QuizV2Service

                    await QuizV2Service.sync_sessions_for_library(
                        db, library_id, user_id=order.user_id, now=now
                    )
                await db.flush()
                await db.refresh(refund)
                return PointsVirtualRefundResponse.model_validate(refund)

    # ── Helpers ──

    @staticmethod
    async def _locked_content(
        db: AsyncSession,
        *,
        product_type: str,
        course_id: int | None,
        library_id: int | None,
    ) -> Course | QuizLibrary:
        if product_type == "course":
            course = await db.scalar(
                select(Course).where(Course.id == course_id).with_for_update()
            )
            if course is None or course.status != "published":
                raise BusinessException("课程不存在或未上架")
            if course.price <= 0:
                raise BusinessException("免费课程不需要积分兑换")
            return course
        library = await db.scalar(
            select(QuizLibrary).where(QuizLibrary.id == library_id).with_for_update()
        )
        if library is None or library.status != "published":
            raise BusinessException("题库不存在或未上架")
        if library.access_mode != "paid" or library.price_cents <= 0:
            raise BusinessException("仅付费独立题库支持积分兑换")
        return library

    @staticmethod
    def _content_snapshot(product_type: str, content: Course | QuizLibrary) -> dict:
        if product_type == "course":
            return {
                "course_id": content.id,
                "title": content.title,
                "description": content.description,
                "teacher_name": content.teacher_name,
                "cash_price_cents": content.price,
            }
        return {
            "library_id": content.id,
            "library_code": content.library_code,
            "name": content.name,
            "description": content.description,
            "cover_url": content.cover_url,
            "cash_price_cents": content.price_cents,
        }

    @staticmethod
    async def _product_responses(
        db: AsyncSession, products: Sequence[PointsVirtualProduct]
    ) -> list[PointsVirtualProductResponse]:
        if not products:
            return []
        course_ids = [int(row.course_id) for row in products if row.course_id]
        library_ids = [int(row.library_id) for row in products if row.library_id]
        courses = (
            {
                int(row.id): row
                for row in (
                    await db.execute(
                        select(Course).where(Course.id.in_(course_ids))
                    )
                ).scalars()
            }
            if course_ids
            else {}
        )
        libraries = (
            {
                int(row.id): row
                for row in (
                    await db.execute(
                        select(QuizLibrary).where(QuizLibrary.id.in_(library_ids))
                    )
                ).scalars()
            }
            if library_ids
            else {}
        )
        product_ids = [int(row.id) for row in products]
        refunded_counts = (
            {
                int(result_row[0]): int(result_row[1])
                for result_row in (
                    await db.execute(
                        select(PointsVirtualOrder.product_id, func.count())
                        .where(
                            PointsVirtualOrder.product_id.in_(product_ids),
                            PointsVirtualOrder.status == "refunded",
                        )
                        .group_by(PointsVirtualOrder.product_id)
                    )
                ).all()
            }
            if product_ids
            else {}
        )
        now = _now()
        result = []
        for product in products:
            payload = PointsVirtualProductResponse.model_validate(product)
            snapshot = product.content_snapshot or {}
            payload.content_name = (
                snapshot.get("title")
                if product.product_type == "course"
                else snapshot.get("name")
            )
            payload.remaining_stock = (
                None
                if product.stock_limit is None
                else max(0, product.stock_limit - product.total_redeemed)
            )
            payload.refunded_count = refunded_counts.get(product.id, 0)
            content = (
                courses.get(product.course_id)
                if product.product_type == "course"
                else libraries.get(product.library_id)
            )
            content_available = (
                content.status == "published"
                and (product.product_type != "course" or content.is_active)
                if content is not None
                else False
            )
            if product.status == "draft":
                payload.redeem_block_reason = "草稿"
            elif product.status == "offline":
                payload.redeem_block_reason = "已停售"
            elif product.available_from and now < product.available_from:
                payload.redeem_block_reason = "未开始"
            elif product.available_to and now >= product.available_to:
                payload.redeem_block_reason = "已结束"
            elif (
                product.stock_limit is not None
                and product.total_redeemed >= product.stock_limit
            ):
                payload.redeem_block_reason = "已兑完"
            elif not content_available:
                payload.redeem_block_reason = "关联内容不可用"
            else:
                payload.redeemable = True
            result.append(payload)
        return result

    @staticmethod
    def _mask_phone(phone: str | None) -> str | None:
        if not phone:
            return None
        if len(phone) != 11:
            return phone
        return f"{phone[:3]}****{phone[7:]}"

    @staticmethod
    def _fingerprint(*, product_id: int, product_version: int) -> str:
        return hashlib.sha256(f"{product_id}:{product_version}".encode()).hexdigest()

    @staticmethod
    def _redeem_response(
        order: PointsVirtualOrder, *, balance: int, idempotent: bool
    ) -> PointsVirtualRedeemResponse:
        payload = PointsVirtualOrderResponse.model_validate(order).model_dump()
        payload.update(
            balance=balance,
            idempotent=idempotent,
            points_history_id=order.points_history_id,
        )
        return PointsVirtualRedeemResponse(**payload)

    @staticmethod
    async def _require_user(db: AsyncSession, user_id: int) -> User:
        user = await db.get(User, user_id)
        if user is None or not user.is_active:
            raise NotFoundException("用户")
        return user

    @staticmethod
    async def _lock_user(db: AsyncSession, user_id: int) -> None:
        user_id = (
            await db.scalar(
                select(User.id)
                .where(User.id == user_id, User.is_active.is_(True))
                .with_for_update()
            )
        )
        if user_id is None:
            raise NotFoundException("用户")

    async def _blocked_reason(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        product: PointsVirtualProduct,
        now: datetime,
        check_balance: bool,
        balance: int,
    ) -> str | None:
        if product.status != "published":
            return "商品未上架或已停售"
        if product.available_from and now < product.available_from:
            return "商品尚未开售"
        if product.available_to and now >= product.available_to:
            return "商品已结束兑换"
        if product.stock_limit is not None and product.total_redeemed >= product.stock_limit:
            return "商品库存不足"
        if check_balance and balance < product.points_price:
            return "积分余额不足"
        if await self._has_active_entitlement(db, user_id=user_id, product=product, now=now):
            return "您已拥有该内容的有效权益"
        return None

    @staticmethod
    async def _has_active_entitlement(
        db: AsyncSession,
        *,
        user_id: int,
        product: PointsVirtualProduct,
        now: datetime,
    ) -> bool:
        if product.product_type == "course":
            expression = select(CourseEnrollment.id).where(
                CourseEnrollment.user_id == user_id,
                CourseEnrollment.course_id == product.course_id,
                CourseEnrollment.status.in_(ACTIVE_COURSE_STATUSES),
                or_(
                    CourseEnrollment.access_expires_at.is_(None),
                    CourseEnrollment.access_expires_at > now,
                ),
            )
        else:
            expression = select(QuizLibraryEntitlement.id).where(
                QuizLibraryEntitlement.user_id == user_id,
                QuizLibraryEntitlement.library_id == product.library_id,
                QuizLibraryEntitlement.status == "active",
                QuizLibraryEntitlement.starts_at <= now,
                or_(
                    QuizLibraryEntitlement.ends_at.is_(None),
                    QuizLibraryEntitlement.ends_at > now,
                ),
            )
        return await db.scalar(expression.limit(1)) is not None

    @staticmethod
    async def _grant_course(
        db: AsyncSession,
        *,
        order: PointsVirtualOrder,
        product: PointsVirtualProduct,
        now: datetime,
        ends_at: datetime | None,
    ) -> None:
        enrollment = CourseEnrollment(
            user_id=order.user_id,
            course_id=product.course_id,
            points_virtual_order_id=order.id,
            status="enrolled",
            learning_access=True,
            access_granted_at=now,
            access_expires_at=ends_at,
        )
        db.add(enrollment)
        await db.flush()
        # A refunded points enrollment may have left revoked course-bound
        # library entitlements. Re-bind them to the new effective enrollment so
        # the next points refund can revoke exactly this order's sources.
        prior_entitlements = (
            await db.execute(
                select(QuizLibraryEntitlement)
                .where(
                    QuizLibraryEntitlement.user_id == order.user_id,
                    QuizLibraryEntitlement.course_id == product.course_id,
                    QuizLibraryEntitlement.enrollment_id.is_not(None),
                )
                .with_for_update()
            )
        ).scalars().all()
        for prior_entitlement in prior_entitlements:
            prior_entitlement.enrollment_id = enrollment.id
        entitlements = await CourseEntitlementService.grant_for_enrollment(
            db, enrollment, now=now
        )
        if ends_at is not None:
            for entitlement in entitlements:
                entitlement.ends_at = ends_at

    @staticmethod
    async def _grant_quiz(
        db: AsyncSession,
        *,
        order: PointsVirtualOrder,
        product: PointsVirtualProduct,
        now: datetime,
        ends_at: datetime | None,
    ) -> None:
        library = await db.get(QuizLibrary, product.library_id)
        if library is None:
            raise NotFoundException("题库")
        entitlement = QuizLibraryEntitlement(
            user_id=order.user_id,
            library_id=library.id,
            course_id=None,
            points_order_id=order.id,
            source_type="points_order",
            status="active",
            starts_at=now,
            ends_at=ends_at,
            snapshot=CourseEntitlementService._snapshot(library),
        )
        db.add(entitlement)
        await db.flush()
        from app.services.quiz_v2 import QuizV2Service

        await QuizV2Service.sync_sessions_for_library(
            db, library.id, user_id=order.user_id, now=now
        )

    @staticmethod
    async def _revoke_course(
        db: AsyncSession, *, order: PointsVirtualOrder, now: datetime
    ) -> list[int]:
        enrollment = await db.scalar(
            select(CourseEnrollment)
            .where(CourseEnrollment.points_virtual_order_id == order.id)
            .with_for_update()
        )
        if enrollment is None:
            raise ConflictException("积分课程权益来源缺失，不能自动退还")
        enrollment.status = "refunded"
        enrollment.learning_access = False
        enrollment.access_revoked_at = now
        rows = (
            await db.execute(
                select(QuizLibraryEntitlement)
                .where(
                    QuizLibraryEntitlement.enrollment_id == enrollment.id,
                    QuizLibraryEntitlement.status == "active",
                )
                .with_for_update()
            )
        ).scalars().all()
        for row in rows:
            row.status = "revoked"
            row.revoked_at = now
            row.ends_at = now
        return [row.library_id for row in rows]

    @staticmethod
    async def _revoke_quiz(
        db: AsyncSession, *, order: PointsVirtualOrder, now: datetime
    ) -> list[int]:
        entitlement = await db.scalar(
            select(QuizLibraryEntitlement)
            .where(QuizLibraryEntitlement.points_order_id == order.id)
            .with_for_update()
        )
        if entitlement is None:
            raise ConflictException("积分题库权益来源缺失，不能自动退还")
        entitlement.status = "revoked"
        entitlement.revoked_at = now
        entitlement.ends_at = now
        return [entitlement.library_id]
