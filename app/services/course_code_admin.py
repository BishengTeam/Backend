"""管理端课程兑换码（B5）：本地落库、必选归属人、撤销/退款留痕、用户搜索。

Admin 端点路径保持不变，仅把视频站代理替换为 Backend 本地表读写。
"""

from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapter.database import get_db_ctx
from app.domain.certification.src.index import (
    Course,
    CourseAuditLog,
    CourseCode,
    CourseEnrollment,
)
from app.domain.user.src.index import User
from app.domain.user.src.model.user_realname import UserRealname
from app.domain.user.src.model.user_profile import UserProfile
from app.port.exceptions import BusinessException, NotFoundException, ValidationException
from app.services.course_code_crypto import decrypt_code, encrypt_code, generate_code, hash_code

ACTION_STATUS = {"revoke": "revoked", "refund": "refunded"}


def _mask_phone(phone: str | None) -> str | None:
    if not phone or len(phone) < 7:
        return phone
    return f"{phone[:3]}****{phone[-4:]}"


def owner_display_name(
    *, real_name: str | None, declared_name: str | None, nickname: str | None,
    phone: str | None, user_id: int,
) -> str:
    """兑换人展示名逐级回退（方案决策 2）。"""
    for candidate in (real_name, declared_name, nickname):
        if candidate and candidate.strip():
            return candidate.strip()
    masked = _mask_phone(phone)
    return masked or f"小程序用户 #{user_id}"


