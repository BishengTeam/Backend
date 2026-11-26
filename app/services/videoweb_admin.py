"""B6 学习授权页：学习者列表、授权清单、禁用/启用视频站访问。"""

from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapter.database import get_db_ctx
from app.domain.certification.src.index import (
    Course,
    CourseAuditLog,
    CourseEnrollment,
    UserChapterProgress,
)
from app.domain.user.src.index import User
from app.domain.user.src.model.user_realname import UserRealname
from app.domain.user.src.model.user_profile import UserProfile
from app.port.exceptions import NotFoundException, ValidationException
from app.services.course_code_admin import CourseCodeAdminService, _mask_phone


class VideoWebAdminService:
    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @classmethod
    async def list_learners(
        cls, *, keyword: str | None, page: int, page_size: int
    ) -> dict:
        text = (keyword or "").strip()
        async with get_db_ctx() as db:
            conditions = []
            if text:
                if len(text) < 2:
                    raise ValidationException("请输入至少 2 个字符的关键词")
                profile_ids = select(UserProfile.user_id).where(
                    or_(
                        UserProfile.nickname.ilike(f"%{text}%"),
                        UserProfile.declared_name.ilike(f"%{text}%"),
                    )
                )
                realname_ids = select(UserRealname.user_id).where(
                    UserRealname.real_name.ilike(f"%{text}%")
                )
                conditions.append(
                    or_(
                        User.phone.ilike(f"{text}%"),
                        User.id.in_(profile_ids.union(realname_ids)),
                    )
                )
            base = select(User).where(*conditions)
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
            user_ids = {user.id for user in users}
            owners = await CourseCodeAdminService._owners(db, user_ids)
            counts = dict(
                (
                    await db.execute(
                        select(CourseEnrollment.user_id, func.count())
                        .where(
                            CourseEnrollment.user_id.in_(user_ids),
                            CourseEnrollment.learning_access.is_(True),
                            CourseEnrollment.status.in_(("enrolled", "completed")),
                        )
                        .group_by(CourseEnrollment.user_id)
                    )
                ).all()
            ) if user_ids else {}
            last_learned = dict(
                (
                    await db.execute(
                        select(
                            UserChapterProgress.user_id,
                            func.max(UserChapterProgress.updated_at),
                        )
                        .where(UserChapterProgress.user_id.in_(user_ids))
                        .group_by(UserChapterProgress.user_id)
                    )
                ).all()
            ) if user_ids else {}
            items = []
            for user in users:
                owner = owners.get(user.id, {})
                items.append(
                    {
                        "user_id": user.id,
                        "display_name": owner.get("display_name")
                        or f"小程序用户 #{user.id}",
                        "phone": _mask_phone(user.phone),
                        "created_at": user.created_at,
                        "entitlement_count": int(counts.get(user.id, 0)),
                        "last_learned_at": last_learned.get(user.id),
                        "access_disabled_at": user.videoweb_access_disabled_at,
                        "is_active": user.is_active,
                    }
                )
            return {"items": items, "total": total, "page": page, "page_size": page_size}

    @classmethod
    async def list_entitlements(cls, user_id: int) -> list[dict]:
        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None:
                raise NotFoundException("小程序用户")
            rows = (
                await db.execute(
                    select(CourseEnrollment, Course)
                    .join(Course, Course.id == CourseEnrollment.course_id)
                    .where(CourseEnrollment.user_id == user_id)
                    .order_by(CourseEnrollment.id.desc())
                )
            ).all()
            return [
                {
                    "enrollment_id": enrollment.id,
                    "course_id": course.id,
                    "course_title": course.title,
                    "course_status": course.status,
                    "status": enrollment.status,
                    "learning_access": enrollment.learning_access,
                    "source": enrollment.source,
                    "course_code_id": enrollment.course_code_id,
                    "access_granted_at": enrollment.access_granted_at,
                    "access_revoked_at": enrollment.access_revoked_at,
                }
                for enrollment, course in rows
            ]

    @classmethod
    async def set_access(cls, user_id: int, *, enabled: bool, admin_id: int) -> dict:
        async with get_db_ctx() as db:
            async with db.begin():
                user = await db.get(User, user_id)
                if user is None:
                    raise NotFoundException("小程序用户")
                user.videoweb_access_disabled_at = None if enabled else cls._now()
                db.add(
                    CourseAuditLog(
                        actor_type="admin",
                        actor_id=admin_id,
                        action="videoweb.access_enabled" if enabled else "videoweb.access_disabled",
                        object_type="user",
                        object_id=user_id,
                        result="succeeded",
                        summary={"enabled": enabled},
                    )
                )
                return {
                    "user_id": user.id,
                    "access_disabled_at": user.videoweb_access_disabled_at,
                    "enabled": enabled,
                }


async def is_videoweb_disabled(db: AsyncSession, user_id: int) -> bool:
    """视频站判权统一入口（B6）：禁用用户不能看目录、不能播放、不能兑换。"""
    disabled_at = await db.scalar(
        select(User.videoweb_access_disabled_at).where(User.id == user_id)
    )
    return disabled_at is not None
