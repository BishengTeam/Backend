"""用户服务 — Level 1 基础资料 + Level 2 实名/学生/企业审核。"""
from datetime import datetime, timezone

from sqlalchemy import select, update, func

from app.adapter.database import get_db_ctx
from app.adapter.redis import redis_get_safe
from app.port.exceptions import BusinessException, NotFoundException, ValidationException
from app.integrations.wechat import WechatClient
from app.integrations.identity_verify import IdentityVerifyError, verify_real_name
from app.domain.user.src.index import (
    DeletedOpenid, User, UserProfile, UserRealname, UserStudent, UserEnterprise,
)
from app.domain.order.src.index import Order
from app.domain.community.src.index import Conversation
from app.domain.review.src.index import Review
from app.utils.validators import validate_id_card
from app.utils.census import resolve_census

SESSION_KEY_PREFIX = "session_key:"
MAX_LEVEL2_EDITS = 10
LEVEL2_RESET_HOURS = 24  # 1 天 = 24 小时


# ── 帮助函数 ──

def _derived_from_id_card(id_card: str) -> tuple[str | None, int | None, str | None]:
    """从 18 位身份证号计算性别、年龄、户籍"""
    gender = None
    age = None
    census = None
    if len(id_card) != 18:
        return gender, age, census

    # 性别：第 17 位，奇数男偶数女
    try:
        digit = int(id_card[16])
        gender = "男" if digit % 2 == 1 else "女"
    except (ValueError, IndexError):
        pass

    # 年龄
    try:
        birth = id_card[6:14]
        birth_date = datetime.strptime(birth, "%Y%m%d").replace(tzinfo=timezone.utc)
        today = datetime.now(timezone.utc)
        age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
    except (ValueError, IndexError):
        pass

    # 户籍
    census = resolve_census(id_card)

    return gender, age, census


def _mask_id_card(id_card: str) -> str | None:
    if not id_card or len(id_card) != 18:
        return None
    return id_card[:4] + "**********" + id_card[-4:]


def _mask_phone(phone: str | None) -> str | None:
    if not phone or len(phone) < 7:
        return phone
    return phone[:3] + "****" + phone[-4:]


async def _get_reject_reason(target_type: str, user_id: int) -> str | None:
    """查询该用户指定类型的最新驳回理由"""
    async with get_db_ctx() as db:
        result = await db.execute(
            select(Review.comment).where(
                Review.target_type == target_type,
                Review.target_id == user_id,
                Review.action == "reject",
            ).order_by(Review.id.desc()).limit(1)
        )
        row = result.one_or_none()
        return row[0] if row else None


async def _check_level2_edit_limit(user):
    """检查 Level-2 修改次数限制。"""
    now = datetime.now(timezone.utc)
    if user.level2_edit_reset_at:
        try:
            reset_at = datetime.fromisoformat(user.level2_edit_reset_at)
            elapsed_hours = (now - reset_at).total_seconds() / 3600
            if elapsed_hours >= LEVEL2_RESET_HOURS:
                user.level2_edit_count = 0
                user.level2_edit_reset_at = None
        except (ValueError, TypeError):
            user.level2_edit_count = 0
            user.level2_edit_reset_at = None
    if user.level2_edit_count >= MAX_LEVEL2_EDITS:
        raise BusinessException(f"实名/学生/企业信息最多修改 {MAX_LEVEL2_EDITS} 次，{LEVEL2_RESET_HOURS} 小时后重置")


def _birth_date_from_id_card(id_card: str) -> str | None:
    """从 18 位身份证号提取出生日期 YYYY-MM-DD"""
    if len(id_card) != 18:
        return None
    try:
        birth = id_card[6:14]
        return f"{birth[0:4]}-{birth[4:6]}-{birth[6:8]}"
    except (ValueError, IndexError):
        return None


def _calc_edit_reset_hours(user: User) -> int | None:
    """计算还有多少小时重置修改次数。未开始使用时返回 None。"""
    if not user.level2_edit_reset_at:
        return None
    try:
        reset_at = datetime.fromisoformat(user.level2_edit_reset_at)
        elapsed = (datetime.now(timezone.utc) - reset_at).total_seconds() / 3600
        remaining = int(LEVEL2_RESET_HOURS - elapsed)
        return remaining if remaining > 0 else None
    except (ValueError, TypeError):
        return None


