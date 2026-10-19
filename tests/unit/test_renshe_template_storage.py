"""Writable private commitment assets, legacy reads and storage error responses."""

import errno
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.middleware.error_handler import app_exception_handler
from app.port.config import settings
from app.port.exceptions import (
    AppException,
    ConflictException,
    StorageUnavailableException,
)
from app.services import renshe_graduated as templates

PDF = b"%PDF-1.4\nconfirmed commitment fixture"


@pytest.fixture
def storage(tmp_path, monkeypatch):
    fixed = tmp_path / "fixed"
    fixed.mkdir()
    (fixed / "报名信息.xlsx").write_bytes(b"fixed registration workbook")
    (fixed / "工作经历.xlsx").write_bytes(b"fixed work workbook")
    monkeypatch.setattr(settings, "RENSHE_TEMPLATE_DIR", str(fixed))
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "RENSHE_COMMITMENT_TEMPLATE_DIR", "")
    return fixed, tmp_path / "uploads" / "private" / "renshe-commitment-templates"


def test_upload_does_not_write_static_excel_directory_and_retries_are_atomic(storage):
    fixed, writable = storage
    metadata = templates.save_template("承诺书.pdf", PDF)
    assert templates.template_path({"commitment_template": metadata}) == writable / metadata["filename"]
    assert (writable / metadata["filename"]).read_bytes() == PDF
    assert {p.name for p in fixed.iterdir()} == {"报名信息.xlsx", "工作经历.xlsx"}
    (writable / metadata["filename"]).unlink()
    with ThreadPoolExecutor(max_workers=4) as pool:
        repeated = list(pool.map(lambda _: templates.save_template("承诺书.pdf", PDF), range(8)))
    assert all(item == metadata for item in repeated)
    assert list(writable.iterdir()) == [writable / metadata["filename"]]


def test_legacy_read_only_file_still_works_and_checksum_is_enforced(storage):
    fixed, writable = storage
    digest = hashlib.sha256(PDF).hexdigest()
    metadata = {"filename": f"commitment_{digest}.pdf", "sha256": digest}
    (fixed / metadata["filename"]).write_bytes(PDF)
    assert templates.template_path({"commitment_template": metadata}).parent == fixed
    writable.mkdir(parents=True)
    (writable / metadata["filename"]).write_bytes(b"corrupt")
    with pytest.raises(ConflictException, match="不一致"):
        templates.template_path({"commitment_template": metadata})


@pytest.mark.parametrize("failure", [errno.EROFS, errno.EACCES, errno.ENOSPC])
def test_failed_atomic_publish_reports_storage_unavailable_and_cleans_partial_files(storage, monkeypatch, failure, caplog):
    _, writable = storage
    def failed_replace(*_args):
        raise OSError(failure, "storage fixture failure")
    monkeypatch.setattr(templates.os, "link", failed_replace)
    with pytest.raises(StorageUnavailableException) as raised:
        templates.save_template("承诺书.pdf", PDF)
    assert raised.value.http_status_code == 503 and raised.value.code == 50410
    assert "存储不可用" in raised.value.message
    assert "storage_failed operation=write" in caplog.text
    assert not list(writable.iterdir())


def test_mkdir_and_read_permission_failures_are_translated(storage, monkeypatch):
    _, writable = storage
    original = Path.mkdir
    def blocked_mkdir(path, *args, **kwargs):
        if path == writable:
            raise PermissionError(errno.EACCES, "fixture denied")
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "mkdir", blocked_mkdir)
        with pytest.raises(StorageUnavailableException, match="存储不可用"):
            templates.save_template("承诺书.pdf", PDF)
    metadata = templates.save_template("承诺书.pdf", PDF)
    original_read = Path.read_bytes
    def blocked_read(path):
        if path.parent == writable:
            raise PermissionError(errno.EACCES, "fixture denied")
        return original_read(path)
    monkeypatch.setattr(Path, "read_bytes", blocked_read)
    with pytest.raises(StorageUnavailableException, match="存储不可用"):
        templates.template_path({"commitment_template": metadata})


def make_app(monkeypatch):
    from app.api.admin.renshe import router
    monkeypatch.setattr("app.services.renshe_audit.record_best_effort_audit", _audit)
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)
    app.include_router(router, prefix="/admin")
    for route in router.routes:
        if "/commitment-templates" in route.path:
            for dependency in route.dependant.dependencies:
                app.dependency_overrides[dependency.call] = lambda: SimpleNamespace(id=1)
    return app


async def _audit(**_kwargs):
    pass


@pytest.mark.asyncio
async def test_http_upload_and_download_survive_application_recreation(storage, monkeypatch):
    app = make_app(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/admin/cert-products/renshe/commitment-templates", files={"file": ("承诺书.pdf", PDF, "application/pdf")})
        assert response.status_code == 200
        metadata = response.json()["data"]
    restarted = make_app(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=restarted), base_url="http://test") as client:
        response = await client.get(f"/admin/cert-products/renshe/commitment-templates/{metadata['filename']}")
        assert response.status_code == 200 and response.content == PDF
        assert (await client.get("/admin/cert-products/renshe/commitment-templates/invalid.pdf")).status_code == 404


@pytest.mark.asyncio
async def test_http_storage_failure_is_actionable_not_a_generic_500(storage, monkeypatch):
    app = make_app(monkeypatch)
    def unavailable(*_args, **_kwargs):
        raise OSError(errno.EROFS, "readonly fixture")
    monkeypatch.setattr(templates.tempfile, "NamedTemporaryFile", unavailable)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/admin/cert-products/renshe/commitment-templates", files={"file": ("承诺书.pdf", PDF, "application/pdf")})
    assert response.status_code == 503
    assert response.json()["code"] == 50410
    assert "承诺书模板存储不可用" in response.json()["message"]


def test_deployment_retains_ro_excel_and_private_writable_persistent_volume():
    root = Path(__file__).resolve().parents[2]
    for name in ("docker-compose.yml", "docker-compose.deploy.yml"):
        content = (root / name).read_text(encoding="utf-8")
        assert ":/app/renshe-templates:ro" in content
        assert "RENSHE_COMMITMENT_TEMPLATE_DIR" in content
        assert "/app/uploads/private/renshe-commitment-templates" in content
        assert "uploads:/app/uploads" in content and "chown -R" in content
