from datetime import datetime, timezone

from sqlalchemy import select, update

from app.adapter.database import get_db_ctx
from app.port.exceptions import BusinessException, NotFoundException, ValidationException
from app.adapter.redis import redis_get_safe
from app.integrations.wechat import WechatClient
from app.integrations.identity_verify import IdentityVerifyError, verify_real_name
from app.domain.user.src.index import DeletedOpenid, User, UserIdentity
from app.domain.order.src.index import Order
from app.schemas.user import (
    UserIdentityCreate,
    UserIdentityResponse,
)
from app.utils.validators import validate_id_card

SESSION_KEY_PREFIX = "session_key:"
MAX_IDENTITY_EDITS = 3

def _mask_identity(identity: UserIdentity) -> UserIdentityResponse:
    id_card = identity.id_card_number
    masked = id_card[:4] + "**********" + id_card[-4:] if len(id_card) == 18 else "****"
    return UserIdentityResponse(
        user_type=identity.user_type,
        real_name=identity.real_name,
        id_card_number=masked,
        id_card_front_oss=identity.id_card_front_oss,
        id_card_back_oss=identity.id_card_back_oss,
        student_card_oss=identity.student_card_oss,
        status=identity.status,
        verified_at=identity.verified_at,
        created_at=identity.created_at.isoformat() if identity.created_at else "",
    )


class UserService:

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

    async def submit_identity(self, user_id: int, data: UserIdentityCreate) -> UserIdentityResponse:
        error = validate_id_card(data.id_card_number)
        if error:
            raise ValidationException(error)
        if data.user_type == "student" and not data.student_card_oss:
            raise ValidationException("学生用户必须上传学生证")

        # 核验姓名+身份证号（仅当 provider 不为 none 时调用）
        verify_passed: bool | None = None
        try:
            verify_passed = await verify_real_name(data.real_name, data.id_card_number)
        except IdentityVerifyError:
            pass  # 核验服务不可用，降级为未核验

        # 确定 status: verified / rejected / pending
        if verify_passed is True:
            identity_status = "verified"
            verified_at = datetime.now(timezone.utc).isoformat()
        elif verify_passed is False:
            identity_status = "rejected"
            verified_at = None
        else:
            identity_status = "pending"  # 未核验或核验服务不可用
            verified_at = None

        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None or not user.is_active:
                raise NotFoundException("用户")
            existing = (
                await db.execute(
                    select(UserIdentity).where(UserIdentity.user_id == user_id)
                )
            ).scalar_one_or_none()
            if existing is not None:
                if existing.edit_count >= MAX_IDENTITY_EDITS:
                    raise BusinessException("实名信息最多修改 3 次，已达上限")
                existing.user_type = data.user_type
                existing.real_name = data.real_name
                existing.id_card_number = data.id_card_number
                existing.id_card_front_oss = data.id_card_front_oss
                existing.id_card_back_oss = data.id_card_back_oss
                existing.student_card_oss = data.student_card_oss
                existing.status = identity_status
                existing.verified_at = verified_at
                existing.edit_count += 1
                await db.commit()
                await db.refresh(existing)
                return _mask_identity(existing)
            identity = UserIdentity(
                user_id=user_id,
                status=identity_status,
                verified_at=verified_at,
                **data.model_dump(),
            )
            db.add(identity)
            await db.commit()
            await db.refresh(identity)
            return _mask_identity(identity)

    async def get_identity(self, user_id: int) -> UserIdentityResponse:
        async with get_db_ctx() as db:
            identity = (
                await db.execute(
                    select(UserIdentity).where(UserIdentity.user_id == user_id)
                )
            ).scalar_one_or_none()
            if identity is None:
                raise NotFoundException("实名认证信息")
            return _mask_identity(identity)

    async def get_profile(self, user_id: int) -> "UserProfileDetail":
        """获取用户个人信息，含脱敏手机号、身份证号和扩展字段"""
        from app.schemas.user import UserProfileDetail

        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None or not user.is_active:
                raise NotFoundException("用户")
            identity = (
                await db.execute(
                    select(UserIdentity).where(UserIdentity.user_id == user_id)
                )
            ).scalar_one_or_none()

            phone = user.phone
            if phone and len(phone) >= 7:
                phone = phone[:3] + "****" + phone[-4:]

            id_card = None
            if identity and identity.id_card_number:
                idn = identity.id_card_number
                id_card = idn[:4] + "**********" + idn[-4:] if len(idn) == 18 else "****"

            # ── 报名预填扩展字段（仅已实名时返回）──
            phone_raw = None
            id_card_raw = None
            pinyin_str = None
            first_name = None
            last_name = None
            age = None

            if identity and identity.status == "verified":
                phone_raw = user.phone
                if identity.id_card_number and len(identity.id_card_number) == 18:
                    id_card_raw = identity.id_card_number
                    try:
                        birth = identity.id_card_number[6:14]
                        birth_date = datetime.strptime(birth, "%Y%m%d").replace(tzinfo=timezone.utc)
                        today = datetime.now(timezone.utc)
                        age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
                    except (ValueError, IndexError):
                        pass
                if identity.real_name:
                    try:
                        from app.services.certification import CertificationService
                        pinyin_str = CertificationService.convert_to_pinyin(identity.real_name)
                        parts = pinyin_str.split()
                        if len(parts) >= 2:
                            last_name = parts[0]
                            first_name = " ".join(parts[1:])
                        else:
                            last_name = pinyin_str
                    except Exception:
                        pass

            return UserProfileDetail(
                id=user.id,
                openid=user.openid,
                phone=phone,
                email=identity.email if identity else None,
                real_name=identity.real_name if identity else None,
                id_card=id_card,
                user_type=identity.user_type if identity else None,
                gender=identity.gender if identity else None,
                education=identity.education if identity else None,
                school=identity.school if identity else None,
                major=identity.major if identity else None,
                organization=identity.organization if identity else None,
                identity_status=identity.status if identity else None,
                created_at=user.created_at.isoformat() if user.created_at else "",
                phone_raw=phone_raw,
                id_card_raw=id_card_raw,
                pinyin=pinyin_str,
                first_name=first_name,
                last_name=last_name,
                age=age,
            )

    async def update_profile(self, user_id: int, data: "UserProfileUpdate") -> "UserProfileDetail":
        """编辑个人信息，含 edit_count 限制；只更新传入的字段"""
        from app.schemas.user import UserProfileUpdate

        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None or not user.is_active:
                raise NotFoundException("用户")

            identity = (
                await db.execute(
                    select(UserIdentity).where(UserIdentity.user_id == user_id)
                )
            ).scalar_one_or_none()

            if identity and identity.edit_count >= MAX_IDENTITY_EDITS:
                raise BusinessException("实名信息最多修改 3 次，已达上限")

            update_data = data.model_dump(exclude_unset=True)

            if "phone" in update_data:
                user.phone = update_data.pop("phone")

            if update_data and identity:
                for key, value in update_data.items():
                    setattr(identity, key, value)
                identity.edit_count += 1

            await db.commit()
            await db.refresh(user)
            return await self.get_profile(user_id)

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
            # 检查手机号是否已被其他用户绑定
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