async def _incr_level2_edit(user: User) -> None:
    now = datetime.now(timezone.utc)
    user.level2_edit_count += 1
    if not user.level2_edit_reset_at:
        user.level2_edit_reset_at = now.isoformat()


# ── 服务 ──

class UserService:

    # ─── Level 1: 基础资料 ───

    async def get_profile(self, user_id: int, is_admin: bool = False) -> "UserProfileDetail":
        """聚合返回用户全部信息。is_admin=True 时 phone/id_card 不脱敏。"""
        from app.schemas.user import UserProfileDetail

        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None or (not is_admin and not user.is_active):
                raise NotFoundException("用户")
            profile = (await db.execute(
                select(UserProfile).where(UserProfile.user_id == user_id)
            )).scalar_one_or_none()
            realname = (await db.execute(
                select(UserRealname).where(UserRealname.user_id == user_id)
            )).scalar_one_or_none()
            student = (await db.execute(
                select(UserStudent).where(UserStudent.user_id == user_id)
            )).scalar_one_or_none()
            enterprise = (await db.execute(
                select(UserEnterprise).where(UserEnterprise.user_id == user_id)
            )).scalar_one_or_none()

            phone = profile.phone if profile else None
            return UserProfileDetail(
                id=user.id,
                openid=user.openid,
                nickname=profile.nickname if profile else None,
                email=profile.email if profile else None,
                phone=phone if is_admin else _mask_phone(phone),
                province=profile.province if profile else None,
                city=profile.city if profile else None,
                address=profile.address if profile else None,
                user_type=realname.user_type if realname else None,
                last_name_zh=realname.last_name_zh if realname else None,
                first_name_zh=realname.first_name_zh if realname else None,
                last_name_en=realname.last_name_en if realname else None,
                first_name_en=realname.first_name_en if realname else None,
                real_name=realname.real_name if realname else None,
                id_card=(
                    _mask_id_card(realname.id_card_number) if realname and not is_admin
                    else (realname.id_card_number if realname else None)
                ),
                id_card_raw=realname.id_card_number if realname and (is_admin or realname.status == 'verified') else None,
                id_card_front_oss=realname.id_card_front_oss if realname else None,
                id_card_back_oss=realname.id_card_back_oss if realname else None,
                avatar_oss=realname.avatar_oss if realname else None,
                birth_date=realname.birth_date if realname else None,
                gender=realname.gender if realname else None,
                age=realname.age if realname else None,
                census_register=realname.census_register if realname else None,
                zip_code=realname.zip_code if realname else None,
                political_status=realname.political_status if realname else None,
                ethnicity=realname.ethnicity if realname else None,
                identity_status=realname.status if realname else None,
                education=student.education if student else None,
                school=student.school if student else None,
                major=student.major if student else None,
                student_card_oss=student.student_card_oss if student else None,
                enrollment_pdf_oss=student.enrollment_pdf_oss if student else None,
                degree_cert_oss=student.degree_cert_oss if student else None,
                student_status=student.status if student else None,
                organization=enterprise.organization if enterprise else None,
                enterprise_status=enterprise.status if enterprise else None,
                identity_reject_reason=(
                    await _get_reject_reason("identity", user_id)
                    if realname and realname.status == "rejected" else None
                ),
                student_reject_reason=(
                    await _get_reject_reason("student", user_id)
                    if student and student.status == "rejected" else None
                ),
                enterprise_reject_reason=(
                    await _get_reject_reason("enterprise", user_id)
                    if enterprise and enterprise.status == "rejected" else None
                ),
                edit_count=user.level2_edit_count,
                edit_count_limit=MAX_LEVEL2_EDITS,
                edit_count_reset_hours=_calc_edit_reset_hours(user),
                created_at=user.created_at.isoformat() if user.created_at else "",
            )

    async def update_profile(self, user_id: int, data: "UserProfileUpdate") -> "UserProfileDetail":
        """修改 Level-1 基础资料（无需审核，直接生效）"""
        from app.schemas.user import UserProfileUpdate

        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None or not user.is_active:
                raise NotFoundException("用户")

            profile = (await db.execute(
                select(UserProfile).where(UserProfile.user_id == user_id)
            )).scalar_one_or_none()
            if profile is None:
                profile = UserProfile(user_id=user_id)
                db.add(profile)

            update_data = data.model_dump(exclude_unset=True)
            for key, value in update_data.items():
                if key == "phone":
                    user.phone = value
                else:
                    setattr(profile, key, value)

            await db.commit()
            return await self.get_profile(user_id)

    # ─── Level 2: 实名认证 ───

    async def submit_realname(self, user_id: int, data: "RealnameSubmit") -> "RealnameResponse":
        """提交/修改实名信息。触发审核，用户立即可见新值。"""
        from app.schemas.user import RealnameResponse

        error = validate_id_card(data.id_card_number)
        if error:
            raise ValidationException(error)

        gender, age, census = _derived_from_id_card(data.id_card_number)
        birth_date = _birth_date_from_id_card(data.id_card_number)
        zip_code_val = data.id_card_number[:6] if len(data.id_card_number) == 18 else None
        # 姓名: 优先用姓+名拼接，fallback 到 real_name
        real_name = data.real_name or (
            f"{data.last_name_zh or ''}{data.first_name_zh or ''}".strip() or None
        )

        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None or not user.is_active:
                raise NotFoundException("用户")

            await _check_level2_edit_limit(user)

            existing_realname = (await db.execute(
                select(UserRealname).where(UserRealname.user_id == user_id)
            )).scalar_one_or_none()
            snapshot = None
            if existing_realname and existing_realname.status == "verified":
                snapshot = {
                    "real_name": existing_realname.real_name,
                    "last_name_zh": existing_realname.last_name_zh,
                    "first_name_zh": existing_realname.first_name_zh,
                    "last_name_en": existing_realname.last_name_en,
                    "first_name_en": existing_realname.first_name_en,
                    "id_card_number": existing_realname.id_card_number,
                    "id_card_front_oss": existing_realname.id_card_front_oss,
                    "id_card_back_oss": existing_realname.id_card_back_oss,
                    "avatar_oss": existing_realname.avatar_oss,
                    "user_type": existing_realname.user_type,
                    "gender": existing_realname.gender,
                    "age": existing_realname.age,
                    "birth_date": existing_realname.birth_date,
                    "census_register": existing_realname.census_register,
                    "zip_code": existing_realname.zip_code,
                    "political_status": existing_realname.political_status,
                    "ethnicity": existing_realname.ethnicity,
                }

            if existing_realname:
                existing_realname.user_type = data.user_type
                existing_realname.real_name = real_name
                existing_realname.last_name_zh = data.last_name_zh
                existing_realname.first_name_zh = data.first_name_zh
                existing_realname.last_name_en = data.last_name_en
                existing_realname.first_name_en = data.first_name_en
                existing_realname.id_card_number = data.id_card_number
                existing_realname.id_card_front_oss = data.id_card_front_oss
                existing_realname.id_card_back_oss = data.id_card_back_oss
                existing_realname.avatar_oss = data.avatar_oss
                existing_realname.gender = gender
                existing_realname.age = age
                existing_realname.birth_date = birth_date
                existing_realname.census_register = census
                existing_realname.zip_code = zip_code_val
                existing_realname.political_status = data.political_status
                existing_realname.ethnicity = data.ethnicity
                existing_realname.status = "pending"
                existing_realname.snapshot = snapshot
                existing_realname.verified_at = None
            else:
                existing_realname = UserRealname(
                    user_id=user_id,
                    user_type=data.user_type,
                    real_name=real_name,
                    last_name_zh=data.last_name_zh,
                    first_name_zh=data.first_name_zh,
                    last_name_en=data.last_name_en,
                    first_name_en=data.first_name_en,
                    id_card_number=data.id_card_number,
                    id_card_front_oss=data.id_card_front_oss,
                    id_card_back_oss=data.id_card_back_oss,
                    avatar_oss=data.avatar_oss,
                    gender=gender,
                    age=age,
                    birth_date=birth_date,
                    census_register=census,
                    zip_code=zip_code_val,
                    political_status=data.political_status,
                    ethnicity=data.ethnicity,
                    status="pending",
                )
                db.add(existing_realname)

            await _incr_level2_edit(user)
            await db.commit()
            await db.refresh(existing_realname)

        return RealnameResponse(
            user_type=existing_realname.user_type,
            last_name_zh=existing_realname.last_name_zh,
            first_name_zh=existing_realname.first_name_zh,
            last_name_en=existing_realname.last_name_en,
            first_name_en=existing_realname.first_name_en,
            real_name=existing_realname.real_name,
            id_card_number=_mask_id_card(existing_realname.id_card_number),
            id_card_front_oss=existing_realname.id_card_front_oss,
            id_card_back_oss=existing_realname.id_card_back_oss,
            avatar_oss=existing_realname.avatar_oss,
            birth_date=existing_realname.birth_date,
            gender=existing_realname.gender,
            age=existing_realname.age,
            census_register=existing_realname.census_register,
            zip_code=existing_realname.zip_code,
            political_status=existing_realname.political_status,
            ethnicity=existing_realname.ethnicity,
            status=existing_realname.status,
            verified_at=existing_realname.verified_at,
        )

    async def get_realname(self, user_id: int) -> "RealnameResponse":
        """获取实名信息（用户端，身份证脱敏）"""
        from app.schemas.user import RealnameResponse

        reject_reason = await _get_reject_reason("identity", user_id)
        async with get_db_ctx() as db:
            realname = (await db.execute(
                select(UserRealname).where(UserRealname.user_id == user_id)
            )).scalar_one_or_none()
            if realname is None:
                raise NotFoundException("实名认证信息")

            return RealnameResponse(
                user_type=realname.user_type,
                last_name_zh=realname.last_name_zh,
                first_name_zh=realname.first_name_zh,
                last_name_en=realname.last_name_en,
                first_name_en=realname.first_name_en,
                real_name=realname.real_name,
                id_card_number=_mask_id_card(realname.id_card_number),
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
                reject_reason=reject_reason,
            )

    # ─── Level 2: 学生信息 ───

    async def submit_student(self, user_id: int, data: "StudentSubmit") -> "StudentResponse":
        """提交/修改学生信息。触发审核。"""
        from app.schemas.user import StudentResponse

        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None or not user.is_active:
                raise NotFoundException("用户")

            # 检查互斥：不能同时有 enterprise
            enterprise = (await db.execute(
                select(UserEnterprise).where(UserEnterprise.user_id == user_id)
            )).scalar_one_or_none()
            if enterprise:
                raise BusinessException("当前为企业用户，请先切换用户类型")

            await _check_level2_edit_limit(user)

            existing = (await db.execute(
                select(UserStudent).where(UserStudent.user_id == user_id)
            )).scalar_one_or_none()

            snapshot = None
            if existing and existing.status == "verified":
                snapshot = {
                    "education": existing.education,
                    "school": existing.school,
                    "major": existing.major,
                    "student_card_oss": existing.student_card_oss,
                    "enrollment_pdf_oss": existing.enrollment_pdf_oss,
                    "degree_cert_oss": existing.degree_cert_oss,
                }

            if existing:
                existing.education = data.education
                existing.school = data.school
                existing.major = data.major
                existing.student_card_oss = data.student_card_oss
                existing.enrollment_pdf_oss = data.enrollment_pdf_oss
                existing.degree_cert_oss = data.degree_cert_oss
                existing.status = "pending"
                existing.snapshot = snapshot
                existing.verified_at = None
            else:
                existing = UserStudent(
                    user_id=user_id,
                    education=data.education,
                    school=data.school,
                    major=data.major,
                    student_card_oss=data.student_card_oss,
                    enrollment_pdf_oss=data.enrollment_pdf_oss,
                    degree_cert_oss=data.degree_cert_oss,
                    status="pending",
                )
                db.add(existing)

            await _incr_level2_edit(user)
            await db.commit()
            await db.refresh(existing)

        return StudentResponse(
            education=existing.education,
            school=existing.school,
            major=existing.major,
            student_card_oss=existing.student_card_oss,
            enrollment_pdf_oss=existing.enrollment_pdf_oss,
            degree_cert_oss=existing.degree_cert_oss,
            status=existing.status,
            verified_at=existing.verified_at,
        )

    async def get_student(self, user_id: int) -> "StudentResponse":
        """获取学生信息"""
        from app.schemas.user import StudentResponse

        reject_reason = await _get_reject_reason("student", user_id)
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
                enrollment_pdf_oss=student.enrollment_pdf_oss,
                degree_cert_oss=student.degree_cert_oss,
                status=student.status,
                verified_at=student.verified_at,
                reject_reason=reject_reason,
            )

    # ─── Level 2: 企业信息 ───

    async def submit_enterprise(self, user_id: int, data: "EnterpriseSubmit") -> "EnterpriseResponse":
        """提交/修改企业信息。触发审核。"""
        from app.schemas.user import EnterpriseResponse

        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None or not user.is_active:
                raise NotFoundException("用户")

            # 检查互斥
            student = (await db.execute(
                select(UserStudent).where(UserStudent.user_id == user_id)
            )).scalar_one_or_none()
            if student:
                raise BusinessException("当前为学生用户，请先切换用户类型")

            await _check_level2_edit_limit(user)

            existing = (await db.execute(
                select(UserEnterprise).where(UserEnterprise.user_id == user_id)
            )).scalar_one_or_none()

            snapshot = None
            if existing and existing.status == "verified":
                snapshot = {"organization": existing.organization}

            if existing:
                existing.organization = data.organization
                existing.status = "pending"
                existing.snapshot = snapshot
                existing.verified_at = None
            else:
                existing = UserEnterprise(
                    user_id=user_id,
                    organization=data.organization,
                    status="pending",
                )
                db.add(existing)

            await _incr_level2_edit(user)
            await db.commit()
            await db.refresh(existing)

        return EnterpriseResponse(
            organization=existing.organization,
            status=existing.status,
            verified_at=existing.verified_at,
        )

    async def get_enterprise(self, user_id: int) -> "EnterpriseResponse":
        """获取企业信息"""
        from app.schemas.user import EnterpriseResponse

        reject_reason = await _get_reject_reason("enterprise", user_id)
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
                reject_reason=reject_reason,
            )

    # ─── 其他 ───

    async def delete_account(self, user_id: int) -> None:
        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None:
                raise NotFoundException("用户")
            db.add(DeletedOpenid(openid=user.openid))
            await db.execute(
                update(Order)
                .where(Order.user_id == user.id)
                .values(candidate_name="***", candidate_phone="***", candidate_idcard="***")
            )
            user.is_active = False
            await db.commit()

    async def unbind(self, user_id: int, unbind_type: str) -> None:
        """解绑手机号或微信"""
        if unbind_type == "wechat":
            raise BusinessException("暂不支持解绑微信")
        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None or not user.is_active:
                raise NotFoundException("用户")
            user.phone = None
            await db.commit()

    async def decrypt_phone(self, user_id: int, encrypted_data: str, iv: str) -> str:
        session_key = await redis_get_safe(f"{SESSION_KEY_PREFIX}{user_id}")
        if not session_key:
            raise BusinessException("登录凭证已过期，请重新登录后再绑定手机号")

        try:
            phone = WechatClient.decrypt_phone(encrypted_data, iv, session_key)
        except Exception as e:
            raise BusinessException(f"手机号解密失败，请重新授权: {e}") from e

        async with get_db_ctx() as db:
            existing = (
                await db.execute(
                    select(User).where(User.phone == phone, User.id != user_id, User.is_active == True)
                )
            ).scalar_one_or_none()
            if existing is not None:
                raise BusinessException("该手机号已被其他用户绑定")

            user = await db.get(User, user_id)
            if user is None:
                raise NotFoundException("用户")
            user.phone = phone
            await db.commit()
        return phone
