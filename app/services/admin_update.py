from __future__ import annotations

import asyncio
import re
import shlex
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from app.port.config import settings
from app.schemas.admin_update import (
    AdminUpdateAsset,
    AdminUpdateCheckResult,
    AdminUpdateRelease,
    AdminUpdateVersion,
)


GITHUB_LATEST_RELEASE_URL = (
    "https://api.github.com/repos/BishengTeam/Backend/releases/latest"
)
UPDATE_CACHE_SECONDS = 300
RELEASE_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40,64}$")
REQUIRED_ASSETS = frozenset({"SHA256SUMS"})


@dataclass(frozen=True, slots=True)
class _CachedRelease:
    expires_at: float
    value: AdminUpdateRelease | None
    reason_code: str | None


_cache: _CachedRelease | None = None
_cache_lock = asyncio.Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _required_string(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"GitHub release field is invalid: {name}")
    return value.strip()


def _commit_from_asset(names: list[str], prefix: str) -> str:
    matches = [name for name in names if name.startswith(prefix) and name.endswith(".tar.zst")]
    if len(matches) != 1:
        raise ValueError("GitHub release image asset is missing or ambiguous")
    commit = matches[0][len(prefix) : -len(".tar.zst")]
    if not COMMIT_RE.fullmatch(commit):
        raise ValueError("GitHub release image commit is invalid")
    return commit


def _parse_release(payload: Any) -> AdminUpdateRelease:
    if not isinstance(payload, dict):
        raise ValueError("GitHub release payload is invalid")
    if payload.get("draft") is not False or payload.get("prerelease") is not False:
        raise ValueError("GitHub latest release must be stable")

    tag = _required_string(payload, "tag_name")
    if not RELEASE_TAG_RE.fullmatch(tag):
        raise ValueError("GitHub release tag is invalid")
    raw_assets = payload.get("assets")
    if not isinstance(raw_assets, list):
        raise ValueError("GitHub release assets are invalid")

    assets: list[AdminUpdateAsset] = []
    names: list[str] = []
    for raw_asset in raw_assets:
        if not isinstance(raw_asset, dict):
            raise ValueError("GitHub release asset is invalid")
        name = _required_string(raw_asset, "name")
        download_url = _required_string(raw_asset, "browser_download_url")
        size = raw_asset.get("size")
        if not isinstance(size, int) or size < 0:
            raise ValueError("GitHub release asset size is invalid")
        if not download_url.startswith("https://github.com/"):
            raise ValueError("GitHub release asset URL is invalid")
        assets.append(
            AdminUpdateAsset(name=name, size=size, download_url=download_url)
        )
        names.append(name)

    if not REQUIRED_ASSETS.issubset(set(names)):
        raise ValueError("GitHub release checksum asset is missing")
    backend_commit = _commit_from_asset(names, "wemini-backend-")
    admin_commit = _commit_from_asset(names, "wemini-admin-")
    notes = payload.get("body")
    if not isinstance(notes, str):
        notes = ""

    return AdminUpdateRelease(
        release_tag=tag,
        published_at=_required_string(payload, "published_at"),
        html_url=_required_string(payload, "html_url"),
        notes=notes[-16000:],
        backend_commit=backend_commit,
        admin_commit=admin_commit,
        assets=assets,
    )


async def _fetch_latest_release(
    client_factory: Callable[[], httpx.AsyncClient],
) -> AdminUpdateRelease:
    try:
        async with client_factory() as client:
            response = await client.get(
                GITHUB_LATEST_RELEASE_URL,
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "weMiniApp-update-check/1.0",
                },
            )
        response.raise_for_status()
        return _parse_release(response.json())
    except (httpx.TimeoutException, httpx.RequestError):
        raise ConnectionError("github_unavailable") from None
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        raise ConnectionError(
            "github_not_found" if status == 404 else "github_rejected"
        ) from None
    except (ValueError, TypeError):
        raise ConnectionError("invalid_github_response") from None


async def _latest_release(
    client_factory: Callable[[], httpx.AsyncClient] | None = None,
) -> tuple[AdminUpdateRelease | None, str | None]:
    global _cache
    if client_factory is None:
        def factory() -> httpx.AsyncClient:
            return httpx.AsyncClient(
                timeout=httpx.Timeout(5.0),
                follow_redirects=True,
            )
    else:
        factory = client_factory

    async with _cache_lock:
        now = time.monotonic()
        if _cache is not None and _cache.expires_at > now:
            return _cache.value, _cache.reason_code
        try:
            release = await _fetch_latest_release(factory)
            reason = None
        except ConnectionError as exc:
            release = None
            reason = str(exc)
        # Errors are cached briefly as well so a storm of page refreshes does
        # not turn the Admin page into a GitHub API amplifier.
        _cache = _CachedRelease(
            expires_at=now + (UPDATE_CACHE_SECONDS if release is not None else 30),
            value=release,
            reason_code=reason,
        )
        return release, reason


def _upgrade_command(release_tag: str, *, dry_run: bool) -> str:
    deployment_root = settings.WEMINI_DEPLOYMENT_ROOT or "<deployment-root>"
    compose_project = settings.WEMINI_COMPOSE_PROJECT or "<compose-project>"
    command = [
        "DOCKER_USE_SUDO=1",
        "/srv/wemini-updater/upgrade_release.sh",
        "--release",
        release_tag,
        "--deployment-root",
        deployment_root,
        "--compose-project",
        compose_project,
    ]
    if dry_run:
        command.append("--dry-run")
    return " ".join(shlex.quote(part) for part in command)


async def check_for_update(
    client_factory: Callable[[], httpx.AsyncClient] | None = None,
) -> AdminUpdateCheckResult:
    latest, reason = await _latest_release(client_factory)
    current = AdminUpdateVersion(
        release_tag=settings.WEMINI_RELEASE_TAG or "unknown",
        backend_commit=settings.WEMINI_BACKEND_COMMIT or "unknown",
        admin_commit=settings.WEMINI_ADMIN_COMMIT or "unknown",
    )
    return AdminUpdateCheckResult(
        current=current,
        latest=latest,
        update_available=latest is not None and latest.release_tag != current.release_tag,
        check_status="ok" if latest is not None else "unavailable",
        checked_at=_utc_now(),
        reason_code=reason,
        dry_run_command=_upgrade_command(
            latest.release_tag if latest is not None else "<release-tag>",
            dry_run=True,
        ),
        upgrade_command=_upgrade_command(
            latest.release_tag if latest is not None else "<release-tag>",
            dry_run=False,
        ),
    )


def reset_update_check_cache_for_tests() -> None:
    global _cache
    _cache = None
