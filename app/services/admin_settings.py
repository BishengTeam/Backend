from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.adapter.database import get_db_ctx
from app.domain.user.src.index import (
    AdminPasswordHistory,
    AdminSecurityAudit,
    AdminUser,
)
from app.port.exceptions import BusinessException, ConflictException, NotFoundException
from app.schemas.admin_settings import (
    AdminSettingsTemporaryPasswordResponse,
    AdminSettingsUserCreate,
    AdminSettingsUserListItem,
    AdminSettingsUserUpdate,
)
from app.schemas.common import PaginatedData
from app.services.admin_auth import AdminAuthService
from app.services.admin_security_audit import (
    AdminAuditContext,
    AdminSecurityAuditService,
    admin_idempotency_digest,
)


ADMIN_CREDENTIAL_IDEMPOTENCY_CONSTRAINT = (
    "uq_admin_security_audit_credential_idempotency"
)
ADMIN_USERNAME_NORMALIZED_CONSTRAINT = "uq_admin_user_username_normalized"
ADMIN_CREDENTIAL_REPLAY_MESSAGE = (
    "相同幂等键的操作已经完成；临时密码不会再次返回，请刷新列表，"
    "必要时使用新的幂等键发起密码重置"
)


class AdminSettingsService:
    audit = AdminSecurityAuditService()
    auth = AdminAuthService()

    async def list_admins(
        self,
        page: int,
        page_size: int,
        *,
        search: str | None = None,
        role: str | None = None,
        is_active: bool | None = None,
        is_locked: bool | None = None,
        must_change_password: bool | None = None,
    ) -> PaginatedData[AdminSettingsUserListItem]:
        filters = []
        if search and search.strip():
            pattern = f"%{search.strip()}%"
            filters.append(
                or_(
                    AdminUser.username.ilike(pattern),
                    AdminUser.display_name.ilike(pattern),
                )
            )
        if role:
            filters.append(AdminUser.role == role)
        if is_active is not None:
            filters.append(AdminUser.is_active.is_(is_active))
        if must_change_password is not None:
            filters.append(AdminUser.must_change_password.is_(must_change_password))
        if is_locked is not None:
            now = datetime.now(timezone.utc)
            locked_expression = AdminUser.locked_until > now
            filters.append(
                locked_expression
                if is_locked
                else or_(AdminUser.locked_until.is_(None), AdminUser.locked_until <= now)
            )

        async with get_db_ctx() as db:
            base = select(AdminUser).where(*filters)
            total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
            admins = (
                await db.execute(
                    base.order_by(AdminUser.id)
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            return PaginatedData[AdminSettingsUserListItem](
                items=[AdminSettingsUserListItem.model_validate(item) for item in admins],
                total=total,
                page=page,
                page_size=page_size,
            )

    async def create_admin(
        self,
        data: AdminSettingsUserCreate,
        *,
        actor_id: int,
        idempotency_key: str,
        context: AdminAuditContext | None = None,
    ) -> AdminSettingsTemporaryPasswordResponse:
        try:
            return await self._create_admin(
                data,
                actor_id=actor_id,
                idempotency_key=idempotency_key,
                context=context,
            )
        except IntegrityError as exc:
            conflict = self._integrity_conflict(
                exc,
                fallback_message="管理员用户名已存在",
                fallback_constraint=ADMIN_USERNAME_NORMALIZED_CONSTRAINT,
            )
            if conflict is None:
                await self._record_failure(
                    action="admin_account.create",
                    actor_id=actor_id,
                    username=data.username,
                    context=context,
                    exc=exc,
                )
                raise
            await self._record_failure(
                action="admin_account.create",
                actor_id=actor_id,
                username=data.username,
                context=context,
                exc=conflict,
            )
            raise conflict from exc
        except Exception as exc:
            await self._record_failure(
                action="admin_account.create",
                actor_id=actor_id,
                username=data.username,
                context=context,
                exc=exc,
            )
            raise

    async def _create_admin(
        self,
        data: AdminSettingsUserCreate,
        *,
        actor_id: int,
        idempotency_key: str,
        context: AdminAuditContext | None,
    ) -> AdminSettingsTemporaryPasswordResponse:
        temporary_password = self.auth.generate_temporary_password(username=data.username)
        password_hash = await asyncio.to_thread(
            self.auth.hash_password, temporary_password
        )
        async with get_db_ctx() as db:
            await self._ensure_idempotency_available(
                db,
                actor_id=actor_id,
                action="admin_account.create",
                idempotency_key=idempotency_key,
            )
            existing = await db.scalar(
                select(AdminUser.id)
                .where(func.lower(AdminUser.username) == data.username)
                .limit(1)
            )
            if existing is not None:
                raise ConflictException("管理员用户名已存在")
            admin = AdminUser(
                username=data.username,
                display_name=data.display_name,
                password_hash=password_hash,
                role="quiz_admin",
                is_active=True,
                must_change_password=True,
                auth_version=1,
                failed_login_attempts=0,
                locked_until=None,
            )
            db.add(admin)
            await db.flush()
            db.add(
                AdminPasswordHistory(
                    admin_id=admin.id,
                    password_hash=password_hash,
                )
            )
            self.audit.append(
                db,
                action="admin_account.create",
                result="succeeded",
                reason_code="quiz_admin_created",
                actor_admin_id=actor_id,
                target_admin_id=admin.id,
                username=admin.username,
                context=context,
                summary={"role": "quiz_admin"},
                idempotency_key=idempotency_key,
            )
            await db.commit()
            await db.refresh(admin)
            return AdminSettingsTemporaryPasswordResponse(
                admin=AdminSettingsUserListItem.model_validate(admin),
                temporary_password=temporary_password,
            )

    async def update_admin(
        self,
        admin_id: int,
        data: AdminSettingsUserUpdate,
        *,
        actor_id: int,
        context: AdminAuditContext | None = None,
    ) -> AdminSettingsUserListItem:
        try:
            async with get_db_ctx() as db:
                admin = await self._get_manageable_admin(db, admin_id)
                admin.display_name = data.display_name
                self.audit.append(
                    db,
                    action="admin_account.update",
                    result="succeeded",
                    reason_code="display_name_changed",
                    actor_admin_id=actor_id,
                    target_admin_id=admin.id,
                    username=admin.username,
                    context=context,
                    summary={"changed_fields": ["display_name"]},
                )
                await db.commit()
                await db.refresh(admin)
                return AdminSettingsUserListItem.model_validate(admin)
        except Exception as exc:
            await self._record_failure(
                action="admin_account.update",
                actor_id=actor_id,
                target_id=admin_id,
                context=context,
                exc=exc,
            )
            raise

    async def disable_admin(
        self,
        admin_id: int,
        *,
        actor_id: int,
        context: AdminAuditContext | None = None,
    ) -> AdminSettingsUserListItem:
        try:
            async with get_db_ctx() as db:
                admin = await self._get_manageable_admin(db, admin_id)
                if not admin.is_active:
                    raise BusinessException("管理员账号已经停用")
                admin.is_active = False
                admin.auth_version += 1
                self.audit.append(
                    db,
                    action="admin_account.disable",
                    result="succeeded",
                    reason_code="account_disabled",
                    actor_admin_id=actor_id,
                    target_admin_id=admin.id,
                    username=admin.username,
                    context=context,
                    summary={"sessions_invalidated": True},
                )
                await db.commit()
                await db.refresh(admin)
                return AdminSettingsUserListItem.model_validate(admin)
        except Exception as exc:
            await self._record_failure(
                action="admin_account.disable",
                actor_id=actor_id,
                target_id=admin_id,
                context=context,
                exc=exc,
            )
            raise

    async def enable_admin(
        self,
        admin_id: int,
        *,
        actor_id: int,
        idempotency_key: str,
        context: AdminAuditContext | None = None,
    ) -> AdminSettingsTemporaryPasswordResponse:
        try:
            return await self._set_temporary_password(
                admin_id,
                actor_id=actor_id,
                idempotency_key=idempotency_key,
                context=context,
                require_inactive=True,
                action="admin_account.enable",
                reason_code="account_enabled_with_temporary_password",
            )
        except IntegrityError as exc:
            conflict = self._integrity_conflict(exc)
            if conflict is None:
                await self._record_failure(
                    action="admin_account.enable",
                    actor_id=actor_id,
                    target_id=admin_id,
                    context=context,
                    exc=exc,
                )
                raise
            await self._record_failure(
                action="admin_account.enable",
                actor_id=actor_id,
                target_id=admin_id,
                context=context,
                exc=conflict,
            )
            raise conflict from exc
        except Exception as exc:
            await self._record_failure(
                action="admin_account.enable",
                actor_id=actor_id,
                target_id=admin_id,
                context=context,
                exc=exc,
            )
            raise

    async def reset_password(
        self,
        admin_id: int,
        *,
        actor_id: int,
        idempotency_key: str,
        context: AdminAuditContext | None = None,
    ) -> AdminSettingsTemporaryPasswordResponse:
        try:
            return await self._set_temporary_password(
                admin_id,
                actor_id=actor_id,
                idempotency_key=idempotency_key,
                context=context,
                require_inactive=False,
                action="admin_account.password_reset",
                reason_code="temporary_password_issued",
            )
        except IntegrityError as exc:
            conflict = self._integrity_conflict(exc)
            if conflict is None:
                await self._record_failure(
                    action="admin_account.password_reset",
                    actor_id=actor_id,
                    target_id=admin_id,
                    context=context,
                    exc=exc,
                )
                raise
            await self._record_failure(
                action="admin_account.password_reset",
                actor_id=actor_id,
                target_id=admin_id,
                context=context,
                exc=conflict,
            )
            raise conflict from exc
        except Exception as exc:
            await self._record_failure(
                action="admin_account.password_reset",
                actor_id=actor_id,
                target_id=admin_id,
                context=context,
                exc=exc,
            )
            raise

    async def _set_temporary_password(
        self,
        admin_id: int,
        *,
        actor_id: int,
        idempotency_key: str,
        context: AdminAuditContext | None,
        require_inactive: bool,
        action: str,
        reason_code: str,
    ) -> AdminSettingsTemporaryPasswordResponse:
        async with get_db_ctx() as db:
            await self._ensure_idempotency_available(
                db,
                actor_id=actor_id,
                action=action,
                idempotency_key=idempotency_key,
            )
            admin = await self._get_manageable_admin(db, admin_id)
            if require_inactive and admin.is_active:
                raise BusinessException("管理员账号当前处于启用状态")
            if not require_inactive and not admin.is_active:
                raise BusinessException("停用账号必须通过重新启用操作设置新临时密码")
            while True:
                temporary_password = self.auth.generate_temporary_password(
                    username=admin.username
                )
                try:
                    await self.auth._ensure_not_recent_password(
                        db, admin, temporary_password
                    )
                except BusinessException:
                    continue
                break
            password_hash = await asyncio.to_thread(
                self.auth.hash_password, temporary_password
            )
            admin.password_hash = password_hash
            admin.password_changed_at = datetime.now(timezone.utc)
            admin.must_change_password = True
            admin.failed_login_attempts = 0
            admin.locked_until = None
            admin.auth_version += 1
            if require_inactive:
                admin.is_active = True
            db.add(
                AdminPasswordHistory(
                    admin_id=admin.id,
                    password_hash=password_hash,
                )
            )
            self.audit.append(
                db,
                action=action,
                result="succeeded",
                reason_code=reason_code,
                actor_admin_id=actor_id,
                target_admin_id=admin.id,
                username=admin.username,
                context=context,
                summary={"sessions_invalidated": True},
                idempotency_key=idempotency_key,
            )
            await db.flush()
            await self.auth._prune_password_history(db, admin.id)
            await db.commit()
            await db.refresh(admin)
            return AdminSettingsTemporaryPasswordResponse(
                admin=AdminSettingsUserListItem.model_validate(admin),
                temporary_password=temporary_password,
            )

    async def unlock_admin(
        self,
        admin_id: int,
        *,
        actor_id: int,
        context: AdminAuditContext | None = None,
    ) -> AdminSettingsUserListItem:
        try:
            async with get_db_ctx() as db:
                admin = await self._get_manageable_admin(db, admin_id)
                now = datetime.now(timezone.utc)
                if admin.locked_until is None or admin.locked_until <= now:
                    raise BusinessException("管理员账号当前未被临时锁定")
                admin.locked_until = None
                admin.failed_login_attempts = 0
                self.audit.append(
                    db,
                    action="admin_account.unlock",
                    result="succeeded",
                    reason_code="manually_unlocked",
                    actor_admin_id=actor_id,
                    target_admin_id=admin.id,
                    username=admin.username,
                    context=context,
                )
                await db.commit()
                await db.refresh(admin)
                return AdminSettingsUserListItem.model_validate(admin)
        except Exception as exc:
            await self._record_failure(
                action="admin_account.unlock",
                actor_id=actor_id,
                target_id=admin_id,
                context=context,
                exc=exc,
            )
            raise

    @staticmethod
    async def _get_manageable_admin(db, admin_id: int) -> AdminUser:
        admin = (
            await db.execute(
                select(AdminUser)
                .where(AdminUser.id == admin_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if admin is None:
            raise NotFoundException("管理员")
        if admin.role == "super_admin":
            raise BusinessException("不能通过管理员列表操作唯一超级管理员")
        if admin.role != "quiz_admin":
            raise BusinessException("管理员角色状态异常")
        return admin

    @staticmethod
    async def _ensure_idempotency_available(
        db,
        *,
        actor_id: int,
        action: str,
        idempotency_key: str,
    ) -> None:
        existing = await db.scalar(
            select(AdminSecurityAudit.id)
            .where(
                AdminSecurityAudit.actor_admin_id == actor_id,
                AdminSecurityAudit.action == action,
                AdminSecurityAudit.result == "succeeded",
                AdminSecurityAudit.idempotency_key_hash
                == admin_idempotency_digest(idempotency_key),
            )
            .limit(1)
        )
        if existing is not None:
            raise ConflictException(ADMIN_CREDENTIAL_REPLAY_MESSAGE)

    @staticmethod
    def _integrity_conflict(
        exc: IntegrityError,
        *,
        fallback_message: str | None = None,
        fallback_constraint: str | None = None,
    ) -> ConflictException | None:
        original = getattr(exc, "orig", None)
        diagnostic = getattr(original, "diag", None)
        constraint_name = getattr(diagnostic, "constraint_name", None)
        if (
            constraint_name == ADMIN_CREDENTIAL_IDEMPOTENCY_CONSTRAINT
            or ADMIN_CREDENTIAL_IDEMPOTENCY_CONSTRAINT in str(original or "")
        ):
            return ConflictException(ADMIN_CREDENTIAL_REPLAY_MESSAGE)
        if (
            fallback_message is not None
            and fallback_constraint is not None
            and (
                constraint_name == fallback_constraint
                or fallback_constraint in str(original or "")
            )
        ):
            return ConflictException(fallback_message)
        return None

    async def _record_failure(
        self,
        *,
        action: str,
        actor_id: int,
        context: AdminAuditContext | None,
        exc: Exception,
        target_id: int | None = None,
        username: str | None = None,
    ) -> None:
        # A path parameter is not a valid foreign-key target when lookup
        # failed.  Preserve the attempted identifier as non-sensitive audit
        # context while keeping the relational target nullable so the failure
        # event itself can always be appended.
        target_missing = isinstance(exc, NotFoundException)
        await self.audit.record_best_effort(
            action=action,
            result="failed",
            reason_code=type(exc).__name__,
            actor_admin_id=actor_id,
            target_admin_id=None if target_missing else target_id,
            username=username,
            context=context,
            summary=(
                {"requested_target_admin_id": target_id}
                if target_missing and target_id is not None
                else None
            ),
        )


__all__ = ["AdminSettingsService"]
