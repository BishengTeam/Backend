import hashlib
import secrets

from sqlalchemy import select

from app.port.config import settings
from app.adapter.database import get_db_ctx
from app.port.exceptions import UnauthorizedException
from app.adapter.security import create_admin_access_token
from app.domain.user.src.index import AdminUser
from app.policy.permissions import ROLE_PERMISSIONS
from app.schemas.admin import AdminInfo, AdminLoginResponse
from app.services.renshe_audit import record_best_effort_audit

SALT_LENGTH = 32
HASH_ITERATIONS = 600_000


class AdminAuthService:

    @staticmethod
    def hash_password(password: str) -> str:
        salt = secrets.token_bytes(SALT_LENGTH)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, HASH_ITERATIONS)
        return salt.hex() + ":" + dk.hex()

    @staticmethod
    def verify_password(password: str, stored_hash: str) -> bool:
        salt_hex, dk_hex = stored_hash.split(":")
        salt = bytes.fromhex(salt_hex)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, HASH_ITERATIONS)
        return secrets.compare_digest(dk.hex(), dk_hex)

    async def login(self, username: str, password: str) -> AdminLoginResponse:
        try:
            return await self._login(username, password)
        except Exception as exc:
            await record_best_effort_audit(
                actor_type="admin",
                actor_id=None,
                action="auth.admin_login",
                object_type="auth",
                object_id=0,
                result="failed",
                summary={"error_type": type(exc).__name__},
            )
            raise

    async def _login(self, username: str, password: str) -> AdminLoginResponse:
        async with get_db_ctx() as db:
            result = await db.execute(
                select(AdminUser).where(AdminUser.username == username)
            )
            admin = result.scalar_one_or_none()
            if admin is None:
                raise UnauthorizedException("账号或密码错误")
            if not admin.is_active:
                raise UnauthorizedException("账号已被禁用")
            if not self.verify_password(password, admin.password_hash):
                raise UnauthorizedException("账号或密码错误")
            access_token = create_admin_access_token(admin.id, admin.username, admin.role)
            response = AdminLoginResponse(
                access_token=access_token,
                expires_in=settings.JWT_EXPIRE_MINUTES * 60,
                admin=AdminInfo.model_validate(admin),
                permissions=ROLE_PERMISSIONS.get(admin.role, []),
            )
        await record_best_effort_audit(
            actor_type="admin",
            actor_id=response.admin.id,
            action="auth.admin_login",
            object_type="admin_user",
            object_id=response.admin.id,
            result="succeeded",
            summary={"role": response.admin.role},
        )
        return response
