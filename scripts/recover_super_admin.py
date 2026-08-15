"""Recover the single super administrator from a controlled server terminal.

The command deliberately accepts neither a username nor a password argument.
It discovers the one ``super_admin`` row, requires an interactive confirmation,
and reads the replacement temporary password without terminal echo.
"""

from __future__ import annotations

import asyncio
import getpass
import os
import sys
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from sqlalchemy import select


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.adapter.database import async_session_factory
from app.domain.user.src.index import AdminPasswordHistory, AdminUser
from app.port.exceptions import BusinessException
from app.services.admin_auth import AdminAuthService
from app.services.admin_security_audit import AdminSecurityAuditService


RECOVERY_SWITCH = "ENABLE_SUPER_ADMIN_RECOVERY"


class RecoveryRefused(RuntimeError):
    """Raised when a recovery safety invariant is not satisfied."""


@dataclass(frozen=True, slots=True)
class SuperAdminIdentity:
    id: int
    username: str


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    admin_id: int
    username: str
    auth_version: int


def confirmation_phrase(identity: SuperAdminIdentity) -> str:
    return f"RECOVER {identity.username}"


def _require_interactive_terminal(stream: TextIO) -> None:
    if not stream.isatty():
        raise RecoveryRefused("recovery requires an interactive server terminal")


def _require_enabled(environ: Mapping[str, str]) -> None:
    if environ.get(RECOVERY_SWITCH) != "1":
        raise RecoveryRefused(
            f"recovery disabled; set {RECOVERY_SWITCH}=1 for this command only"
        )


def _read_password_without_echo(prompt: str) -> str:
    """Fail closed instead of using getpass's potentially echoed fallback."""

    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass(prompt)
        except getpass.GetPassWarning as exc:
            raise RecoveryRefused(
                "secure no-echo password input is unavailable"
            ) from exc


def _unique_super_admin(rows: list[AdminUser]) -> AdminUser:
    if len(rows) != 1:
        raise RecoveryRefused(
            "recovery requires exactly one existing super administrator"
        )
    return rows[0]


async def find_unique_super_admin(
    *, session_factory=async_session_factory
) -> SuperAdminIdentity:
    """Read the recovery target without accepting a caller-selected account."""

    async with session_factory() as db:
        rows = list(
            (
                await db.execute(
                    select(AdminUser).where(AdminUser.role == "super_admin")
                )
            )
            .scalars()
            .all()
        )
    admin = _unique_super_admin(rows)
    return SuperAdminIdentity(id=admin.id, username=admin.username)


async def recover_super_admin(
    *,
    expected_identity: SuperAdminIdentity,
    new_password: str,
    session_factory=async_session_factory,
    auth_service: AdminAuthService | None = None,
    audit_service: AdminSecurityAuditService | None = None,
) -> RecoveryResult:
    """Atomically rotate and restrict the sole super administrator credential."""

    auth = auth_service or AdminAuthService()
    audit = audit_service or AdminSecurityAuditService()

    async with session_factory() as db:
        try:
            rows = list(
                (
                    await db.execute(
                        select(AdminUser)
                        .where(AdminUser.role == "super_admin")
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            admin = _unique_super_admin(rows)
            if (
                admin.id != expected_identity.id
                or admin.username != expected_identity.username
            ):
                raise RecoveryRefused(
                    "super administrator identity changed; restart recovery"
                )

            auth.validate_password(new_password, username=admin.username)
            if await asyncio.to_thread(
                auth.verify_password, new_password, admin.password_hash
            ):
                raise BusinessException(
                    "新密码不能与最近 5 次使用的密码相同"
                )
            await auth._ensure_not_recent_password(db, admin, new_password)
            password_hash = await asyncio.to_thread(auth.hash_password, new_password)
            now = datetime.now(timezone.utc)
            was_active = admin.is_active

            admin.password_hash = password_hash
            admin.password_changed_at = now
            admin.is_active = True
            admin.must_change_password = True
            admin.failed_login_attempts = 0
            admin.locked_until = None
            admin.auth_version += 1
            db.add(
                AdminPasswordHistory(
                    admin_id=admin.id,
                    password_hash=password_hash,
                )
            )
            audit.append(
                db,
                action="admin_account.emergency_recovery",
                result="succeeded",
                reason_code="controlled_server_command",
                target_admin_id=admin.id,
                username=admin.username,
                summary={
                    "source": "controlled_server_command",
                    "account_reactivated": not was_active,
                    "sessions_revoked": True,
                    "must_change_password": True,
                },
            )
            await db.flush()
            await auth._prune_password_history(db, admin.id)
            await db.commit()
        except BaseException:
            await db.rollback()
            raise

    return RecoveryResult(
        admin_id=admin.id,
        username=admin.username,
        auth_version=admin.auth_version,
    )


async def run_interactive(
    *,
    session_factory=async_session_factory,
    environ: Mapping[str, str] | None = None,
    stdin: TextIO | None = None,
    input_fn: Callable[[str], str] | None = None,
    password_reader: Callable[[str], str] | None = None,
    print_fn: Callable[[str], None] | None = None,
) -> RecoveryResult:
    """Run the guarded terminal workflow without ever displaying a password."""

    active_environ = os.environ if environ is None else environ
    active_stdin = sys.stdin if stdin is None else stdin
    read_input = input if input_fn is None else input_fn
    read_password = (
        _read_password_without_echo if password_reader is None else password_reader
    )
    write_line = print if print_fn is None else print_fn

    _require_enabled(active_environ)
    _require_interactive_terminal(active_stdin)
    identity = await find_unique_super_admin(session_factory=session_factory)
    phrase = confirmation_phrase(identity)
    write_line(
        "This will reactivate the sole super administrator, revoke all existing "
        "sessions, and require a password change on next login."
    )
    write_line(f"Target: {identity.username} (id={identity.id})")
    if read_input(f'Type "{phrase}" to continue: ').strip() != phrase:
        raise RecoveryRefused("confirmation did not match; no changes were made")

    new_password = read_password("New temporary password: ")
    confirmation = read_password("Repeat temporary password: ")
    if new_password != confirmation:
        raise RecoveryRefused("password confirmation did not match; no changes were made")

    try:
        AdminAuthService.validate_password(new_password, username=identity.username)
        result = await recover_super_admin(
            expected_identity=identity,
            new_password=new_password,
            session_factory=session_factory,
        )
    except BusinessException as exc:
        raise RecoveryRefused(exc.message) from exc

    write_line(
        "Super administrator recovered; all previous sessions are invalid and "
        "the next login must change the temporary password."
    )
    write_line(
        f"Recovery record: target={result.username} auth_version={result.auth_version}"
    )
    return result


def main() -> None:
    try:
        if len(sys.argv) != 1:
            raise RecoveryRefused("this command does not accept arguments")
        asyncio.run(run_interactive())
    except RecoveryRefused as exc:
        print(f"Recovery refused: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except (EOFError, KeyboardInterrupt):
        print(
            "Recovery interrupted; verify the transaction outcome before retrying.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    except Exception:
        # SQLAlchemy exceptions may render bound values such as password
        # hashes.  Keep the operational console credential-safe and inspect
        # database/server telemetry out of band instead.
        print(
            "Recovery failed unexpectedly; verify the transaction outcome "
            "before retrying.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
