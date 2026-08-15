from __future__ import annotations

import asyncio
import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.adapter.database import get_db_ctx
from app.adapter.security import (
    ADMIN_SESSION_EXPIRE_MINUTES,
    create_admin_access_token,
    create_admin_reauth_token,
)
from app.domain.user.src.index import AdminPasswordHistory, AdminUser
from app.policy.permissions import ROLE_PERMISSIONS
from app.port.exceptions import BusinessException, UnauthorizedException
from app.schemas.admin import AdminInfo, AdminLoginResponse, AdminReauthResponse
from app.services.admin_security_audit import AdminAuditContext, AdminSecurityAuditService
from app.utils.passwords import hash_admin_password, verify_admin_password


LOGIN_FAILURE_LIMIT = 5
LOGIN_LOCK_MINUTES = 15
PASSWORD_HISTORY_LIMIT = 5
TEMPORARY_PASSWORD_LENGTH = 18

# Stable hash used only to make unknown-username authentication consume the
# same PBKDF2 verifier as a real account.  It is not an application credential.
_DUMMY_PASSWORD_HASH = (
    "0000000000000000000000000000000000000000000000000000000000000000:"
    "3b388f2ad3514965dc4d68260078a92c8b86cf8eb0940b02da75ec10c8220b4b"
)

_FROZEN_WEAK_PASSWORDS = frozenset(
    {
        "password1234",
        "password12345",
        "qwerty123456",
        "qwertyuiop12",
        "abc123456789",
        "administrator1",
        "welcome12345",
        "letmein123456",
        "1234567890ab",
    }
)


