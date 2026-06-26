"""审核服务"""

from sqlalchemy import select, func

from app.adapter.database import get_db_ctx
from app.port.exceptions import BusinessException, NotFoundException
from app.domain.review.src.index import Review
from app.domain.user.src.index import UserRealname, UserStudent, UserEnterprise
from app.domain.order.src.index import Order
from app.schemas.common import PaginatedData
from app.schemas.review import ReviewCreate, ReviewFilter, ReviewResponse
from app.services.review_callbacks import REVIEW_CALLBACKS


# target_type → (model, id_attr_name, status_attr)
TARGET_CONFIG = {
    "identity":   (UserRealname,    "user_id", "status"),
    "student":    (UserStudent,     "user_id", "status"),
    "enterprise": (UserEnterprise,  "user_id", "status"),
    "order":      (Order,           "id",      "status"),
}


class ReviewService:

    async def create_review(
        self, reviewer_id: int, data: ReviewCreate
    ) -> ReviewResponse:
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

            # 2. 写入 Review 记录
            review = Review(
                target_type=data.target_type,
                target_id=data.target_id,
                reviewer_id=reviewer_id,
                action=data.action,
                comment=data.comment,
            )
            db.add(review)
            await db.commit()
            await db.refresh(review)

        # 3. 查回调并执行
        callback = REVIEW_CALLBACKS.get((data.target_type, data.action))
        if callback is not None:
            await callback(data.target_id, data.comment)

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
