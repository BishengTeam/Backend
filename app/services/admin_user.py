"""管理端用户服务"""
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.port.exceptions import NotFoundException, ValidationException
from app.domain.community.src.index import Conversation
from app.domain.order.src.index import Order
from app.domain.user.src.index import (
    User, UserProfile, UserRealname, UserStudent, UserEnterprise,
)
from app.schemas.admin import (
    AdminIdentityReview,
    AdminUserConversationBrief,
    AdminUserFilter,
    AdminUserListItem,
    AdminUserOrderBrief,
    AdminUserStatusToggle,
)
from app.schemas.common import PaginatedData
from app.services.user import _derived_from_id_card
from app.utils.validators import validate_id_card


class AdminUserService:

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
                    UserEnterprise.status.label("enterprise_status"),
                )
                .outerjoin(UserRealname, UserRealname.user_id == User.id)
                .outerjoin(UserStudent, UserStudent.user_id == User.id)
                .outerjoin(UserEnterprise, UserEnterprise.user_id == User.id)
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
                if filters.enterprise_status:
                    base = base.where(UserEnterprise.status == filters.enterprise_status)
            count_stmt = select(func.count()).select_from(base.subquery())
            total = (await db.execute(count_stmt)).scalar() or 0
            stmt = base.order_by(User.id.desc()).offset((page - 1) * page_size).limit(page_size)
            result = await db.execute(stmt)
            rows = result.all()
            items = [AdminUserListItem(
                id=r.id,
                openid=r.openid,
                phone=r.phone,
                is_active=r.is_active,
                created_at=r.created_at,
                identity_status=r.identity_status,
                student_status=r.student_status,
                enterprise_status=r.enterprise_status,
            ) for r in rows]
            return PaginatedData[AdminUserListItem](
                items=items,
                total=total,
                page=page,
                page_size=page_size,
            )

    async def import_users_csv(self, filters: AdminUserFilter | None) -> str:
        import csv
        import io

        async with get_db_ctx() as db:
            base = select(User)
            need_realname = False
            need_student = False
            need_enterprise = False
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
                    base = base.outerjoin(UserRealname, UserRealname.user_id == User.id)
                    base = base.where(UserRealname.status == filters.identity_status)
                    need_realname = True
                if filters.student_status:
                    if not need_realname:
                        base = base.outerjoin(UserRealname, UserRealname.user_id == User.id)
                    base = base.outerjoin(UserStudent, UserStudent.user_id == User.id)
                    base = base.where(UserStudent.status == filters.student_status)
                if filters.enterprise_status:
                    base = base.outerjoin(UserEnterprise, UserEnterprise.user_id == User.id)
                    base = base.where(UserEnterprise.status == filters.enterprise_status)
            stmt = base.order_by(User.id.desc())
            result = await db.execute(stmt)
            users = result.scalars().all()
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["ID", "OpenID", "Phone", "IsActive", "CreatedAt"])
            for u in users:
                writer.writerow([u.id, u.openid, u.phone or "", u.is_active, u.created_at])
            return output.getvalue()

    async def get_user(self, user_id: int) -> AdminUserListItem:
        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None:
                raise NotFoundException("用户")
            return AdminUserListItem.model_validate(user)

    async def toggle_user_status(self, user_id: int, is_active: bool) -> AdminUserListItem:
        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None:
                raise NotFoundException("用户")
            user.is_active = is_active
            await db.commit()
            await db.refresh(user)
            return AdminUserListItem.model_validate(user)

    async def batch_delete(self, user_ids: list[int]) -> int:
        async with get_db_ctx() as db:
            result = await db.execute(
                select(User).where(User.id.in_(user_ids), User.is_active == True)
            )
            users = result.scalars().all()
            for user in users:
                user.is_active = False
            await db.commit()
            return len(users)

    async def get_user_orders(self, user_id: int) -> list[AdminUserOrderBrief]:
        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None:
                raise NotFoundException("用户")
            stmt = select(Order).where(Order.user_id == user_id).order_by(Order.id.desc())
            result = await db.execute(stmt)
            return [AdminUserOrderBrief.model_validate(o) for o in result.scalars().all()]

    async def update_user_profile(self, user_id: int, data: "AdminProfileUpdate") -> "UserProfileDetail":
        """管理员直接编辑用户任意字段，不触发审核，直接生效"""
        from app.schemas.admin import AdminProfileUpdate
        from app.schemas.user import UserProfileDetail

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

            for key in ("nickname", "email"):
                if key in update_data:
                    setattr(profile, key, update_data.pop(key))
            if "phone" in update_data:
                user.phone = update_data.pop("phone")

            # Level 2: user_realname
            realname = (await db.execute(
                select(UserRealname).where(UserRealname.user_id == user_id)
            )).scalar_one_or_none()
            realname_keys = ("user_type", "real_name", "id_card_number",
                             "id_card_front_oss", "id_card_back_oss")
            # 过滤非空值：只处理非空字符串的字段
            realname_updates = {k: update_data[k] for k in realname_keys
                                if k in update_data and update_data[k] not in (None, "")}
            if realname_updates:
                # 身份证校验 + 衍生字段自动推导
                if "id_card_number" in realname_updates:
                    id_card = realname_updates["id_card_number"]
                    error = validate_id_card(id_card)
                    if error:
                        raise ValidationException(error)
                    gender, age, census = _derived_from_id_card(id_card)
                    if gender is not None:
                        realname_updates["gender"] = gender
                    if age is not None:
                        realname_updates["age"] = age
                    if census is not None:
                        realname_updates["census_register"] = census
                if realname is None:
                    realname = UserRealname(user_id=user_id, user_type="student",
                                            real_name="", id_card_number="",
                                            status="verified")
                    db.add(realname)
                for key, value in realname_updates.items():
                    setattr(realname, key, value)
                for key in realname_keys:
                    update_data.pop(key, None)
            if "identity_status" in update_data:
                if realname is None:
                    # 不存在实名记录时，忽略纯状态更新（不创建空字段记录）
                    update_data.pop("identity_status")
                else:
                    realname.status = update_data.pop("identity_status")

            # Level 2: user_student
            student_keys = ("education", "school", "major", "student_card_oss")
            # 过滤非空值
            student_updates = {k: update_data[k] for k in student_keys
                               if k in update_data and update_data[k] not in (None, "")}
            if student_updates or "student_status" in update_data:
                student = (await db.execute(
                    select(UserStudent).where(UserStudent.user_id == user_id)
                )).scalar_one_or_none()
            if student_updates:
                if student is None:
                    student = UserStudent(user_id=user_id, education="", school="",
                                          major="", student_card_oss="", status="verified")
                    db.add(student)
                for key, value in student_updates.items():
                    setattr(student, key, value)
                for key in student_keys:
                    update_data.pop(key, None)
            if "student_status" in update_data:
                if student is None:
                    update_data.pop("student_status")
                else:
                    student.status = update_data.pop("student_status")

            # Level 2: user_enterprise
            enterprise = None
            need_enterprise = ("organization" in update_data and update_data["organization"] not in (None, "")) or "enterprise_status" in update_data
            if need_enterprise:
                enterprise = (await db.execute(
                    select(UserEnterprise).where(UserEnterprise.user_id == user_id)
                )).scalar_one_or_none()
            if "organization" in update_data:
                org_value = update_data.pop("organization")
                if org_value not in (None, ""):
                    if enterprise is None:
                        enterprise = UserEnterprise(user_id=user_id, organization=org_value,
                                                    status="verified")
                        db.add(enterprise)
                    else:
                        enterprise.organization = org_value
            if "enterprise_status" in update_data:
                if enterprise is None:
                    update_data.pop("enterprise_status")
                else:
                    enterprise.status = update_data.pop("enterprise_status")

            await db.commit()
            return await self.get_user_profile(user_id)

    async def get_user_conversations(self, user_id: int) -> list[AdminUserConversationBrief]:
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
            return [AdminUserConversationBrief.model_validate(c) for c in result.scalars().all()]

    # ─── 聚合查询（管理端，不脱敏） ───

    async def get_user_profile(self, user_id: int) -> "UserProfileDetail":
        from app.services.user import UserService
        return await UserService().get_profile(user_id, is_admin=True)

    async def get_user_identity(self, user_id: int) -> "RealnameAdminResponse":
        from app.schemas.user import RealnameAdminResponse

        async with get_db_ctx() as db:
            realname = (await db.execute(
                select(UserRealname).where(UserRealname.user_id == user_id)
            )).scalar_one_or_none()
            if realname is None:
                raise NotFoundException("实名认证信息")

            return RealnameAdminResponse(
                user_type=realname.user_type,
                real_name=realname.real_name,
                id_card_number=realname.id_card_number,
                id_card_front_oss=realname.id_card_front_oss,
                id_card_back_oss=realname.id_card_back_oss,
                gender=realname.gender,
                age=realname.age,
                census_register=realname.census_register,
                status=realname.status,
                verified_at=realname.verified_at,
            )

    async def get_user_student(self, user_id: int) -> "StudentResponse":
        from app.schemas.user import StudentResponse

        async with get_db_ctx() as db:
            student = (await db.execute(
                select(UserStudent).where(UserStudent.user_id == user_id)
            )).scalar_one_or_none()
            if student is None:
                raise NotFoundException("学生信息")

            return StudentResponse(
                education=student.education,
                school=student.school,
                major=student.major,
                student_card_oss=student.student_card_oss,
                status=student.status,
                verified_at=student.verified_at,
            )

    async def get_user_enterprise(self, user_id: int) -> "EnterpriseResponse":
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

    async def review_identity(self, user_id: int, data: AdminIdentityReview) -> dict:
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
            await db.commit()

        return {"user_id": user_id, "status": data.status, "comment": data.comment}

    async def review_student(self, user_id: int, data: AdminIdentityReview) -> dict:
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
                    setattr(student, key, value)
                student.snapshot = None

            student.status = data.status
            if data.status == "verified":
                student.verified_at = datetime.now(timezone.utc).isoformat()
                student.snapshot = None
            else:
                student.verified_at = None
            await db.commit()

        return {"user_id": user_id, "status": data.status, "comment": data.comment}

    async def review_enterprise(self, user_id: int, data: AdminIdentityReview) -> dict:
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
