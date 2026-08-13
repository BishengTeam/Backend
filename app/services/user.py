"""用户服务 — Level 1 基础资料 + Level 2 实名/学生审核。"""
from datetime import datetime, timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.adapter.database import get_db_ctx
from app.adapter.redis import redis_get_safe
from app.port.exceptions import (
    BusinessException,
    ConflictException,
    NotFoundException,
    ValidationException,
)
from app.integrations.wechat import WechatClient
from app.integrations.identity_verify import IdentityVerifyError, verify_real_name
from app.integrations.renshe_storage import assert_owned_source_key
from app.integrations.renshe_storage import RensheObjectStorage
from app.domain.user.src.index import (
    DeletedIdentityHash,
    DeletedOpenid,
    User,
    UserEnterprise,
    UserProfile,
    UserRealname,
    UserStudent,
)
from app.domain.order.src.index import Order
from app.domain.renshe.src.index import (
    PROFILE_LOCKING_APPLICATION_STATUSES,
    RensheApplication,
    RensheAuditLog,
    RensheRefundRequest,
)
from app.domain.community.src.index import Conversation
from app.domain.review.src.index import Review
from app.utils.validators import validate_id_card
from app.utils.census import resolve_census
from app.utils.pii import identity_hash
from app.services.renshe_source import (
    delete_unreferenced_source_keys,
    profile_source_keys,
)
from pypinyin import lazy_pinyin

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


async def _assert_renshe_profile_unlocked(db, user_id: int) -> None:
    active_applications = (
        await db.execute(
            select(RensheApplication)
            .where(
                RensheApplication.user_id == user_id,
                RensheApplication.status != "closed",
            )
            .order_by(RensheApplication.id)
            .with_for_update()
        )
    ).scalars().all()
    if any(
        application.status in PROFILE_LOCKING_APPLICATION_STATUSES
        or application.frozen_at is not None
        for application in active_applications
    ):
        raise BusinessException("报名正在支付、审核或退款处理中，暂不能修改认证资料")


async def _assert_account_closure_allowed(db, user_id: int) -> None:
    """Reject soft deletion while any payment, review, or refund flow is active."""

    active_application_id = await db.scalar(
        select(RensheApplication.id)
        .where(
            RensheApplication.user_id == user_id,
            or_(
                RensheApplication.status.notin_(("draft", "closed")),
                RensheApplication.frozen_at.is_not(None),
            ),
        )
        .limit(1)
    )
    active_order_id = await db.scalar(
        select(Order.id)
        .where(
            Order.user_id == user_id,
            Order.status.in_(("pending", "paid", "completed")),
        )
        .limit(1)
    )
    active_refund_id = await db.scalar(
        select(RensheRefundRequest.id)
        .where(
            RensheRefundRequest.user_id == user_id,
            RensheRefundRequest.status.in_(("requested", "approved", "processing", "failed")),
        )
        .limit(1)
    )
    if any(value is not None for value in (active_application_id, active_order_id, active_refund_id)):
        raise BusinessException("存在未结束的报名、订单、审核或退款流程，暂不能注销账号")


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


