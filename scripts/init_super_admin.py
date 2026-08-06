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
from app.domain.user.src.index import AdminUser
from app.services.admin_auth import AdminAuthService


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


async def main() -> None:
    if os.getenv("ENABLE_INITIAL_SUPER_ADMIN") != "1":
        raise RuntimeError(
            "initialization disabled; set ENABLE_INITIAL_SUPER_ADMIN=1 explicitly"
        )

    username = _required_env("INITIAL_SUPER_ADMIN_USERNAME")
    password = _required_env("INITIAL_SUPER_ADMIN_PASSWORD")
    if len(password) < 12:
        raise RuntimeError("INITIAL_SUPER_ADMIN_PASSWORD must be at least 12 characters")

    async with async_session_factory() as db:
        existing = await db.scalar(
            select(AdminUser.id).where(AdminUser.role == "super_admin").limit(1)
        )
        if existing is not None:
            raise RuntimeError("a super administrator already exists")

        username_taken = await db.scalar(
            select(AdminUser.id).where(AdminUser.username == username).limit(1)
        )
        if username_taken is not None:
            raise RuntimeError("administrator username already exists")

        db.add(
            AdminUser(
                username=username,
                password_hash=AdminAuthService.hash_password(password),
                role="super_admin",
                is_active=True,
            )
        )
        await db.commit()

    print("Initial super administrator created. Disable the initialization switch now.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError as exc:
        print(f"Initialization refused: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
