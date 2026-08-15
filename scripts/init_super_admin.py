"""Create the one and only initial super administrator.

This command is intentionally never called from application startup.  It is
enabled only with an explicit environment switch and refuses to run once a
super administrator exists.
"""

import asyncio
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select

from app.adapter.database import async_session_factory
from app.domain.user.src.index import (
    AdminPasswordHistory,
    AdminSecurityAudit,
    AdminUser,
)
from app.port.exceptions import BusinessException
from app.services.admin_auth import AdminAuthService


class InitializationRefused(RuntimeError):
    """Expected, credential-safe refusal of the one-time initialization."""


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise InitializationRefused(f"{name} is required")
    return value


async def main() -> None:
    if os.getenv("ENABLE_INITIAL_SUPER_ADMIN") != "1":
        raise InitializationRefused(
            "initialization disabled; set ENABLE_INITIAL_SUPER_ADMIN=1 explicitly"
        )

    username = AdminAuthService.normalize_username(
        _required_env("INITIAL_SUPER_ADMIN_USERNAME")
    )
    password = _required_env("INITIAL_SUPER_ADMIN_PASSWORD")
    AdminAuthService.validate_password(password, username=username)

    async with async_session_factory() as db:
        existing = await db.scalar(
            select(AdminUser.id).where(AdminUser.role == "super_admin").limit(1)
        )
        if existing is not None:
            raise InitializationRefused("a super administrator already exists")

        username_taken = await db.scalar(
            select(AdminUser.id).where(AdminUser.username == username).limit(1)
        )
        if username_taken is not None:
            raise InitializationRefused("administrator username already exists")

        password_hash = AdminAuthService.hash_password(password)
        admin = AdminUser(
            username=username,
            display_name=username,
            password_hash=password_hash,
            role="super_admin",
            is_active=True,
            must_change_password=True,
        )
        db.add(admin)
        await db.flush()
        db.add(AdminPasswordHistory(admin_id=admin.id, password_hash=password_hash))
        db.add(
            AdminSecurityAudit(
                target_admin_id=admin.id,
                action="admin_account.create",
                result="succeeded",
                reason_code="initialization_command_super_admin_created",
                username=admin.username,
                summary={"source": "initialization_command"},
            )
        )
        await db.commit()

    print("Initial super administrator created. Disable the initialization switch now.")


def cli_main() -> None:
    try:
        asyncio.run(main())
    except InitializationRefused as exc:
        print(f"Initialization refused: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except BusinessException as exc:
        # Password-policy messages are stable and never contain the supplied
        # password, so they are safe to return as an expected refusal.
        print(f"Initialization refused: {exc.message}", file=sys.stderr)
        raise SystemExit(1) from None
    except Exception:
        # SQLAlchemy/driver exceptions may render bound password hashes.  The
        # initialization console must never echo an unexpected exception.
        print(
            "Initialization failed unexpectedly; verify the transaction outcome "
            "before retrying.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    cli_main()
