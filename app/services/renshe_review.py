from datetime import datetime, timezone

from sqlalchemy import func, or_, select

from app.adapter.database import get_db_ctx
from app.domain.order.src.index import Order
from app.domain.renshe.src.index import (
    RensheApplication,
    RensheApplicationVersion,
    RensheAuditLog,
    RensheMaterial,
    RensheReview,
    RensheReviewCorrection,
)
from app.domain.user.src.index import UserRealname
from app.port.exceptions import ConflictException, NotFoundException
from app.schemas.common import PaginatedData
from app.schemas.renshe import (
    RensheAdminApplicationDetailResponse,
    RensheAdminApplicationListItem,
    RensheAdminVersionDetailResponse,
    RensheApplicationResponse,
    RensheMaterialMetadataResponse,
    RensheReviewCorrectionCreate,
    RensheReviewCorrectionResponse,
    RensheReviewCreate,
    RensheReviewResponse,
)


class RensheReviewService:
    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    async def list_applications(
        self,
        *,
        plan_id: int | None,
        status: str | None,
        page: int,
        page_size: int,
        payment_status: str | None = None,
        keyword: str | None = None,
        submitted_at_start: datetime | None = None,
        submitted_at_end: datetime | None = None,
    ) -> PaginatedData[RensheAdminApplicationListItem]:
        async with get_db_ctx() as db:
            latest_order_id = (
                select(Order.id)
                .where(Order.application_id == RensheApplication.id)
                .order_by(Order.id.desc())
                .limit(1)
                .correlate(RensheApplication)
                .scalar_subquery()
            )
            base = (
                select(
                    RensheApplication,
                    RensheApplicationVersion,
                    Order,
                    UserRealname,
                )
                .outerjoin(
                    RensheApplicationVersion,
                    RensheApplicationVersion.id == RensheApplication.current_version_id,
                )
                .outerjoin(Order, Order.id == latest_order_id)
                .outerjoin(UserRealname, UserRealname.user_id == RensheApplication.user_id)
            )
            if plan_id is not None:
                base = base.where(RensheApplication.plan_id == plan_id)
            if status is not None:
                base = base.where(RensheApplication.status == status)
            if payment_status is not None:
                if payment_status not in {"pending", "paid", "completed", "refunded", "closed"}:
                    raise ConflictException("不支持的订单状态筛选")
                base = base.where(Order.status == payment_status)
            if submitted_at_start is not None:
                base = base.where(RensheApplication.submitted_at >= submitted_at_start)
            if submitted_at_end is not None:
                base = base.where(RensheApplication.submitted_at <= submitted_at_end)
            if keyword and keyword.strip():
                term = f"%{keyword.strip()}%"
                base = base.where(
                    or_(
                        RensheApplicationVersion.realname_snapshot["real_name"]
                        .as_string()
                        .ilike(term),
                        RensheApplicationVersion.realname_snapshot["id_card_number"]
                        .as_string()
                        .ilike(term),
                        RensheApplicationVersion.form_data["contact_phone"]
                        .as_string()
                        .ilike(term),
                        (
                            RensheApplication.current_version_id.is_(None)
                            & UserRealname.real_name.ilike(term)
                        ),
                        (
                            RensheApplication.current_version_id.is_(None)
                            & UserRealname.id_card_number.ilike(term)
                        ),
                        (
                            RensheApplication.current_version_id.is_(None)
                            & RensheApplication.draft_data["contact_phone"]
                            .as_string()
                            .ilike(term)
                        ),
                    )
                )
            total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
            rows = (
                await db.execute(
                    base.order_by(RensheApplication.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).all()
            items = []
            for application, version, order, realname in rows:
                realname_snapshot = dict(version.realname_snapshot) if version else {}
                form_data = dict(version.form_data) if version else dict(application.draft_data or {})
                id_card = realname_snapshot.get("id_card_number")
                candidate_name = realname_snapshot.get("real_name")
                if version is None and realname is not None:
                    id_card = realname.id_card_number
                    candidate_name = realname.real_name
                phone = form_data.get("contact_phone")
                items.append(
                    RensheAdminApplicationListItem(
                        id=application.id,
                        plan_id=application.plan_id,
                        user_id=application.user_id,
                        current_version_id=application.current_version_id,
                        status=application.status,
                        candidate_name=candidate_name,
                        id_card_masked=self._mask_id_card(id_card),
                        contact_phone_masked=self._mask_phone(phone),
                        payment_order_id=order.id if order else None,
                        payment_status=order.status if order else None,
                        submitted_at=application.submitted_at,
                        frozen_at=application.frozen_at,
                        created_at=application.created_at,
                        updated_at=application.updated_at,
                    )
                )
            return PaginatedData[RensheAdminApplicationListItem](
                items=items,
                total=total,
                page=page,
                page_size=page_size,
            )

    @staticmethod
    def _mask_id_card(value: str | None) -> str | None:
        if not value or len(value) < 8:
            return value
        return f"{value[:4]}**********{value[-4:]}"

    @staticmethod
    def _mask_phone(value: str | None) -> str | None:
        if not value or len(value) < 7:
            return value
        return f"{value[:3]}****{value[-4:]}"

    async def get_application(
        self, application_id: int
    ) -> RensheAdminApplicationDetailResponse:
        async with get_db_ctx() as db:
            application = await db.get(RensheApplication, application_id)
            if application is None:
                raise NotFoundException("人社报名")
            versions = (
                await db.execute(
                    select(RensheApplicationVersion)
                    .where(RensheApplicationVersion.application_id == application_id)
                    .order_by(RensheApplicationVersion.version_no.desc())
                )
            ).scalars().all()
            version_ids = [version.id for version in versions]
            materials = []
            reviews = []
            if version_ids:
                materials = (
                    await db.execute(
                        select(RensheMaterial)
                        .where(RensheMaterial.version_id.in_(version_ids))
                        .order_by(RensheMaterial.version_id, RensheMaterial.kind)
                    )
                ).scalars().all()
                reviews = (
                    await db.execute(
                        select(RensheReview)
                        .where(RensheReview.version_id.in_(version_ids))
                        .order_by(RensheReview.reviewed_at, RensheReview.id)
                    )
                ).scalars().all()
            materials_by_version: dict[int, list[RensheMaterial]] = {}
            reviews_by_version: dict[int, list[RensheReview]] = {}
            for material in materials:
                materials_by_version.setdefault(material.version_id, []).append(material)
            for review in reviews:
                reviews_by_version.setdefault(review.version_id, []).append(review)
            order = await db.scalar(
                select(Order)
                .where(Order.application_id == application_id)
                .order_by(Order.id.desc())
                .limit(1)
            )
            return RensheAdminApplicationDetailResponse(
                **RensheApplicationResponse.model_validate(application).model_dump(),
                versions=[
                    RensheAdminVersionDetailResponse(
                        id=version.id,
                        version_no=version.version_no,
                        submitted_at=version.submitted_at,
                        realname_snapshot=dict(version.realname_snapshot),
                        student_snapshot=dict(version.student_snapshot),
                        form_data=dict(version.form_data),
                        sensitive_cleared_at=version.sensitive_cleared_at,
                        materials=[
                            RensheMaterialMetadataResponse.model_validate(material)
                            for material in materials_by_version.get(version.id, [])
                        ],
                        reviews=[
                            RensheReviewResponse.model_validate(review)
                            for review in reviews_by_version.get(version.id, [])
                        ],
                    )
                    for version in versions
                ],
                current_order_id=order.id if order else None,
                current_order_status=order.status if order else None,
            )

    async def review(
        self,
        *,
        reviewer_id: int,
        application_id: int,
        stage: str,
        data: RensheReviewCreate,
    ) -> RensheReviewResponse:
        expected_status = {
            "initial": "pending_initial_review",
            "external": "pending_external_review",
        }[stage]
        target_status = {
            ("initial", "approved"): "pending_external_review",
            ("initial", "rejected"): "initial_rejected",
            ("external", "approved"): "external_approved",
            ("external", "rejected"): "external_rejected",
        }[(stage, data.decision)]

        async with get_db_ctx() as db:
            order = await db.scalar(
                select(Order)
                .where(
                    Order.application_id == application_id,
                    Order.status.in_(("paid", "completed")),
                )
                .with_for_update()
                .limit(1)
            )
            application = await db.scalar(
                select(RensheApplication)
                .where(RensheApplication.id == application_id)
                .with_for_update()
            )
            if application is None:
                raise NotFoundException("人社报名")
            if application.frozen_at is not None:
                raise ConflictException("退款处理中，报名已冻结")
            if application.status != expected_status:
                raise ConflictException("当前报名状态不允许执行该审核")
            if application.current_version_id is None:
                raise ConflictException("报名缺少当前提交版本")

            if order is None:
                raise ConflictException("只有已支付报名可以审核")

            now = self._now()
            review = RensheReview(
                application_id=application.id,
                version_id=application.current_version_id,
                stage=stage,
                decision=data.decision,
                reason=data.reason,
                required_changes=data.required_changes,
                reviewer_id=reviewer_id,
                reviewed_at=now,
            )
            db.add(review)
            application.status = target_status
            if stage == "initial":
                application.initial_reviewed_at = now
            else:
                application.external_reviewed_at = now
            await db.flush()
            db.add(
                RensheAuditLog(
                    actor_type="admin",
                    actor_id=reviewer_id,
                    action=f"review.{stage}.{data.decision}",
                    object_type="review",
                    object_id=review.id,
                    application_id=application.id,
                    version_id=application.current_version_id,
                    result="succeeded",
                    summary={"stage": stage, "decision": data.decision},
                )
            )
            await db.commit()
            await db.refresh(review)
            return RensheReviewResponse.model_validate(review)

    async def correct_review(
        self,
        *,
        super_admin_id: int,
        review_id: int,
        data: RensheReviewCorrectionCreate,
    ) -> RensheReviewCorrectionResponse:
        async with get_db_ctx() as db:
            review = await db.scalar(
                select(RensheReview)
                .where(RensheReview.id == review_id)
                .with_for_update()
            )
            if review is None:
                raise NotFoundException("人社审核记录")
            application = await db.scalar(
                select(RensheApplication)
                .where(RensheApplication.id == review.application_id)
                .with_for_update()
            )
            if application is None:
                raise NotFoundException("人社报名")
            if application.frozen_at is not None:
                raise ConflictException("退款处理中，不能更正审核结果")
            if application.current_version_id != review.version_id:
                raise ConflictException("该审核版本已被后续重提替代，不能更正")

            latest_correction = await db.scalar(
                select(RensheReviewCorrection)
                .where(RensheReviewCorrection.review_id == review.id)
                .order_by(RensheReviewCorrection.id.desc())
                .limit(1)
            )
            current_decision = (
                latest_correction.to_decision if latest_correction else review.decision
            )
            if current_decision == data.to_decision:
                raise ConflictException("更正结果与当前有效结果相同")

            expected_status = {
                ("initial", "approved"): "pending_external_review",
                ("initial", "rejected"): "initial_rejected",
                ("external", "approved"): "external_approved",
                ("external", "rejected"): "external_rejected",
            }[(review.stage, current_decision)]
            target_status = {
                ("initial", "approved"): "pending_external_review",
                ("initial", "rejected"): "initial_rejected",
                ("external", "approved"): "external_approved",
                ("external", "rejected"): "external_rejected",
            }[(review.stage, data.to_decision)]
            if application.status != expected_status:
                raise ConflictException("报名已进入后续流程，不能直接更正该审核")

            before = {"application_status": application.status, "decision": current_decision}
            application.status = target_status
            after = {"application_status": target_status, "decision": data.to_decision}
            correction = RensheReviewCorrection(
                review_id=review.id,
                application_id=application.id,
                corrected_by_admin_id=super_admin_id,
                from_decision=current_decision,
                to_decision=data.to_decision,
                reason=data.reason.strip(),
                state_before=before,
                state_after=after,
            )
            db.add(correction)
            await db.flush()
            db.add(
                RensheAuditLog(
                    actor_type="admin",
                    actor_id=super_admin_id,
                    action="review.correct",
                    object_type="review_correction",
                    object_id=correction.id,
                    application_id=application.id,
                    version_id=review.version_id,
                    result="succeeded",
                    summary={"from": current_decision, "to": data.to_decision},
                )
            )
            await db.commit()
            await db.refresh(correction)
            return RensheReviewCorrectionResponse.model_validate(correction)
