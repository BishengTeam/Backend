from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AdminUpdateVersion(BaseModel):
    release_tag: str
    backend_commit: str
    admin_commit: str


class AdminUpdateAsset(BaseModel):
    name: str
    size: int = Field(ge=0)
    download_url: str


class AdminUpdateRelease(BaseModel):
    release_tag: str
    published_at: str
    html_url: str
    notes: str
    backend_commit: str
    admin_commit: str
    assets: list[AdminUpdateAsset]


class AdminUpdateCheckResult(BaseModel):
    current: AdminUpdateVersion
    latest: AdminUpdateRelease | None = None
    update_available: bool
    check_status: Literal["ok", "unavailable"]
    checked_at: str
    reason_code: str | None = None
    manual_upgrade_required: bool = True
    dry_run_command: str
    upgrade_command: str
    estimated_downtime_seconds: int = Field(default=300, ge=1, le=3600)