class AdminAuthService:
    audit = AdminSecurityAuditService()

    @staticmethod
    def hash_password(password: str) -> str:
        return hash_admin_password(password)

    @staticmethod
    def verify_password(password: str, stored_hash: str) -> bool:
        return verify_admin_password(password, stored_hash)

    @staticmethod
    def normalize_username(username: str) -> str:
        return username.strip().lower()

    @classmethod
    def validate_password(cls, password: str, *, username: str) -> None:
        if not 12 <= len(password) <= 128:
            raise BusinessException("密码长度必须为 12～128 位")
        if any(character in password for character in ("\x00", "\r", "\n")):
            raise BusinessException("密码包含不支持的控制字符")
        if not re.search(r"[A-Za-z]", password) or not re.search(r"[0-9]", password):
            raise BusinessException("密码必须同时包含字母和数字")
        normalized_username = cls.normalize_username(username)
        if normalized_username and normalized_username in password.lower():
            raise BusinessException("密码不能包含完整登录用户名")
        if password.casefold() in _FROZEN_WEAK_PASSWORDS:
            raise BusinessException("密码过于常见，请更换更安全的密码")

    @classmethod
    def generate_temporary_password(cls, *, username: str) -> str:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
        chooser = secrets.SystemRandom()
        while True:
            chars = [
                chooser.choice("ABCDEFGHJKLMNPQRSTUVWXYZ"),
                chooser.choice("abcdefghijkmnopqrstuvwxyz"),
                chooser.choice("23456789"),
            ]
            chars.extend(
                chooser.choice(alphabet)
                for _ in range(TEMPORARY_PASSWORD_LENGTH - len(chars))
            )
            chooser.shuffle(chars)
            password = "".join(chars)
            try:
                cls.validate_password(password, username=username)
            except BusinessException:
                continue
            return password

    async def login(
        self,
        username: str,
        password: str,
        *,
        context: AdminAuditContext | None = None,
    ) -> AdminLoginResponse:
        normalized_username = self.normalize_username(username)
        now = datetime.now(timezone.utc)
        async with get_db_ctx() as db:
            admin = (
                await db.execute(
                    select(AdminUser)
                    .where(AdminUser.username == normalized_username)
                    .with_for_update()
                )
            ).scalar_one_or_none()

            if admin is None:
                await asyncio.to_thread(self.verify_password, password, _DUMMY_PASSWORD_HASH)
                self.audit.append(
                    db,
                    action="auth.admin_login",
                    result="failed",
                    reason_code="invalid_credentials",
                    username=normalized_username,
                    context=context,
                )
                await db.commit()
                raise UnauthorizedException("账号或密码错误")

            if not admin.is_active:
                await asyncio.to_thread(self.verify_password, password, _DUMMY_PASSWORD_HASH)
                self.audit.append(
                    db,
                    action="auth.admin_login",
                    result="failed",
                    reason_code="account_inactive",
                    target_admin_id=admin.id,
                    username=normalized_username,
                    context=context,
                )
                await db.commit()
                raise UnauthorizedException("账号或密码错误")

            if admin.locked_until is not None and admin.locked_until > now:
                await asyncio.to_thread(self.verify_password, password, _DUMMY_PASSWORD_HASH)
                self.audit.append(
                    db,
                    action="auth.admin_login",
                    result="failed",
                    reason_code="account_locked",
                    target_admin_id=admin.id,
                    username=normalized_username,
                    context=context,
                )
                await db.commit()
                raise UnauthorizedException("账号或密码错误")

            if admin.locked_until is not None:
                admin.locked_until = None
                admin.failed_login_attempts = 0
                self.audit.append(
                    db,
                    action="admin_account.auto_unlock",
                    result="succeeded",
                    reason_code="lock_expired",
                    target_admin_id=admin.id,
                    username=admin.username,
                    context=context,
                )

            password_matches = await asyncio.to_thread(
                self.verify_password, password, admin.password_hash
            )
            if not password_matches:
                admin.failed_login_attempts += 1
                self.audit.append(
                    db,
                    action="auth.admin_login",
                    result="failed",
                    reason_code="invalid_credentials",
                    target_admin_id=admin.id,
                    username=normalized_username,
                    context=context,
                    summary={"failed_attempts": admin.failed_login_attempts},
                )
                if admin.failed_login_attempts >= LOGIN_FAILURE_LIMIT:
                    admin.locked_until = now + timedelta(minutes=LOGIN_LOCK_MINUTES)
                    self.audit.append(
                        db,
                        action="admin_account.lock",
                        result="succeeded",
                        reason_code="login_failure_limit",
                        target_admin_id=admin.id,
                        username=admin.username,
                        context=context,
                        summary={"failed_attempts": admin.failed_login_attempts},
                    )
                await db.commit()
                raise UnauthorizedException("账号或密码错误")

            admin.failed_login_attempts = 0
            admin.locked_until = None
            admin.last_login_at = now
            admin.last_login_ip = (context or AdminAuditContext()).normalized().source_ip
            session_mode = "restricted" if admin.must_change_password else "normal"
            access_token = create_admin_access_token(
                admin.id,
                admin.username,
                admin.role,
                auth_version=admin.auth_version,
                session_mode=session_mode,
            )
            self.audit.append(
                db,
                action="auth.admin_login",
                result="succeeded",
                reason_code="authenticated",
                actor_admin_id=admin.id,
                target_admin_id=admin.id,
                username=admin.username,
                context=context,
                summary={"role": admin.role, "session_mode": session_mode},
            )
            await db.commit()

            return AdminLoginResponse(
                access_token=access_token,
                expires_in=ADMIN_SESSION_EXPIRE_MINUTES * 60,
                admin=AdminInfo.model_validate(admin),
                permissions=ROLE_PERMISSIONS.get(admin.role, []),
                session_mode=session_mode,
                must_change_password=admin.must_change_password,
            )

    async def change_password(
        self,
        *,
        admin_id: int,
        expected_auth_version: int,
        current_password: str,
        new_password: str,
        context: AdminAuditContext | None = None,
    ) -> None:
        async with get_db_ctx() as db:
            admin = (
                await db.execute(
                    select(AdminUser)
                    .where(AdminUser.id == admin_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if (
                admin is None
                or not admin.is_active
                or admin.auth_version != expected_auth_version
            ):
                raise UnauthorizedException("登录状态已变化，请重新登录")
            if not await asyncio.to_thread(
                self.verify_password, current_password, admin.password_hash
            ):
                self.audit.append(
                    db,
                    action="admin_account.password_change",
                    result="failed",
                    reason_code="current_password_invalid",
                    actor_admin_id=admin.id,
                    target_admin_id=admin.id,
                    username=admin.username,
                    context=context,
                )
                await db.commit()
                raise BusinessException("当前密码错误")

            try:
                self.validate_password(new_password, username=admin.username)
                await self._ensure_not_recent_password(db, admin, new_password)
            except BusinessException:
                self.audit.append(
                    db,
                    action="admin_account.password_change",
                    result="failed",
                    reason_code="password_policy_rejected",
                    actor_admin_id=admin.id,
                    target_admin_id=admin.id,
                    username=admin.username,
                    context=context,
                )
                await db.commit()
                raise
            new_hash = await asyncio.to_thread(self.hash_password, new_password)
            admin.password_hash = new_hash
            admin.password_changed_at = datetime.now(timezone.utc)
            admin.must_change_password = False
            admin.failed_login_attempts = 0
            admin.locked_until = None
            admin.auth_version += 1
            db.add(AdminPasswordHistory(admin_id=admin.id, password_hash=new_hash))
            self.audit.append(
                db,
                action="admin_account.password_change",
                result="succeeded",
                reason_code="password_changed",
                actor_admin_id=admin.id,
                target_admin_id=admin.id,
                username=admin.username,
                context=context,
            )
            await db.flush()
            await self._prune_password_history(db, admin.id)
            await db.commit()

    async def reauthenticate(
        self,
        *,
        admin_id: int,
        expected_auth_version: int,
        jti: str,
        password: str,
        context: AdminAuditContext | None = None,
    ) -> AdminReauthResponse:
        async with get_db_ctx() as db:
            admin = (
                await db.execute(
                    select(AdminUser)
                    .where(AdminUser.id == admin_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if (
                admin is None
                or not admin.is_active
                or admin.auth_version != expected_auth_version
                or admin.role != "super_admin"
                or admin.must_change_password
            ):
                raise UnauthorizedException("登录状态已变化，请重新登录")
            if not await asyncio.to_thread(
                self.verify_password, password, admin.password_hash
            ):
                self.audit.append(
                    db,
                    action="auth.admin_reauth",
                    result="failed",
                    reason_code="current_password_invalid",
                    actor_admin_id=admin.id,
                    target_admin_id=admin.id,
                    username=admin.username,
                    context=context,
                )
                await db.commit()
                # The caller already has a valid, normal super-admin session.
                # Keep 40100 reserved for a stale/revoked session so clients
                # can immediately clear authorization state; an incorrect
                # current password is a business validation failure, matching
                # the change-password endpoint.
                raise BusinessException("当前密码验证失败")

        # Persist the successful password verification before issuing a Redis
        # grant.  This ordering is deliberately fail-closed: if the permanent
        # audit commit fails there is no credential to revoke or accidentally
        # accept.  Redis failure after the audit creates no grant and is
        # recorded as a second, failed outcome below.
        await self.audit.record(
            action="auth.admin_reauth",
            result="succeeded",
            reason_code="password_verified",
            actor_admin_id=admin_id,
            target_admin_id=admin_id,
            context=context,
        )
        token = await create_admin_reauth_token(
            admin_id=admin_id,
            jti=jti,
            auth_version=expected_auth_version,
        )
        if token is None:
            await self.audit.record_best_effort(
                action="auth.admin_reauth",
                result="failed",
                reason_code="reauth_store_unavailable",
                actor_admin_id=admin_id,
                target_admin_id=admin_id,
                context=context,
            )
            raise BusinessException("再认证服务暂不可用，请稍后重试")

        return AdminReauthResponse(reauth_token=token)

    async def record_logout(
        self,
        *,
        admin_id: int,
        username: str,
        succeeded: bool,
        context: AdminAuditContext | None = None,
    ) -> None:
        await self.audit.record(
            action="auth.admin_logout",
            result="succeeded" if succeeded else "failed",
            reason_code="session_revoked" if succeeded else "revocation_store_unavailable",
            actor_admin_id=admin_id,
            target_admin_id=admin_id,
            username=username,
            context=context,
        )

    async def _ensure_not_recent_password(
        self, db, admin: AdminUser, password: str
    ) -> None:
        recent_hashes = list(
            (
                await db.execute(
                    select(AdminPasswordHistory.password_hash)
                    .where(AdminPasswordHistory.admin_id == admin.id)
                    .order_by(
                        AdminPasswordHistory.created_at.desc(),
                        AdminPasswordHistory.id.desc(),
                    )
                    .limit(PASSWORD_HISTORY_LIMIT)
                )
            ).scalars()
        )
        comparison_hashes = [admin.password_hash]
        comparison_hashes.extend(
            password_hash
            for password_hash in recent_hashes
            if password_hash != admin.password_hash
        )
        for password_hash in comparison_hashes[:PASSWORD_HISTORY_LIMIT]:
            if await asyncio.to_thread(self.verify_password, password, password_hash):
                raise BusinessException("新密码不能与最近 5 次使用的密码相同")

    @staticmethod
    async def _prune_password_history(db, admin_id: int) -> None:
        stale_ids = list(
            (
                await db.execute(
                    select(AdminPasswordHistory.id)
                    .where(AdminPasswordHistory.admin_id == admin_id)
                    .order_by(
                        AdminPasswordHistory.created_at.desc(),
                        AdminPasswordHistory.id.desc(),
                    )
                    .offset(PASSWORD_HISTORY_LIMIT)
                )
            ).scalars()
        )
        if stale_ids:
            await db.execute(
                delete(AdminPasswordHistory).where(AdminPasswordHistory.id.in_(stale_ids))
            )


__all__ = [
    "AdminAuthService",
    "LOGIN_FAILURE_LIMIT",
    "LOGIN_LOCK_MINUTES",
    "PASSWORD_HISTORY_LIMIT",
]
