"""管理端用户服务"""
from datetime import date, datetime, timezone

from sqlalchemy import and_, delete, func, or_, select

from app.adapter.database import get_db_ctx
from app.port.exceptions import ConflictException, NotFoundException, ValidationException
from app.domain.community.src.index import Conversation
from app.domain.order.src.index import Order
from app.domain.renshe.src.index import RensheAuditLog
from app.domain.user.src.index import (
    DeletedOpenid, User, UserProfile, UserRealname, UserStudent, UserEnterprise,
)
from app.schemas.admin import (
    AdminIdentityReview,
    AdminUserConversationBrief,
    AdminUserFilter,
    AdminUserListItem,
    AdminUserReviewSummary,
    AdminUserReviewSummaryItem,
    AdminUserOrderBrief,
    AdminUserStatusToggle,
)
from app.schemas.common import PaginatedData
from app.services.user import _assert_account_closure_allowed
from app.services.renshe_audit import record_best_effort_audit


class AdminUserService:

    @staticmethod
    def _mask_phone(value: str | None) -> str | None:
        """Return the default list representation of a phone number."""

        if not value or len(value) < 7:
            return value
        return f"{value[:3]}****{value[-4:]}"

    @staticmethod
    def _latest_review_at_expr():
        return func.greatest(
            func.coalesce(UserRealname.updated_at, User.created_at),
            func.coalesce(UserStudent.updated_at, User.created_at),
        )

    # ─── 用户列表 / 导出 ───

    async def list_users(
        self, filters: AdminUserFilter | None, page: int, page_size: int
    ) -> PaginatedData[AdminUserListItem]:
        async with get_db_ctx() as db:
            base = (
                select(
                    User.id,
                    User.openid,
                    User.phone,
                    User.is_active,
                    User.created_at,
                    UserRealname.status.label("identity_status"),
                    UserStudent.status.label("student_status"),
                )
                .outerjoin(UserRealname, UserRealname.user_id == User.id)
                .outerjoin(UserStudent, UserStudent.user_id == User.id)
                # 禁用用户进回收站陈列、已注销用户后台不可见，列表与导出一律不再展示
                .where(User.is_active == True)  # noqa: E712
            )
            if filters:
                if filters.openid:
                    base = base.where(User.openid == filters.openid)
                if filters.phone:
                    base = base.where(User.phone == filters.phone)
                if filters.created_at_start:
                    base = base.where(User.created_at >= filters.created_at_start)
                if filters.created_at_end:
                    base = base.where(User.created_at <= filters.created_at_end)
                if filters.identity_status:
                    base = base.where(UserRealname.status == filters.identity_status)
                if filters.student_status:
                    base = base.where(UserStudent.status == filters.student_status)
                if filters.review_status == "pending":
                    base = base.where(
                        or_(
                            UserRealname.status == "pending",
                            UserStudent.status == "pending",
                        )
                    )
                elif filters.review_status == "approved":
                    base = base.where(
                        and_(
                            UserRealname.status == "verified",
                            UserStudent.status == "verified",
                        )
                    )
                elif filters.review_status == "rejected":
                    base = base.where(
                        or_(
                            UserRealname.status == "rejected",
                            UserStudent.status == "rejected",
                        )
                    )
            count_stmt = select(func.count()).select_from(base.subquery())
            total = (await db.execute(count_stmt)).scalar() or 0
            review_filter_active = bool(
                filters
                and (
                    filters.review_status
                    or filters.identity_status
                    or filters.student_status
                )
            )
            ordered_stmt = (
                base.order_by(self._latest_review_at_expr().desc(), User.id.desc())
                if review_filter_active
                else base.order_by(User.id.desc())
            )
            stmt = ordered_stmt.offset((page - 1) * page_size).limit(page_size)
            result = await db.execute(stmt)
            rows = result.all()
            items = [AdminUserListItem(
                id=r.id,
                openid=r.openid,
                # User lists are the default view; raw phone numbers are only
                # available through the explicitly audited detail workflow.
                phone=self._mask_phone(r.phone),
                is_active=r.is_active,
                created_at=r.created_at,
                identity_status=r.identity_status,
                student_status=r.student_status,
            ) for r in rows]
            return PaginatedData[AdminUserListItem](
                items=items,
                total=total,
                page=page,
                page_size=page_size,
            )

    async def get_review_summary(
        self, *, latest_limit: int = 10
    ) -> AdminUserReviewSummary:
        async with get_db_ctx() as db:
            identity_pending_count = (
                await db.scalar(
                    select(func.count())
                    .select_from(UserRealname)
                    .where(UserRealname.status == "pending")
                )
            ) or 0
            student_pending_count = (
                await db.scalar(
                    select(func.count())
                    .select_from(UserStudent)
                    .where(UserStudent.status == "pending")
                )
            ) or 0
            latest_at = self._latest_review_at_expr()
            rows = (
                await db.execute(
                    select(
                        User,
                        UserRealname.status.label("identity_status"),
                        UserStudent.status.label("student_status"),
                        UserRealname.updated_at.label("identity_updated_at"),
                        UserStudent.updated_at.label("student_updated_at"),
                        latest_at.label("latest_review_at"),
                    )
                    .outerjoin(UserRealname, UserRealname.user_id == User.id)
                    .outerjoin(UserStudent, UserStudent.user_id == User.id)
                    .where(
                        or_(
                            UserRealname.status == "pending",
                            UserStudent.status == "pending",
                        )
                    )
                    .order_by(latest_at.desc(), User.id.desc())
                    .limit(latest_limit)
                )
            ).all()
            latest_pending = [
                AdminUserReviewSummaryItem(
                    user_id=row.User.id,
                    openid=row.User.openid,
                    phone=self._mask_phone(row.User.phone),
                    is_active=row.User.is_active,
                    created_at=row.User.created_at,
                    identity_status=row.identity_status,
                    student_status=row.student_status,
                    identity_updated_at=row.identity_updated_at,
                    student_updated_at=row.student_updated_at,
                    latest_review_at=row.latest_review_at,
                )
                for row in rows
            ]
            return AdminUserReviewSummary(
                identity_pending_count=int(identity_pending_count),
                student_pending_count=int(student_pending_count),
                latest_pending=latest_pending,
            )

    async def import_users_csv(
        self,
        filters: AdminUserFilter | None,
        *,
        actor_id: int | None = None,
    ) -> str:
        import csv
        import io

        async with get_db_ctx() as db:
            base = (
                select(User)
                .outerjoin(UserRealname, UserRealname.user_id == User.id)
                .outerjoin(UserStudent, UserStudent.user_id == User.id)
                # 禁用用户进回收站陈列、已注销用户后台不可见，列表与导出一律不再展示
                .where(User.is_active == True)  # noqa: E712
            )
            if filters:
                if filters.openid:
                    base = base.where(User.openid == filters.openid)
                if filters.phone:
                    base = base.where(User.phone == filters.phone)
                if filters.created_at_start:
                    base = base.where(User.created_at >= filters.created_at_start)
                if filters.created_at_end:
                    base = base.where(User.created_at <= filters.created_at_end)
                if filters.identity_status:
                    base = base.where(UserRealname.status == filters.identity_status)
                if filters.student_status:
                    base = base.where(UserStudent.status == filters.student_status)
                if filters.review_status == "pending":
                    base = base.where(
                        or_(UserRealname.status == "pending", UserStudent.status == "pending")
                    )
                elif filters.review_status == "approved":
                    base = base.where(
                        and_(
                            UserRealname.status == "verified",
                            UserStudent.status == "verified",
                        )
                    )
                elif filters.review_status == "rejected":
                    base = base.where(
                        or_(
                            UserRealname.status == "rejected",
                            UserStudent.status == "rejected",
                        )
                    )
            stmt = base.order_by(User.id.desc())
            result = await db.execute(stmt)
            users = result.scalars().all()
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["ID", "OpenID", "Phone", "IsActive", "CreatedAt"])
            for u in users:
                writer.writerow(
                    [
                        u.id,
                        u.openid,
                        self._mask_phone(u.phone) or "",
                        u.is_active,
                        u.created_at,
                    ]
                )
            csv_content = output.getvalue()
        if actor_id is not None:
            # The export contains only the same masked phone representation as
            # the list view.  Keep filter values out of the audit summary: an
            # openid/phone search term is itself sensitive data.
            await record_best_effort_audit(
                actor_type="admin",
                actor_id=actor_id,
                action="user.export",
                object_type="user_export",
                object_id=0,
                result="succeeded",
                summary={
                    "count": len(users),
                    "filter_fields": sorted(
                        key
                        for key, value in (
                            ("openid", filters.openid if filters else None),
                            ("phone", filters.phone if filters else None),
                            (
                                "created_at_start",
                                filters.created_at_start if filters else None,
                            ),
                            (
                                "created_at_end",
                                filters.created_at_end if filters else None,
                            ),
                            (
                                "identity_status",
                                filters.identity_status if filters else None,
                            ),
                            (
                                "student_status",
                                filters.student_status if filters else None,
                            ),
                        )
                        if value is not None
                    ),
                },
            )
        return csv_content

    async def export_users(
        self,
        filters: AdminUserFilter | None,
        *,
        actor_id: int | None = None,
    ) -> str:
        """Compatibility name for the admin CSV export workflow.

        Older integration callers use ``export_users`` while the HTTP layer
        historically called ``import_users_csv``.  Keep one implementation so
        filtering, masking and audit behavior cannot drift between them.
        """

        return await self.import_users_csv(filters, actor_id=actor_id)

    async def get_user(
        self, user_id: int, *, actor_id: int | None = None
    ) -> AdminUserListItem:
        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None:
                raise NotFoundException("用户")
            payload = AdminUserListItem.model_validate(user)
            payload.phone = self._mask_phone(payload.phone)
        if actor_id is not None:
            await record_best_effort_audit(
                actor_type="admin",
                actor_id=actor_id,
                action="user.view",
                object_type="user",
                object_id=user_id,
                result="succeeded",
                summary={"admin_view": True},
            )
        return payload

    async def toggle_user_status(
        self, user_id: int, is_active: bool, *, actor_id: int | None = None
    ) -> AdminUserListItem:
        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None:
                raise NotFoundException("用户")
            if not is_active and user.is_active:
                await _assert_account_closure_allowed(db, user_id)
            if is_active:
                # 登录闸门先查注销黑名单再看 is_active（auth._login），
                # 启用必须同事务清掉 deleted_openid，否则恢复的用户仍无法登录。
                await db.execute(
                    delete(DeletedOpenid).where(DeletedOpenid.openid == user.openid)
                )
            user.is_active = is_active
            db.add(
                RensheAuditLog(
                    actor_type="admin",
                    actor_id=actor_id,
                    action="user_account.activate" if is_active else "user_account.disable",
                    object_type="user",
                    object_id=user_id,
                    result="succeeded",
                    summary={"is_active": is_active},
                )
            )
            await db.commit()
            await db.refresh(user)
            payload = AdminUserListItem.model_validate(user)
            payload.phone = self._mask_phone(payload.phone)
            return payload

    async def adjust_points(
        self,
        user_id: int,
        *,
        amount: int,
        reason: str,
        actor_id: int | None = None,
    ) -> int:
        """手工调分（仅 super_admin）：正数发放、负数扣减，返回调整后余额。

        扣减带 allow_negative=False，余额不足直接拒绝，防误操作扣出欠分；
        系统侧退款追扣（允许负余额）不经过这里。每次调整独立幂等键，
        原因写入流水与审计日志。
        """
        if amount == 0:
            raise ValidationException("调整积分不能为 0")
        from uuid import uuid4

        from app.services.points import PointsService

        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None:
                raise NotFoundException("用户")
            points_service = PointsService()
            grant = amount > 0
            if grant:
                history, _granted = await points_service._grant_points_in_session(
                    db,
                    user_id=user_id,
                    amount=amount,
                    action_type="admin_adjust",
                    description=f"管理员调整：{reason}",
                    source_type="admin_manual",
                    source_id=uuid4().hex,
                )
            else:
                history = await points_service._debit_points_in_session(
                    db,
                    user_id=user_id,
                    amount=-amount,
                    action_type="admin_adjust",
                    description=f"管理员调整：{reason}",
                    source_type="admin_manual",
                    source_id=uuid4().hex,
                    allow_negative=False,
                )
            db.add(
                RensheAuditLog(
                    actor_type="admin",
                    actor_id=actor_id,
                    action="points.manual_adjust",
                    object_type="user",
                    object_id=user_id,
                    result="succeeded",
                    summary={"amount": amount, "reason": reason,
                             "balance_after": history.balance_after},
                )
            )
            await db.commit()
            return history.balance_after

    async def batch_delete(
        self, user_ids: list[int], *, actor_id: int | None = None
    ) -> int:
        async with get_db_ctx() as db:
            result = await db.execute(
                select(User).where(User.id.in_(user_ids), User.is_active == True)
            )
            users = result.scalars().all()
            for user in users:
                await _assert_account_closure_allowed(db, user.id)
                user.is_active = False
                db.add(
                    RensheAuditLog(
                        actor_type="admin",
                        actor_id=actor_id,
                        action="user_account.disable",
                        object_type="user",
                        object_id=user.id,
                        result="succeeded",
                        summary={"is_active": False, "batch": True},
                    )
                )
            await db.commit()
            return len(users)

    async def get_user_orders(
        self, user_id: int, *, actor_id: int | None = None
    ) -> list[AdminUserOrderBrief]:
        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None:
                raise NotFoundException("用户")
            stmt = select(Order).where(Order.user_id == user_id).order_by(Order.id.desc())
            result = await db.execute(stmt)
            payload = [AdminUserOrderBrief.model_validate(o) for o in result.scalars().all()]
        if actor_id is not None:
            await record_best_effort_audit(
                actor_type="admin",
                actor_id=actor_id,
                action="user.orders.view",
                object_type="user",
                object_id=user_id,
                result="succeeded",
                summary={"count": len(payload)},
            )
        return payload

    async def update_user_profile(
        self,
        user_id: int,
        data: "AdminProfileUpdate",
        *,
        actor_id: int | None = None,
    ) -> "UserProfileDetail":
        """Edit non-certification profile fields only."""

        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None:
                raise NotFoundException("用户")

            update_data = data.model_dump(exclude_unset=True)

            # Level 1: user_profile
            profile = (await db.execute(
                select(UserProfile).where(UserProfile.user_id == user_id)
            )).scalar_one_or_none()
            if profile is None:
                profile = UserProfile(user_id=user_id)
                db.add(profile)

            changed_fields: set[str] = set()
            for key in ("nickname", "email", "province", "city", "address"):
                if key in update_data:
                    if getattr(profile, key) != update_data[key]:
                        changed_fields.add(key)
                    setattr(profile, key, update_data[key])
            if "phone" in update_data:
                phone = update_data["phone"]
                if phone:
                    occupied_by = await db.scalar(
                        select(User.id)
                        .where(User.id != user_id, User.phone == phone, User.is_active.is_(True))
                        .limit(1)
                    )
                    if occupied_by is not None:
                        raise ConflictException("该手机号已被其他有效账号绑定")
                if user.phone != phone:
                    changed_fields.add("phone")
                user.phone = phone

            db.add(
                RensheAuditLog(
                    actor_type="admin",
                    actor_id=actor_id,
                    action="user.profile.update",
                    object_type="user",
                    object_id=user_id,
                    result="succeeded",
                    summary={"changed_fields": sorted(changed_fields)},
                )
            )
            await db.commit()
            return await self.get_user_profile(user_id)

    async def get_user_conversations(
        self, user_id: int, *, actor_id: int | None = None
    ) -> list[AdminUserConversationBrief]:
        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None:
                raise NotFoundException("用户")
            stmt = (
                select(Conversation)
                .where(Conversation.user_id == user_id)
                .order_by(Conversation.id.desc())
            )
            result = await db.execute(stmt)
            payload = [AdminUserConversationBrief.model_validate(c) for c in result.scalars().all()]
        if actor_id is not None:
            await record_best_effort_audit(
                actor_type="admin",
                actor_id=actor_id,
                action="user.conversations.view",
                object_type="user",
                object_id=user_id,
                result="succeeded",
                summary={"count": len(payload)},
            )
        return payload

    # ─── 聚合查询（管理端，不脱敏） ───

    async def get_user_profile(
        self, user_id: int, *, actor_id: int | None = None
    ) -> "UserProfileDetail":
        from app.services.user import UserService
        payload = await UserService().get_profile(user_id, is_admin=True)
        if actor_id is not None:
            await record_best_effort_audit(
                actor_type="admin",
                actor_id=actor_id,
                action="user.profile.view",
                object_type="user",
                object_id=user_id,
                result="succeeded",
                summary={"admin_view": True},
            )
        return payload

    async def get_user_identity(
        self, user_id: int, *, actor_id: int | None = None
    ) -> "RealnameAdminResponse":
        from app.schemas.user import RealnameAdminResponse

        async with get_db_ctx() as db:
            realname = (await db.execute(
                select(UserRealname).where(UserRealname.user_id == user_id)
            )).scalar_one_or_none()
            if realname is None:
                raise NotFoundException("实名认证信息")

            payload = RealnameAdminResponse(
                user_type=realname.user_type,
                last_name_zh=realname.last_name_zh,
                first_name_zh=realname.first_name_zh,
                last_name_en=realname.last_name_en,
                first_name_en=realname.first_name_en,
                real_name=realname.real_name,
                id_card_number=realname.id_card_number,
                id_card_front_oss=realname.id_card_front_oss,
                id_card_back_oss=realname.id_card_back_oss,
                avatar_oss=realname.avatar_oss,
                birth_date=realname.birth_date,
                gender=realname.gender,
                age=realname.age,
                census_register=realname.census_register,
                zip_code=realname.zip_code,
                political_status=realname.political_status,
                ethnicity=realname.ethnicity,
                status=realname.status,
                verified_at=realname.verified_at,
            )
        if actor_id is not None:
            await record_best_effort_audit(
                actor_type="admin",
                actor_id=actor_id,
                action="verification.identity.view",
                object_type="identity",
                object_id=user_id,
                result="succeeded",
                summary={"status": payload.status, "admin_view": True},
            )
        return payload

    async def get_user_student(
        self, user_id: int, *, actor_id: int | None = None
    ) -> "StudentResponse":
        from app.schemas.user import StudentResponse

        async with get_db_ctx() as db:
            student = (await db.execute(
                select(UserStudent).where(UserStudent.user_id == user_id)
            )).scalar_one_or_none()
            if student is None:
                raise NotFoundException("学生信息")

            payload = StudentResponse(
                education=student.education,
                school=student.school,
                major=student.major,
                enrollment_date=student.enrollment_date,
                student_card_oss=student.student_card_oss,
                enrollment_pdf_oss=student.enrollment_pdf_oss,
                degree_cert_oss=student.degree_cert_oss,
                status=student.status,
                verified_at=student.verified_at,
            )
        if actor_id is not None:
            await record_best_effort_audit(
                actor_type="admin",
                actor_id=actor_id,
                action="verification.student.view",
                object_type="student",
                object_id=user_id,
                result="succeeded",
                summary={"status": payload.status, "admin_view": True},
            )
        return payload

    async def get_user_enterprise(self, user_id: int) -> "EnterpriseResponse":
        raise ValidationException("首版已停用企业认证，只支持学生认证")
        from app.schemas.user import EnterpriseResponse

        async with get_db_ctx() as db:
            enterprise = (await db.execute(
                select(UserEnterprise).where(UserEnterprise.user_id == user_id)
            )).scalar_one_or_none()
            if enterprise is None:
                raise NotFoundException("企业信息")

            return EnterpriseResponse(
                organization=enterprise.organization,
                status=enterprise.status,
                verified_at=enterprise.verified_at,
            )

    # ─── 审核 ───

    async def review_identity(
        self, user_id: int, data: AdminIdentityReview, *, actor_id: int | None = None
    ) -> dict:
        if data.status not in ("verified", "rejected"):
            raise ValidationException("status 必须为 verified 或 rejected")

        async with get_db_ctx() as db:
            realname = (await db.execute(
                select(UserRealname).where(UserRealname.user_id == user_id)
            )).scalar_one_or_none()
            if realname is None:
                raise NotFoundException("实名认证信息")

            if data.status == "rejected" and realname.snapshot:
                for key, value in realname.snapshot.items():
                    setattr(realname, key, value)
                realname.snapshot = None

            realname.status = data.status
            if data.status == "verified":
                realname.verified_at = datetime.now(timezone.utc).isoformat()
                realname.snapshot = None
            else:
                realname.verified_at = None
                # Rejected identity data must not keep occupying the live
                # deterministic ID-card reservation.
                realname.active_id_card_hash = None
            db.add(
                RensheAuditLog(
                    actor_type="admin",
                    actor_id=actor_id,
                    action=f"verification.identity.{data.status}",
                    object_type="identity",
                    object_id=user_id,
                    result="succeeded",
                    summary={"status": data.status, "comment_provided": bool(data.comment)},
                )
            )
            await db.commit()

        return {"user_id": user_id, "status": data.status, "comment": data.comment}

    async def review_student(
        self, user_id: int, data: AdminIdentityReview, *, actor_id: int | None = None
    ) -> dict:
        if data.status not in ("verified", "rejected"):
            raise ValidationException("status 必须为 verified 或 rejected")

        async with get_db_ctx() as db:
            student = (await db.execute(
                select(UserStudent).where(UserStudent.user_id == user_id)
            )).scalar_one_or_none()
            if student is None:
                raise NotFoundException("学生信息")

            if data.status == "rejected" and student.snapshot:
                for key, value in student.snapshot.items():
                    if key == "enrollment_date" and isinstance(value, str):
                        value = date.fromisoformat(value)
                    setattr(student, key, value)
                student.snapshot = None

            student.status = data.status
            if data.status == "verified":
                student.verified_at = datetime.now(timezone.utc).isoformat()
                student.snapshot = None
            else:
                student.verified_at = None
            db.add(
                RensheAuditLog(
                    actor_type="admin",
                    actor_id=actor_id,
                    action=f"verification.student.{data.status}",
                    object_type="student",
                    object_id=user_id,
                    result="succeeded",
                    summary={"status": data.status, "comment_provided": bool(data.comment)},
                )
            )
            await db.commit()

        return {"user_id": user_id, "status": data.status, "comment": data.comment}

    async def review_enterprise(self, user_id: int, data: AdminIdentityReview) -> dict:
        raise ValidationException("首版已停用企业认证，只支持学生认证")
        if data.status not in ("verified", "rejected"):
            raise ValidationException("status 必须为 verified 或 rejected")

        async with get_db_ctx() as db:
            enterprise = (await db.execute(
                select(UserEnterprise).where(UserEnterprise.user_id == user_id)
            )).scalar_one_or_none()
            if enterprise is None:
                raise NotFoundException("企业信息")

            if data.status == "rejected" and enterprise.snapshot:
                for key, value in enterprise.snapshot.items():
                    setattr(enterprise, key, value)
                enterprise.snapshot = None

            enterprise.status = data.status
            if data.status == "verified":
                enterprise.verified_at = datetime.now(timezone.utc).isoformat()
                enterprise.snapshot = None
            else:
                enterprise.verified_at = None
            await db.commit()

        return {"user_id": user_id, "status": data.status, "comment": data.comment}
