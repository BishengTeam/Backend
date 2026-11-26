"""课程兑换码（B2）：我的码直读本表 + 本人兑换。

兑换规则（方案 7.2）：
- 码仅本人可用；不存在/已撤销/已退款/他人所有文案分开
- 课程状态：仅 published 可兑；offline / archived / draft 拒绝（恢复上线后可兑）
- 幂等：同一用户重复兑同一课返回已有授权，不消耗第二张码
- 写授权：upsert course_enrollment（source='redeem'、course_code_id）
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapter.database import get_db_ctx
from app.domain.certification.src.index import Course, CourseAuditLog, CourseCode, CourseEnrollment
from app.port.exceptions import BusinessException, NotFoundException
from app.services.course_code_crypto import decrypt_code, hash_code, normalize_code
from app.services.videoweb_admin import is_videoweb_disabled

REDEEMABLE_COURSE_STATUSES = ("published",)

STATUS_MESSAGES = {
    "revoked": "兑换码已作废，请联系客服",
    "refunded": "兑换码已退款回收，无法使用",
}


class CourseCodeService:
    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _serialize(row: CourseCode) -> dict:
        return {
            "id": row.id,
            "code": decrypt_code(row.code_cipher),
            "course_id": row.course_id,
            "status": row.status,
            "source_type": row.source,
            "source_order_id": None,
            "owner_miniapp_user_id": row.owner_user_id,
            "redeemed_by": row.owner_user_id if row.status == "redeemed" else None,
            "redeemed_at": row.redeemed_at,
            "revoked_at": row.revoked_at,
            "created_at": row.created_at,
        }

    @classmethod
    async def list_for_user(cls, user_id: int) -> list[dict]:
        async with get_db_ctx() as db:
            rows = (
                await db.scalars(
                    select(CourseCode)
                    .where(CourseCode.owner_user_id == user_id)
                    .order_by(CourseCode.id.desc())
                )
            ).all()
            return [cls._serialize(row) for row in rows]

    @classmethod
    async def redeem(cls, user_id: int, raw_code: str) -> dict:
        normalized = normalize_code(raw_code)
        if not normalized:
            raise BusinessException("请输入有效的兑换码")
        code_hash = hash_code(raw_code)

        async with get_db_ctx() as db:
            async with db.begin():
                row = (
                    await db.execute(
                        select(CourseCode)
                        .where(CourseCode.code_hash == code_hash)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if row is None:
                    raise BusinessException("兑换码不存在，请检查后重试")
                if row.status in STATUS_MESSAGES:
                    raise BusinessException(STATUS_MESSAGES[row.status])
                if row.owner_user_id != user_id:
                    raise BusinessException("该兑换码不属于当前账号")
                if await is_videoweb_disabled(db, user_id):
                    raise BusinessException("视频站访问已禁用，请联系管理员")
                course = await db.get(Course, row.course_id)
                if course is None:
                    raise NotFoundException("课程")
                if course.status not in REDEEMABLE_COURSE_STATUSES or not course.is_active:
                    raise BusinessException(
                        _course_blocked_message(course.status)
                    )
                enrollment = await cls._upsert_access(db, user_id, row)
                already = row.status == "redeemed"
                if not already:
                    row.status = "redeemed"
                    row.redeemed_at = cls._now()
                db.add(
                    CourseAuditLog(
                        actor_type="user",
                        actor_id=user_id,
                        action="course_code.redeemed",
                        object_type="course_code",
                        object_id=row.id,
                        result="succeeded",
                        summary={
                            "course_id": row.course_id,
                            "already_redeemed": already,
                            "enrollment_id": enrollment.id,
                        },
                    )
                )
                return {
                    "code_id": row.id,
                    "course_id": row.course_id,
                    "entitlement_id": enrollment.id,
                    "status": "redeemed",
                    "already_redeemed": already,
                    "learning_access": True,
                    "access_granted_at": enrollment.access_granted_at,
                }

    @classmethod
    async def _upsert_access(
        cls, db: AsyncSession, user_id: int, code: CourseCode
    ) -> CourseEnrollment:
        enrollment = (
            await db.execute(
                select(CourseEnrollment).where(
                    CourseEnrollment.user_id == user_id,
                    CourseEnrollment.course_id == code.course_id,
                )
            )
        ).scalar_one_or_none()
        if enrollment is None:
            enrollment = CourseEnrollment(
                user_id=user_id,
                course_id=code.course_id,
                status="enrolled",
                learning_access=True,
                access_granted_at=cls._now(),
                source="redeem",
                course_code_id=code.id,
            )
            db.add(enrollment)
            await db.flush()
            return enrollment
        if enrollment.learning_access and enrollment.status in ("enrolled", "completed"):
            # 幂等：已有有效授权时只做溯源补齐，不改授权时间
            if enrollment.course_code_id is None:
                enrollment.course_code_id = code.id
                enrollment.source = enrollment.source or "redeem"
            return enrollment
        enrollment.status = "enrolled"
        enrollment.learning_access = True
        enrollment.access_granted_at = cls._now()
        enrollment.access_revoked_at = None
        enrollment.source = "redeem"
        enrollment.course_code_id = code.id
        await db.flush()
        return enrollment


def _course_blocked_message(status: str) -> str:
    if status == "archived":
        return "课程已归档，暂不可兑换"
    if status == "offline":
        return "课程已下线，恢复上线后可兑换"
    return "课程未发布，暂不可兑换"
