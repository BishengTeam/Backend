from __future__ import annotations

import httpx
import pytest

from app.services import admin_update
from app.services.admin_update import (
    check_for_update,
    reset_update_check_cache_for_tests,
)


def _release_payload() -> dict[str, object]:
    return {
        "tag_name": "2026.08.19.1",
        "draft": False,
        "prerelease": False,
        "published_at": "2026-08-19T01:00:00Z",
        "html_url": "https://github.com/BishengTeam/Backend/releases/tag/2026.08.19.1",
        "body": "Release notes",
        "assets": [
            {
                "name": "SHA256SUMS",
                "size": 358,
                "browser_download_url": "https://github.com/BishengTeam/Backend/releases/download/1/SHA256SUMS",
            },
            {
                "name": "wemini-backend-" + "a" * 40 + ".tar.zst",
                "size": 100,
                "browser_download_url": "https://github.com/BishengTeam/Backend/releases/download/1/backend.tar.zst",
            },
            {
                "name": "wemini-admin-" + "b" * 40 + ".tar.zst",
                "size": 100,
                "browser_download_url": "https://github.com/BishengTeam/Backend/releases/download/1/admin.tar.zst",
            },
            {
                "name": "wemini-deploy-2026.08.19.1.tar.gz",
                "size": 100,
                "browser_download_url": "https://github.com/BishengTeam/Backend/releases/download/1/deploy.tar.gz",
            },
        ],
    }


class _FakeClient:
    calls = 0

    def __init__(self, response: httpx.Response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, *_args, **_kwargs):
        return self.response


@pytest.mark.asyncio
async def test_update_check_compares_and_caches_stable_github_release(monkeypatch):
    reset_update_check_cache_for_tests()
    monkeypatch.setattr(admin_update.settings, "WEMINI_RELEASE_TAG", "2026.08.18.6")
    monkeypatch.setattr(admin_update.settings, "WEMINI_BACKEND_COMMIT", "c" * 40)
    monkeypatch.setattr(admin_update.settings, "WEMINI_ADMIN_COMMIT", "d" * 40)
    monkeypatch.setattr(admin_update.settings, "WEMINI_DEPLOYMENT_ROOT", "/srv/wemini")
    monkeypatch.setattr(admin_update.settings, "WEMINI_COMPOSE_PROJECT", "wemini")

    calls = 0

    def factory():
        nonlocal calls
        calls += 1
        response = httpx.Response(200, json=_release_payload())
        response._request = httpx.Request(
            "GET", admin_update.GITHUB_LATEST_RELEASE_URL
        )
        return _FakeClient(response)

    first = await check_for_update(factory)
    second = await check_for_update(factory)

    assert calls == 1
    assert first.check_status == "ok"
    assert first.update_available is True
    assert first.current.release_tag == "2026.08.18.6"
    assert first.latest is not None
    assert first.latest.release_tag == "2026.08.19.1"
    assert first.latest.backend_commit == "a" * 40
    assert first.latest.admin_commit == "b" * 40
    assert first.manual_upgrade_required is True
    assert "--dry-run" in first.dry_run_command
    assert "--dry-run" not in first.upgrade_command
    assert second.latest is not None
    assert second.latest.release_tag == "2026.08.19.1"


@pytest.mark.asyncio
async def test_update_check_reports_github_unavailable_without_leaking_details(monkeypatch):
    reset_update_check_cache_for_tests()
    monkeypatch.setattr(admin_update.settings, "WEMINI_RELEASE_TAG", "2026.08.18.6")

    class _UnavailableClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return httpx.Response(
                403,
                request=httpx.Request("GET", admin_update.GITHUB_LATEST_RELEASE_URL),
            )

    result = await check_for_update(lambda: _UnavailableClient())
    assert result.check_status == "unavailable"
    assert result.latest is None
    assert result.update_available is False
    assert result.reason_code == "github_rejected"
    assert result.manual_upgrade_required is True
