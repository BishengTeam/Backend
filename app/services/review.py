"""审核服务"""

from datetime import date, datetime, timezone

from sqlalchemy import select, func

from app.adapter.database import get_db_ctx
from app.port.exceptions import BusinessException, ConflictException, NotFoundException
from app.domain.review.src.index import Review
from app.domain.renshe.src.index import RensheAuditLog
from app.domain.user.src.index import UserRealname, UserStudent
from app.domain.order.src.index import Order
from app.schemas.common import PaginatedData
from app.schemas.review import ReviewCreate, ReviewFilter, ReviewResponse
from app.services.review_callbacks import REVIEW_CALLBACKS


# target_type → (model, id_attr_name, status_attr)
TARGET_CONFIG = {
    "identity":   (UserRealname,    "user_id", "status"),
    "student":    (UserStudent,     "user_id", "status"),
    "order":      (Order,           "id",      "status"),
}


class ReviewService:

    async def create_review(
        self, reviewer_id: int, data: ReviewCreate
    ) -> ReviewResponse:
        if data.target_type in {"identity", "student"}:
            return await self._create_profile_review(reviewer_id, data)

        # 1. 校验 target 存在性
        config = TARGET_CONFIG.get(data.target_type)
        if config is None:
            raise BusinessException(f"不支持的审核类型: {data.target_type}")

        model, id_attr, _status_attr = config
        async with get_db_ctx() as db:
            target = (await db.execute(
                select(model).where(getattr(model, id_attr) == data.target_id)
            )).scalar_one_or_none()
            if target is None:
                raise NotFoundException(f"{data.target_type} 对象")
            if data.target_type == "order" and target.application_id is not None:
                raise ConflictException("人社订单必须使用专用报名审核和退款流程")

            # 2. 写入 Review 记录
            review = Review(
                target_type=data.target_type,
                target_id=data.target_id,
                reviewer_id=reviewer_id,
                action=data.action,
                comment=data.comment,
            )
            db.add(review)
            await db.flush()
            # Keep the human-resources audit trail beside the legacy generic
            # review row.  Do not copy the free-form comment: it may contain
            # names, phone numbers or identity details and is already stored
            # in the dedicated review table.
            db.add(
                RensheAuditLog(
                    actor_type="admin",
                    actor_id=reviewer_id,
                    action=f"verification.{data.target_type}.{data.action}",
                    object_type=data.target_type,
                    object_id=data.target_id,
                    result="succeeded",
                    summary={
                        "review_id": review.id,
                        "status": target.status,
                        "comment_provided": bool(data.comment),
                    },
                )
            )
            await db.commit()
            await db.refresh(review)

        # 3. 查回调并执行
        callback = REVIEW_CALLBACKS.get((data.target_type, data.action))
        if callback is not None:
            await callback(data.target_id, data.comment)

        return ReviewResponse.model_validate(review)

    async def _create_profile_review(
        self, reviewer_id: int, data: ReviewCreate
    ) -> ReviewResponse:
        model, id_attr, _status_attr = TARGET_CONFIG[data.target_type]
        async with get_db_ctx() as db:
            target = (
                await db.execute(
                    select(model)
                    .where(getattr(model, id_attr) == data.target_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if target is None:
                raise NotFoundException(f"{data.target_type} 对象")
            if target.status != "pending":
                raise ConflictException("只有待审核的认证资料可以登记审核结果")

            if data.action == "reject" and target.snapshot:
                for key, value in target.snapshot.items():
                    if (
                        data.target_type == "student"
                        and key == "enrollment_date"
                        and isinstance(value, str)
                    ):
                        value = date.fromisoformat(value)
                    setattr(target, key, value)
                target.snapshot = None

            target.status = "verified" if data.action == "approve" else "rejected"
            if data.action == "approve":
                target.verified_at = datetime.now(timezone.utc).isoformat()
                target.snapshot = None
            else:
                target.verified_at = None
                if data.target_type == "identity":
                    # A rejected identity is not an active reservation.  Keep
                    # the irreversible historical hash, but release the
                    # unique live-identity slot so another account can submit
                    # the same document and this user can correct/re-submit.
                    target.active_id_card_hash = None

            review = Review(
                target_type=data.target_type,
                target_id=data.target_id,
                reviewer_id=reviewer_id,
                action=data.action,
                comment=data.comment,
            )
            db.add(review)
            # The dedicated human-resources audit row must be committed in the
            # same transaction as the profile status transition.  Do not copy
            # the free-form comment: it can contain PII and remains in the
            # review table, while the audit summary is intentionally minimal.
            db.add(
                RensheAuditLog(
                    actor_type="admin",
                    actor_id=reviewer_id,
                    action=f"verification.{data.target_type}.{data.action}",
                    object_type=data.target_type,
                    object_id=data.target_id,
                    result="succeeded",
                    summary={
                        "status": target.status,
                        "comment_provided": bool(data.comment),
                    },
                )
            )
            await db.commit()
            await db.refresh(review)
            return ReviewResponse.model_validate(review)

    async def list_reviews(
        self, filters: ReviewFilter | None, page: int, page_size: int
    ) -> PaginatedData[ReviewResponse]:
        async with get_db_ctx() as db:
            base = select(Review)
            if filters:
                if filters.target_type:
                    base = base.where(Review.target_type == filters.target_type)
                if filters.target_id:
                    base = base.where(Review.target_id == filters.target_id)
            count_stmt = select(func.count()).select_from(base.subquery())
            total = (await db.execute(count_stmt)).scalar() or 0
            stmt = base.order_by(Review.id.desc()).offset(
                (page - 1) * page_size
            ).limit(page_size)
            result = await db.execute(stmt)
            reviews = result.scalars().all()
            return PaginatedData[ReviewResponse](
                items=[ReviewResponse.model_validate(r) for r in reviews],
                total=total,
                page=page,
                page_size=page_size,
            )