class CourseCodeAdminService:
    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    async def _owners(db: AsyncSession, user_ids: set[int]) -> dict[int, dict]:
        if not user_ids:
            return {}
        users = (
            await db.execute(select(User).where(User.id.in_(user_ids)))
        ).scalars().all()
        profiles = {
            row.user_id: row
            for row in (
                await db.execute(
                    select(UserProfile).where(UserProfile.user_id.in_(user_ids))
                )
            ).scalars()
        }
        identities = {
            row.user_id: row
            for row in (
                await db.execute(
                    select(UserRealname).where(
                        UserRealname.user_id.in_(user_ids),
                        UserRealname.status == "verified",
                    )
                )
            ).scalars()
        }
        result: dict[int, dict] = {}
        for user in users:
            profile = profiles.get(user.id)
            identity = identities.get(user.id)
            real_name = identity.real_name if identity else None
            declared = profile.declared_name if profile else None
            nickname = profile.nickname if profile else None
            phone = user.phone or (profile.phone if profile else None)
            result[user.id] = {
                "user_id": user.id,
                "display_name": owner_display_name(
                    real_name=real_name,
                    declared_name=declared,
                    nickname=nickname,
                    phone=phone,
                    user_id=user.id,
                ),
                "real_name": real_name,
                "declared_name": declared,
                "nickname": nickname,
                "phone": _mask_phone(phone),
            }
        return result

    @staticmethod
    def _serialize(row: CourseCode, owner: dict | None) -> dict:
        return {
            "id": row.id,
            "code": decrypt_code(row.code_cipher),
            "course_id": row.course_id,
            "status": row.status,
            "source_type": row.source,
            "source_order_id": None,
            "owner_user_id": row.owner_user_id,
            "owner": owner,
            "redeemed_at": row.redeemed_at,
            "revoked_at": row.revoked_at,
            "revoke_reason": row.revoke_reason,
            "revoked_by": row.revoked_by,
            "note": row.note,
            "created_at": row.created_at,
        }

    @classmethod
    async def list_codes(
        cls,
        *,
        course_id: int | None,
        status: str | None,
        page: int,
        page_size: int,
        owner_user_id: int | None = None,
    ) -> dict:
        async with get_db_ctx() as db:
            conditions = []
            if course_id is not None:
                conditions.append(CourseCode.course_id == course_id)
            if status:
                conditions.append(CourseCode.status == status)
            if owner_user_id is not None:
                conditions.append(CourseCode.owner_user_id == owner_user_id)
            total = int(
                await db.scalar(
                    select(func.count()).select_from(CourseCode).where(*conditions)
                )
                or 0
            )
            rows = (
                await db.execute(
                    select(CourseCode)
                    .where(*conditions)
                    .order_by(CourseCode.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            owners = await cls._owners(db, {row.owner_user_id for row in rows if row.owner_user_id})
            return {
                "items": [cls._serialize(row, owners.get(row.owner_user_id)) for row in rows],
                "total": total,
                "page": page,
                "page_size": page_size,
            }

    @classmethod
    async def issue(
        cls,
        *,
        course_id: int,
        quantity: int,
        owner_user_id: int,
        note: str | None,
        admin_id: int,
    ) -> list[dict]:
        async with get_db_ctx() as db:
            async with db.begin():
                course = await db.get(Course, course_id)
                if course is None:
                    raise NotFoundException("课程")
                owner = await db.get(User, owner_user_id)
                if owner is None or not owner.is_active:
                    raise ValidationException("归属用户不存在或已停用")
                created: list[tuple[CourseCode, str]] = []
                for _ in range(quantity):
                    raw = generate_code()
                    row = CourseCode(
                        code_hash=hash_code(raw),
                        code_cipher=encrypt_code(raw),
                        course_id=course_id,
                        owner_user_id=owner_user_id,
                        status="issued",
                        source="admin",
                        note=note,
                        created_by=f"admin:{admin_id}",
                    )
                    db.add(row)
                    created.append((row, raw))
                await db.flush()
                for row, _raw in created:
                    db.add(
                        CourseAuditLog(
                            actor_type="admin",
                            actor_id=admin_id,
                            action="course_code.issued",
                            object_type="course_code",
                            object_id=row.id,
                            result="succeeded",
                            summary={"course_id": course_id, "owner_user_id": owner_user_id},
                        )
                    )
                return [
                    {"id": row.id, "code": raw, "course_id": row.course_id, "status": row.status}
                    for row, raw in created
                ]

    @classmethod
    async def change_status(
        cls, code_id: int, action: str, reason: str | None, admin_id: int
    ) -> dict:
        target = ACTION_STATUS.get(action)
        if target is None:
            raise ValidationException("不支持的操作")
        async with get_db_ctx() as db:
            async with db.begin():
                row = await db.scalar(
                    select(CourseCode).where(CourseCode.id == code_id).with_for_update()
                )
                if row is None:
                    raise NotFoundException("兑换码")
                if row.status == target:
                    return cls._serialize(row, None) | {"already": True}
                if row.status == "refunded" and action == "revoke":
                    raise BusinessException("兑换码已退款回收，无法撤销")
                was_redeemed = row.status == "redeemed"
                row.status = target
                row.revoked_at = cls._now()
                row.revoke_reason = (reason or "").strip()[:128] or None
                row.revoked_by = admin_id
                if was_redeemed:
                    await cls._revoke_enrollment(db, row)
                db.add(
                    CourseAuditLog(
                        actor_type="admin",
                        actor_id=admin_id,
                        action=f"course_code.{action}d",
                        object_type="course_code",
                        object_id=row.id,
                        result="succeeded",
                        summary={
                            "reason": row.revoke_reason,
                            "revoked_enrollment": was_redeemed,
                        },
                    )
                )
                return cls._serialize(row, None)

    @staticmethod
    async def _revoke_enrollment(db: AsyncSession, code: CourseCode) -> None:
        enrollment = await db.scalar(
            select(CourseEnrollment).where(
                CourseEnrollment.course_code_id == code.id,
                CourseEnrollment.learning_access.is_(True),
            )
        )
        if enrollment is None:
            return
        enrollment.status = "refunded"
        enrollment.learning_access = False
        enrollment.access_revoked_at = datetime.now(timezone.utc)

    @classmethod
    async def search_users(
        cls, *, keyword: str | None, page: int, page_size: int
    ) -> dict:
        text = (keyword or "").strip()
        if len(text) < 2:
            raise ValidationException("请输入至少 2 个字符的手机号、姓名或昵称")
        async with get_db_ctx() as db:
            profile_ids = (
                select(UserProfile.user_id)
                .where(
                    or_(
                        UserProfile.nickname.ilike(f"%{text}%"),
                        UserProfile.declared_name.ilike(f"%{text}%"),
                    )
                )
            )
            identity_ids = select(UserRealname.user_id).where(
                UserRealname.real_name.ilike(f"%{text}%")
            )
            matched_ids = profile_ids.union(identity_ids)
            base = select(User).where(
                or_(User.phone.ilike(f"{text}%"), User.id.in_(matched_ids))
            )
            total = int(
                await db.scalar(select(func.count()).select_from(base.subquery())) or 0
            )
            users = (
                await db.execute(
                    base.order_by(User.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            owners = await cls._owners(db, {user.id for user in users})
            return {
                "items": [owners[user.id] for user in users if user.id in owners],
                "total": total,
                "page": page,
                "page_size": page_size,
            }