def _to_pinyin(name: str | None) -> str | None:
    """中文姓名转拼音首字母大写，如 张三 → ZhangSan。"""
    if not name:
        return None
    return "".join(s.capitalize() for s in lazy_pinyin(name))


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
                enrollment_date=student.enrollment_date if student else None,
                student_card_oss=student.student_card_oss if student else None,
                enrollment_pdf_oss=student.enrollment_pdf_oss if student else None,
                degree_cert_oss=student.degree_cert_oss if student else None,
                student_status=student.status if student else None,
                identity_reject_reason=(
                    await _get_reject_reason("identity", user_id)
                    if realname and realname.status == "rejected" else None
                ),
                student_reject_reason=(
                    await _get_reject_reason("student", user_id)
                    if student and student.status == "rejected" else None
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
        for storage_key in (
            data.id_card_front_oss,
            data.id_card_back_oss,
            data.avatar_oss,
        ):
            assert_owned_source_key(user_id, storage_key)

        gender, age, census = _derived_from_id_card(data.id_card_number)
        birth_date = _birth_date_from_id_card(data.id_card_number)
        zip_code_val = data.id_card_number[:6] if len(data.id_card_number) == 18 else None
        # 姓名: 优先用姓+名拼接，fallback 到 real_name
        real_name = data.real_name.strip()
        # 拼音: 未传入时由中文姓/名自动计算
        last_name_en = data.last_name_en or _to_pinyin(data.last_name_zh)
        first_name_en = data.first_name_en or _to_pinyin(data.first_name_zh)

        replaced_source_keys: set[str] = set()
        async with get_db_ctx() as db:
            user = await db.scalar(
                select(User)
                .where(User.id == user_id, User.is_active.is_(True))
                .with_for_update()
            )
            if user is None:
                raise NotFoundException("用户")

            await _assert_renshe_profile_unlocked(db, user_id)
            await _check_level2_edit_limit(user)

            digest = identity_hash(data.id_card_number)
            occupied_by = await db.scalar(
                select(UserRealname.user_id)
                .join(User, User.id == UserRealname.user_id)
                .where(
                    UserRealname.user_id != user_id,
                    User.is_active.is_(True),
                    # A rejected submission no longer represents a valid
                    # identity reservation.  Pending and verified rows still
                    # reserve the digest to prevent concurrent duplicate
                    # submissions.
                    UserRealname.status.in_(("pending", "verified")),
                    or_(
                        UserRealname.active_id_card_hash == digest,
                        UserRealname.id_card_number == data.id_card_number,
                    ),
                )
                .limit(1)
            )
            if occupied_by is not None:
                raise ConflictException("该身份证号已绑定其他有效账号")

            existing_realname = (await db.execute(
                select(UserRealname).where(UserRealname.user_id == user_id)
            )).scalar_one_or_none()
            snapshot = None
            if existing_realname is not None:
                # The previous profile keys may become orphaned when the user
                # replaces a pending/rejected submission.  Keep them as
                # candidates and let the post-commit reference check decide
                # whether they are still needed by a submitted version.
                replaced_source_keys = profile_source_keys(realname=existing_realname)
            if existing_realname and existing_realname.status == "verified":
                snapshot = {
                    "real_name": existing_realname.real_name,
                    "last_name_zh": existing_realname.last_name_zh,
                    "first_name_zh": existing_realname.first_name_zh,
                    "last_name_en": existing_realname.last_name_en,
                    "first_name_en": existing_realname.first_name_en,
                    "id_card_number": existing_realname.id_card_number,
                    "id_card_hash": existing_realname.id_card_hash,
                    "active_id_card_hash": existing_realname.active_id_card_hash,
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
                existing_realname.user_type = "student"
                existing_realname.real_name = real_name
                existing_realname.last_name_zh = data.last_name_zh
                existing_realname.first_name_zh = data.first_name_zh
                existing_realname.last_name_en = last_name_en
                existing_realname.first_name_en = first_name_en
                existing_realname.id_card_number = data.id_card_number
                existing_realname.id_card_hash = digest
                existing_realname.active_id_card_hash = digest
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
                    user_type="student",
                    real_name=real_name,
                    last_name_zh=data.last_name_zh,
                    first_name_zh=data.first_name_zh,
                    last_name_en=last_name_en,
                    first_name_en=first_name_en,
                    id_card_number=data.id_card_number,
                    id_card_hash=digest,
                    active_id_card_hash=digest,
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
            db.add(
                RensheAuditLog(
                    actor_type="user",
                    actor_id=user_id,
                    action="verification.identity.submit",
                    object_type="identity",
                    object_id=user_id,
                    result="succeeded",
                    summary={"status": "pending", "material_count": 3},
                )
            )
            try:
                await db.commit()
            except IntegrityError as exc:
                await db.rollback()
                if "active_id_card_hash" in str(exc):
                    raise ConflictException("该身份证号已绑定其他有效账号") from exc
                raise
            await db.refresh(existing_realname)

        if replaced_source_keys:
            await delete_unreferenced_source_keys(
                RensheObjectStorage(), replaced_source_keys
            )

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

        for storage_key in (
            data.student_card_oss,
            data.enrollment_pdf_oss,
            data.degree_cert_oss,
        ):
            assert_owned_source_key(user_id, storage_key)

        replaced_source_keys: set[str] = set()
        async with get_db_ctx() as db:
            user = await db.scalar(
                select(User)
                .where(User.id == user_id, User.is_active.is_(True))
                .with_for_update()
            )
            if user is None:
                raise NotFoundException("用户")

            await _assert_renshe_profile_unlocked(db, user_id)
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
            if existing is not None:
                replaced_source_keys = profile_source_keys(student=existing)
            if existing and existing.status == "verified":
                snapshot = {
                    "education": existing.education,
                    "school": existing.school,
                    "major": existing.major,
                    "enrollment_date": (
                        existing.enrollment_date.isoformat()
                        if existing.enrollment_date else None
                    ),
                    "student_card_oss": existing.student_card_oss,
                    "enrollment_pdf_oss": existing.enrollment_pdf_oss,
                    "degree_cert_oss": existing.degree_cert_oss,
                }

            if existing:
                existing.education = data.education
                existing.school = data.school
                existing.major = data.major
                existing.enrollment_date = data.enrollment_date
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
                    enrollment_date=data.enrollment_date,
                    student_card_oss=data.student_card_oss,
                    enrollment_pdf_oss=data.enrollment_pdf_oss,
                    degree_cert_oss=data.degree_cert_oss,
                    status="pending",
                )
                db.add(existing)

            await _incr_level2_edit(user)
            db.add(
                RensheAuditLog(
                    actor_type="user",
                    actor_id=user_id,
                    action="verification.student.submit",
                    object_type="student",
                    object_id=user_id,
                    result="succeeded",
                    summary={"status": "pending", "material_count": 3},
                )
            )
            await db.commit()
            await db.refresh(existing)

        if replaced_source_keys:
            await delete_unreferenced_source_keys(
                RensheObjectStorage(), replaced_source_keys
            )

        return StudentResponse(
            education=existing.education,
            school=existing.school,
            major=existing.major,
            enrollment_date=existing.enrollment_date,
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
                enrollment_date=student.enrollment_date,
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
        raise BusinessException("首版已停用企业认证，只支持学生认证")
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
        raise BusinessException("首版已停用企业认证，只支持学生认证")
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
        source_keys_to_reclaim: set[str] = set()
        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None or not user.is_active:
                raise NotFoundException("用户")

            await _assert_account_closure_allowed(db, user_id)

            now = datetime.now(timezone.utc)
            drafts = (
                await db.execute(
                    select(RensheApplication).where(
                        RensheApplication.user_id == user_id,
                        RensheApplication.status == "draft",
                    )
                )
            ).scalars().all()
            for application in drafts:
                application.status = "closed"
                application.closed_at = now
                application.close_reason = "account_deleted"

            realname = await db.scalar(
                select(UserRealname).where(UserRealname.user_id == user_id)
            )
            if realname is not None:
                source_keys_to_reclaim.update(profile_source_keys(realname=realname))
                digest = realname.id_card_hash or identity_hash(realname.id_card_number)
                db.add(
                    DeletedIdentityHash(
                        former_user_id=user_id,
                        id_card_hash=digest,
                        released_at=now,
                    )
                )
                realname.id_card_hash = digest
                realname.active_id_card_hash = None
                # Keep only the irreversible equality hash after account
                # closure.  The user-facing identity row is retained for
                # referential integrity, but must not remain a second copy of
                # the full ID number or source object keys.
                realname.id_card_number = f"DELETED-{digest[:10]}"
                realname.real_name = "***"
                realname.last_name_zh = None
                realname.first_name_zh = None
                realname.last_name_en = None
                realname.first_name_en = None
                realname.id_card_front_oss = None
                realname.id_card_back_oss = None
                realname.avatar_oss = None
                realname.gender = None
                realname.age = None
                realname.birth_date = None
                realname.census_register = None
                realname.zip_code = None
                realname.political_status = None
                realname.ethnicity = None
                realname.status = "rejected"
                realname.snapshot = None
                realname.verified_at = None

            student = await db.scalar(
                select(UserStudent).where(UserStudent.user_id == user_id)
            )
            if student is not None:
                source_keys_to_reclaim.update(profile_source_keys(student=student))
                # ``student_card_oss`` is historically non-null, so use a
                # non-addressable sentinel rather than weakening the live
                # schema in an account-closure transaction.
                student.education = "deleted"
                student.school = "***"
                student.major = "***"
                student.enrollment_date = None
                student.student_card_oss = "deleted"
                student.enrollment_pdf_oss = None
                student.degree_cert_oss = None
                student.status = "rejected"
                student.snapshot = None
                student.verified_at = None

            enterprise = await db.scalar(
                select(UserEnterprise).where(UserEnterprise.user_id == user_id)
            )
            if enterprise is not None:
                enterprise.organization = "***"
                enterprise.status = "rejected"
                enterprise.snapshot = None
                enterprise.verified_at = None

            profile = await db.scalar(
                select(UserProfile).where(UserProfile.user_id == user_id)
            )
            if profile is not None:
                profile.phone = None
                profile.email = None
                profile.address = None
                profile.province = None
                profile.city = None
            db.add(DeletedOpenid(openid=user.openid))
            await db.execute(
                update(Order)
                .where(Order.user_id == user.id)
                .values(candidate_name="***", candidate_phone="***", candidate_idcard="***")
            )
            user.phone = None
            user.is_active = False
            db.add(
                RensheAuditLog(
                    actor_type="user",
                    actor_id=user_id,
                    action="user_account.delete",
                    object_type="user",
                    object_id=user_id,
                    result="succeeded",
                    summary={"identity_hash_released": realname is not None},
                )
            )
            await db.commit()

        if source_keys_to_reclaim:
            await delete_unreferenced_source_keys(
                RensheObjectStorage(), source_keys_to_reclaim
            )

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
