"""Runtime dependency diagnostics used by liveness and readiness probes.

The probes deliberately return a small, non-sensitive status document.  They
never include credentials, connection strings, bucket names or other secret
values.  Configuration checks are kept separate from the database/Redis
probes so a broken optional integration can be diagnosed without making the
process liveness endpoint unusable.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from app.integrations.renshe_storage import RensheObjectStorage
from app.port.config import settings


OSS_REQUIRED_SETTINGS = (
    "ALIYUN_OSS_ENDPOINT",
    "ALIYUN_OSS_BUCKET",
    "ALIYUN_OSS_ACCESS_KEY_ID",
    "ALIYUN_OSS_ACCESS_KEY_SECRET",
)
QUIZ_OSS_REQUIRED_SETTINGS = (
    "QUIZ_OSS_ENDPOINT",
    "QUIZ_OSS_BUCKET",
    "QUIZ_OSS_ACCESS_KEY_ID",
    "QUIZ_OSS_ACCESS_KEY_SECRET",
)
WECHAT_LOGIN_REQUIRED_SETTINGS = ("WECHAT_APPID", "WECHAT_SECRET")


def _missing(settings_names: tuple[str, ...]) -> list[str]:
    return [
        name
        for name in settings_names
        if not str(getattr(settings, name, "") or "").strip()
    ]


def inspect_oss_configuration() -> dict[str, Any]:
    """Return OSS configuration state without making a network request."""

    storage_type = (settings.RENSHE_STORAGE_TYPE or "").strip().lower()
    if storage_type == "disabled":
        return {
            "status": "not_configured",
            "configured": False,
            "required": False,
            "mode": "disabled",
            "reason": "feature_not_configured",
            "probe": "not_required",
        }
    if storage_type == "local":
        # Local storage is an intentional development/test adapter.  Production
        # settings reject it before the application starts.
        return {
            "status": "ok",
            "configured": True,
            "mode": "local",
            "probe": "not_required",
        }
    if storage_type != "aliyun_oss":
        return {
            "status": "unavailable",
            "configured": False,
            "mode": storage_type or "unknown",
            "reason": "unsupported_storage_type",
        }

    missing = _missing(OSS_REQUIRED_SETTINGS)
    if missing:
        return {
            "status": "unavailable",
            "configured": False,
            "mode": "aliyun_oss",
            "reason": "missing_configuration",
            "missing": missing,
        }
    try:
        # Importing the SDK is a cheap way to catch an incomplete deployment;
        # bucket connectivity is probed separately by ``probe_oss``.
        import oss2  # noqa: F401
    except ImportError:
        return {
            "status": "unavailable",
            "configured": False,
            "mode": "aliyun_oss",
            "reason": "sdk_not_installed",
        }
    return {
        "status": "ok",
        "configured": True,
        "mode": "aliyun_oss",
        "probe": "not_run",
    }


def inspect_quiz_oss_configuration() -> dict[str, Any]:
    """Return quiz-import storage state without exposing bucket metadata."""

    storage_type = (settings.QUIZ_IMPORT_STORAGE_TYPE or "").strip().lower()
    if storage_type == "disabled":
        return {
            "status": "not_configured",
            "configured": False,
            "required": False,
            "mode": "disabled",
            "reason": "feature_not_configured",
            "probe": "not_required",
        }
    if storage_type == "local":
        return {
            "status": "ok",
            "configured": True,
            "mode": "local",
            "probe": "not_required",
        }
    if storage_type != "aliyun_oss":
        return {
            "status": "unavailable",
            "configured": False,
            "mode": storage_type or "unknown",
            "reason": "unsupported_storage_type",
        }
    missing = _missing(QUIZ_OSS_REQUIRED_SETTINGS)
    if missing:
        return {
            "status": "unavailable",
            "configured": False,
            "mode": "aliyun_oss",
            "reason": "missing_configuration",
            "missing": missing,
        }
    try:
        import oss2  # noqa: F401
    except ImportError:
        return {
            "status": "unavailable",
            "configured": False,
            "mode": "aliyun_oss",
            "reason": "sdk_not_installed",
        }
    return {
        "status": "ok",
        "configured": True,
        "mode": "aliyun_oss",
        "probe": "not_run",
    }


def _local_quiz_storage_directory() -> Path | None:
    """Resolve the private import directory without permitting traversal."""

    root = (Path(settings.UPLOAD_DIR).resolve() / "private").resolve()
    prefix = settings.QUIZ_OSS_PREFIX.strip("/") or "quiz-imports"
    target = (root / prefix).resolve()
    if target != root and root not in target.parents:
        return None
    return target


def _probe_local_quiz_storage() -> bool:
    """Create and remove a private probe file using application permissions."""

    directory = _local_quiz_storage_directory()
    if directory is None:
        return False
    probe_path: Path | None = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".quiz-ready-",
            dir=directory,
            delete=False,
        ) as probe:
            probe.write(b"ready")
            probe_path = Path(probe.name)
        probe_path.unlink()
        return True
    except (OSError, ValueError):
        if probe_path is not None:
            try:
                probe_path.unlink(missing_ok=True)
            except OSError:
                pass
        return False


def inspect_wechat_login_configuration() -> dict[str, Any]:
    """Check only the non-secret presence of the WeChat login settings."""

    missing = _missing(WECHAT_LOGIN_REQUIRED_SETTINGS)
    if missing:
        return {
            "status": "unavailable",
            "configured": False,
            "reason": "missing_configuration",
            "missing": missing,
        }
    return {"status": "ok", "configured": True}


def inspect_wechat_payment_configuration() -> dict[str, Any]:
    """Report the retired WeChat Pay integration.

    WeChat Pay was removed from the product (every order settles by bank
    transfer), so the probe is informational only: it can never make the
    deployment unready, and stale V3 credentials are not inspected.
    """

    from app.contracts.payment import WECHAT_PAY_RETIRED_MESSAGE

    return {
        "status": "retired",
        "configured": False,
        "required": False,
        "reason": "wechat_pay_retired",
        "message": WECHAT_PAY_RETIRED_MESSAGE,
    }


def required_dependency_names() -> set[str]:
    """Return checks that must be healthy before traffic can be accepted."""

    # A migrated database without exactly one active super administrator is
    # not ready to accept Admin traffic.  Clean installation is allowed to
    # pass the migration with an empty table, but bootstrap must create the
    # initial account before runtime readiness succeeds.
    required = {"database", "redis", "admin_identity"}
    if (
        settings.APP_ENV == "production"
        or inspect_oss_configuration().get("mode") != "disabled"
    ):
        required.add("oss")
    if settings.QUIZ_TASKS_ENABLED:
        required.add("quiz_worker")
        if (
            settings.APP_ENV == "production"
            or inspect_quiz_oss_configuration().get("mode") != "disabled"
        ):
            required.add("quiz_oss")
    if settings.APP_ENV == "production":
        # ``wechat_payment`` is intentionally absent: WeChat Pay is retired.
        required.add("wechat_login")
    return required


def is_ready(checks: dict[str, Any]) -> bool:
    """Evaluate a check document without exposing implementation details."""

    required = required_dependency_names()
    for name in required:
        check = checks.get(name) or {}
        status = check if isinstance(check, str) else check.get("status")
        if status != "ok":
            return False
    return True


async def probe_oss(timeout_seconds: float | None = None) -> bool:
    """Perform a bounded, read-only OSS probe in production.

    Local development storage does not need a network probe.  The call is
    isolated in a worker thread because the OSS SDK is synchronous.
    """

    configuration = inspect_oss_configuration()
    if configuration.get("mode") == "local":
        return True
    if configuration.get("status") != "ok":
        return False
    timeout = timeout_seconds or settings.HEALTH_CHECK_TIMEOUT_SECONDS

    def _probe() -> bool:
        bucket = RensheObjectStorage._oss_bucket()
        # get_bucket_acl is a lightweight authenticated read and works for a
        # private bucket without exposing any object data.  A reachable but
        # public-read bucket is not ready for identity documents.
        result = bucket.get_bucket_acl()
        status = getattr(result, "status", 0)
        acl = str(getattr(result, "acl", "") or "").strip().lower()
        return status // 100 == 2 and acl == "private"

    try:
        return await asyncio.wait_for(asyncio.to_thread(_probe), timeout=timeout)
    except Exception:
        return False


async def enrich_oss_probe(check: dict[str, Any]) -> dict[str, Any]:
    """Add a bounded reachability result to an OSS configuration check."""

    result = dict(check)
    if result.get("status") != "ok" or result.get("mode") == "local":
        return result
    reachable = await probe_oss()
    result["probe"] = "ok" if reachable else "unavailable"
    if not reachable:
        result["status"] = "unavailable"
        result["reason"] = "bucket_probe_failed"
    return result


async def probe_quiz_oss(timeout_seconds: float | None = None) -> bool:
    """Verify that the configured quiz-import bucket is reachable and private."""

    configuration = inspect_quiz_oss_configuration()
    if configuration.get("mode") == "local":
        timeout = timeout_seconds or settings.HEALTH_CHECK_TIMEOUT_SECONDS
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_probe_local_quiz_storage), timeout=timeout
            )
        except Exception:
            return False
    if configuration.get("status") != "ok":
        return False
    timeout = timeout_seconds or settings.HEALTH_CHECK_TIMEOUT_SECONDS

    def _probe() -> bool:
        # Import lazily so development/test installations can intentionally
        # use the local adapter without constructing an OSS client.
        import oss2

        auth = oss2.Auth(
            settings.QUIZ_OSS_ACCESS_KEY_ID,
            settings.QUIZ_OSS_ACCESS_KEY_SECRET,
        )
        bucket = oss2.Bucket(
            auth,
            settings.QUIZ_OSS_ENDPOINT,
            settings.QUIZ_OSS_BUCKET,
        )
        result = bucket.get_bucket_acl()
        status = int(getattr(result, "status", 0) or 0)
        acl = str(getattr(result, "acl", "") or "").strip().lower()
        return status // 100 == 2 and acl == "private"

    try:
        return await asyncio.wait_for(asyncio.to_thread(_probe), timeout=timeout)
    except Exception:
        return False


async def enrich_quiz_oss_probe(check: dict[str, Any]) -> dict[str, Any]:
    """Add a bounded, non-sensitive quiz bucket reachability result."""

    result = dict(check)
    if result.get("status") != "ok":
        return result
    reachable = await probe_quiz_oss()
    result["probe"] = "ok" if reachable else "unavailable"
    if not reachable:
        result["status"] = "unavailable"
        result["reason"] = (
            "local_storage_not_writable"
            if result.get("mode") == "local"
            else "bucket_probe_failed"
        )
    return result
