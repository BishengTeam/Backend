from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path

import asyncpg

from app.utils.passwords import hash_admin_password, verify_admin_password
from bootstrap_app.models import BootstrapAdminRequest


class BootstrapRuntimeError(RuntimeError):
    """A migrated runtime dependency does not satisfy bootstrap invariants."""


def read_runtime_env(path: Path) -> dict[str, str]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise BootstrapRuntimeError("runtime configuration is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise BootstrapRuntimeError("runtime configuration path is unsafe")
    result: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise BootstrapRuntimeError("runtime configuration cannot be read") from exc
    for line in lines:
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key or key in result:
            raise BootstrapRuntimeError("runtime configuration is malformed")
        result[key] = value
    return result


def _read_secret(path: Path) -> str:
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise BootstrapRuntimeError("secret path is unsafe")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise BootstrapRuntimeError("secret permissions are unsafe")
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise BootstrapRuntimeError("secret cannot be read") from exc
    if not value:
        raise BootstrapRuntimeError("secret is empty")
    return value


@dataclass(frozen=True, slots=True)
class DatabaseTarget:
    host: str
    port: int
    user: str
    password: str
    database: str

    @classmethod
    def from_installation(cls, installation_dir: Path) -> "DatabaseTarget":
        runtime = read_runtime_env(installation_dir / "runtime.env")
        required = ("DB_HOST", "DB_PORT", "DB_USER", "DB_NAME")
        if any(not runtime.get(name) for name in required):
            raise BootstrapRuntimeError("database runtime configuration is incomplete")
        try:
            port = int(runtime["DB_PORT"])
        except ValueError as exc:
            raise BootstrapRuntimeError("database port is invalid") from exc
        if not 1 <= port <= 65535:
            raise BootstrapRuntimeError("database port is invalid")
        password = _read_secret(installation_dir / "secrets" / "postgres_password")
        return cls(
            host=runtime["DB_HOST"],
            port=port,
            user=runtime["DB_USER"],
            password=password,
            database=runtime["DB_NAME"],
        )


async def create_initial_super_admin(
    installation_dir: Path,
    request: BootstrapAdminRequest,
) -> int:
    target = DatabaseTarget.from_installation(installation_dir)
    password_hash = hash_admin_password(request.password.get_secret_value())
    connection = None
    try:
        connection = await asyncpg.connect(
            host=target.host,
            port=target.port,
            user=target.user,
            password=target.password,
            database=target.database,
            timeout=10,
            command_timeout=15,
        )
        async with connection.transaction():
            # Transaction-scoped advisory lock serializes concurrent browser
            # submissions without creating another bootstrap table.
            await connection.execute("SELECT pg_advisory_xact_lock($1)", 836_642_001)
            security_tables_ready = await connection.fetchval(
                "SELECT to_regclass('public.admin_user') IS NOT NULL "
                "AND to_regclass('public.admin_password_history') IS NOT NULL "
                "AND to_regclass('public.admin_security_audit') IS NOT NULL"
            )
            if not security_tables_ready:
                raise BootstrapRuntimeError("administrator security tables are not migrated")
            existing = await connection.fetchrow(
                """
                SELECT id, username, password_hash, is_active
                FROM admin_user
                WHERE role = 'super_admin'
                LIMIT 1
                """
            )
            if existing is not None:
                if not existing["is_active"]:
                    raise BootstrapRuntimeError(
                        "the existing super administrator is inactive"
                    )
                if (
                    existing["username"] == request.username
                    and verify_admin_password(
                        request.password.get_secret_value(),
                        existing["password_hash"],
                    )
                ):
                    return int(existing["id"])
                raise BootstrapRuntimeError("a super administrator already exists")
            username_taken = await connection.fetchval(
                "SELECT id FROM admin_user WHERE username = $1 LIMIT 1",
                request.username,
            )
            if username_taken is not None:
                raise BootstrapRuntimeError("administrator username already exists")
            identifier = await connection.fetchval(
                """
                INSERT INTO admin_user
                    (
                        username, password_hash, role, is_active, display_name,
                        must_change_password, auth_version, failed_login_attempts
                    )
                VALUES
                    ($1, $2, 'super_admin', TRUE, $1, TRUE, 1, 0)
                RETURNING id
                """,
                request.username,
                password_hash,
            )
            if not isinstance(identifier, int):
                raise BootstrapRuntimeError("super administrator creation failed")
            # The initial bootstrap password is a temporary credential, but it
            # still belongs to the recent-password history.  Persist both the
            # history row and the security event in the same transaction as
            # account creation so bootstrap cannot leave a partially secured
            # administrator behind.
            await connection.execute(
                """
                INSERT INTO admin_password_history (admin_id, password_hash)
                VALUES ($1, $2)
                """,
                identifier,
                password_hash,
            )
            await connection.execute(
                """
                INSERT INTO admin_security_audit
                    (target_admin_id, action, result, reason_code, username, summary)
                VALUES
                    ($1, 'admin_account.create', 'succeeded',
                     'bootstrap_super_admin_created', $2,
                     '{"source":"bootstrap"}'::jsonb)
                """,
                identifier,
                request.username,
            )
            return identifier
    except BootstrapRuntimeError:
        raise
    except (asyncpg.PostgresError, OSError, TimeoutError) as exc:
        raise BootstrapRuntimeError("database operation failed") from exc
    finally:
        if connection is not None:
            await connection.close()
