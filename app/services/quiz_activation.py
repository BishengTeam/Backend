"""Activation codes (quiz018): per-library offline batches redeemable for entitlements.

甲方线下发放激活码；学生在小程序输入后发放 ``quiz_library_entitlement``
（source_type=activation_code），练习闸门复用 ``QuizV2Service._accessible_clause``。
码一次一用，本人重复提交幂等成功；兑换后权益永久有效（ends_at 预留未用）。
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.adapter.logging import client_ip_var, request_id_var
from app.domain.community.src.index import (
    QuizActivationCode,
    QuizAdminAuditLog,
    QuizLibrary,
    QuizLibraryEntitlement,
)
from app.domain.community.src.rule.quiz import QuizActivationCodeStatus
from app.domain.user.src.index import User
from app.port.exceptions import BusinessException, NotFoundException, ValidationException
from app.schemas.admin_quiz_contract import (
    AdminQuizActivationCodeCreate,
    AdminQuizActivationCodeItem,
    AdminQuizActivationCodeQuery,
)
from app.schemas.common import PaginatedData
from app.schemas.quiz_contract import QuizLibraryActivationResponse
from app.services.course_entitlement import CourseEntitlementService
from app.services.quiz_v2 import QuizV2Service

# 无歧义字符集：去掉 0/O/1/I/L，降低线下抄写错码率。
_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
_CODE_GROUPS = 4
_CODE_GROUP_LEN = 4
_GENERATE_ATTEMPTS = 8

ACTIVATION_CODE_CSV_HEADERS = [
    "激活码",
    "状态",
    "兑换用户ID",
    "兑换手机号",
    "兑换时间",
    "备注",
    "创建时间",
]


def normalize_activation_code(raw: str) -> str:
    """归一化输入：去空白与分隔符、转大写。落库与比对都用归一化形态。"""

    cleaned = "".join(ch for ch in raw.upper() if ch.isalnum())
    if not cleaned:
        raise ValidationException("请输入激活码")
    return cleaned


def format_activation_code(normalized: str) -> str:
    return "-".join(
        normalized[i : i + _CODE_GROUP_LEN]
        for i in range(0, len(normalized), _CODE_GROUP_LEN)
    )


def _new_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_GROUPS * _CODE_GROUP_LEN))


def _audit(
    db,
    *,
    admin_id: int,
    action: str,
    object_type: str,
    object_id: int | None,
) -> None:
    request_id = request_id_var.get()
    db.add(
        QuizAdminAuditLog(
            actor_type="admin",
            admin_id=admin_id,
            permission="quiz_library_manage",
            request_id=None if request_id == "-" else request_id,
            ip_address=client_ip_var.get(),
            action=action,
            object_type=object_type,
            object_id=object_id,
            result="succeeded",
            changed_fields=None,
        )
    )


class QuizActivationCodeService:
    """激活码的生成、兑换与管理（quiz018）。"""

    # ---------- 学生侧 ----------

    async def redeem(
        self, user_id: int, library_id: int, raw_code: str
    ) -> QuizLibraryActivationResponse:
        code = normalize_activation_code(raw_code)
        now = datetime.now(timezone.utc)
        async with get_db_ctx() as db:
            row = (
                await db.execute(
                    select(QuizActivationCode).where(QuizActivationCode.code == code).with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                raise ValidationException("激活码不存在，请核对后重试")
            if int(row.library_id) != int(library_id):
                raise ValidationException("激活码与该题库不匹配")

            if row.status == QuizActivationCodeStatus.REDEEMED:
                if int(row.redeemed_by or 0) == int(user_id):
                    # 幂等：本人重复提交同一激活码直接成功。
                    return QuizLibraryActivationResponse(library_id=int(library_id), activated=True)
                raise ValidationException("激活码已被使用")
            if row.status == QuizActivationCodeStatus.REVOKED:
                raise ValidationException("激活码已作废，请联系发放方")

            library = await db.get(QuizLibrary, int(library_id))
            if library is None:
                raise NotFoundException("题库")
            if library.access_mode != "activation_code":
                raise BusinessException("该题库当前无需激活码")

            row.status = QuizActivationCodeStatus.REDEEMED
            row.redeemed_by = int(user_id)
            row.redeemed_at = now
            db.add(
                QuizLibraryEntitlement(
                    user_id=int(user_id),
                    library_id=int(library_id),
                    activation_code_id=int(row.id),
                    source_type="activation_code",
                    status="active",
                    starts_at=now,
                    ends_at=None,
                    snapshot={
                        **CourseEntitlementService._snapshot(library),
                        "activation_code": format_activation_code(code),
                    },
                )
            )
            await db.flush()
            await QuizV2Service.sync_sessions_for_library(
                db, int(library_id), user_id=int(user_id), now=now
            )
            await db.commit()
        return QuizLibraryActivationResponse(library_id=int(library_id), activated=True)

    # ---------- 管理侧 ----------

    async def generate_codes(
        self, library_id: int, body: AdminQuizActivationCodeCreate, *, admin_id: int
    ) -> list[str]:
        async with get_db_ctx() as db:
            library = await db.get(QuizLibrary, int(library_id))
            if library is None:
                raise NotFoundException("题库")
            rows: list[QuizActivationCode] = []
            for _ in range(body.quantity):
                candidate = _new_code()
                for _attempt in range(_GENERATE_ATTEMPTS):
                    taken = await db.scalar(
                        select(QuizActivationCode.id).where(QuizActivationCode.code == candidate)
                    )
                    if taken is None:
                        break
                    candidate = _new_code()
                else:
                    raise BusinessException("激活码生成冲突，请重试")
                rows.append(
                    QuizActivationCode(
                        code=candidate,
                        library_id=int(library_id),
                        note=body.note,
                        created_by=int(admin_id),
                    )
                )
            db.add_all(rows)
            await db.flush()
            _audit(
                db,
                admin_id=int(admin_id),
                action="library.generate_activation_codes",
                object_type="library",
                object_id=int(library_id),
            )
            await db.commit()
            return [format_activation_code(row.code) for row in rows]

    async def list_codes(
        self, query: AdminQuizActivationCodeQuery
    ) -> PaginatedData[AdminQuizActivationCodeItem]:
        async with get_db_ctx() as db:
            conditions = [QuizActivationCode.library_id == int(query.library_id)]
            if query.status is not None:
                conditions.append(QuizActivationCode.status == str(query.status))
            total = await db.scalar(
                select(func.count()).select_from(QuizActivationCode).where(*conditions)
            )
            rows = list(
                (
                    await db.execute(
                        select(QuizActivationCode)
                        .where(*conditions)
                        .order_by(QuizActivationCode.id.desc())
                        .offset((query.page - 1) * query.page_size)
                        .limit(query.page_size)
                    )
                ).scalars()
            )
            phones = await self._redeemed_phones(db, rows)
            return PaginatedData(
                items=[self._item(row, phones) for row in rows],
                total=int(total or 0),
                page=query.page,
                page_size=query.page_size,
            )

    async def export_rows(
        self, library_id: int, status: QuizActivationCodeStatus | None
    ) -> list[list[str]]:
        """导出全部（或按状态筛选的）激活码，供线下发放。"""

        async with get_db_ctx() as db:
            conditions = [QuizActivationCode.library_id == int(library_id)]
            if status is not None:
                conditions.append(QuizActivationCode.status == str(status))
            rows = list(
                (
                    await db.execute(
                        select(QuizActivationCode)
                        .where(*conditions)
                        .order_by(QuizActivationCode.id.asc())
                    )
                ).scalars()
            )
            phones = await self._redeemed_phones(db, rows)
            status_labels = {
                QuizActivationCodeStatus.ISSUED: "未兑换",
                QuizActivationCodeStatus.REDEEMED: "已兑换",
                QuizActivationCodeStatus.REVOKED: "已作废",
            }
            return [
                [
                    format_activation_code(row.code),
                    status_labels.get(QuizActivationCodeStatus(row.status), row.status),
                    "" if row.redeemed_by is None else str(int(row.redeemed_by)),
                    phones.get(int(row.id), ""),
                    "" if row.redeemed_at is None else row.redeemed_at.strftime("%Y-%m-%d %H:%M:%S"),
                    row.note or "",
                    row.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                ]
                for row in rows
            ]

    async def revoke_code(self, code_id: int, *, admin_id: int) -> None:
        async with get_db_ctx() as db:
            row = (
                await db.execute(
                    select(QuizActivationCode).where(QuizActivationCode.id == int(code_id)).with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                raise NotFoundException("激活码")
            if row.status == QuizActivationCodeStatus.REDEEMED:
                raise BusinessException("已兑换的激活码不能作废")
            if row.status == QuizActivationCodeStatus.REVOKED:
                return
            row.status = QuizActivationCodeStatus.REVOKED
            _audit(
                db,
                admin_id=int(admin_id),
                action="library.revoke_activation_code",
                object_type="activation_code",
                object_id=int(code_id),
            )
            await db.commit()

    # ---------- 内部 ----------

    @staticmethod
    async def _redeemed_phones(db, rows: list[QuizActivationCode]) -> dict[int, str]:
        user_ids = {
            int(row.redeemed_by)
            for row in rows
            if row.redeemed_by is not None
        }
        if not user_ids:
            return {}
        users = (
            await db.execute(select(User.id, User.phone).where(User.id.in_(user_ids)))
        ).all()
        return {int(uid): (phone or "") for uid, phone in users}

    @staticmethod
    def _item(
        row: QuizActivationCode, phones: dict[int, str]
    ) -> AdminQuizActivationCodeItem:
        return AdminQuizActivationCodeItem(
            id=int(row.id),
            code=format_activation_code(row.code),
            status=QuizActivationCodeStatus(row.status),
            note=row.note,
            redeemed_by=None if row.redeemed_by is None else int(row.redeemed_by),
            redeemed_by_phone=phones.get(int(row.id)) or None,
            redeemed_at=row.redeemed_at,
            created_at=row.created_at,
        )
