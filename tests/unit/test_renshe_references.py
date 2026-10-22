from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.api.admin.renshe import router
from app.middleware.auth import require_normal_admin
from app.middleware.error_handler import app_exception_handler
from app.port.config import settings
from app.port.exceptions import AppException, NotFoundException
from app.services.renshe_references import DOCUMENTS, reference_file


@pytest.mark.asyncio
async def test_reference_auth_permission_and_bundled_response(monkeypatch):
    monkeypatch.setattr(settings, "RENSHE_REFERENCE_DIR", "")
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(AppException, app_exception_handler)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get('/cert-products/renshe/references/conditions')).status_code == 401
        app.dependency_overrides[require_normal_admin] = lambda: SimpleNamespace(role="no-permissions", id=1)
        assert (await client.get('/cert-products/renshe/references/conditions')).status_code == 403
        app.dependency_overrides[require_normal_admin] = lambda: SimpleNamespace(role="cert_admin", id=1)
        for document_id in DOCUMENTS:
            result = await client.get('/cert-products/renshe/references/' + document_id)
            assert result.status_code == 200
            assert result.headers['cache-control'] == 'private, no-store'
            path, mime = reference_file(document_id)
            if mime == 'text/plain':
                assert 'charset=utf-8' in result.headers['content-type']
                assert result.text
            else:
                assert result.content == path.read_bytes()
                assert result.headers['content-disposition'].startswith('inline' if mime == 'application/pdf' else 'attachment')
        assert (await client.get('/cert-products/renshe/references/unknown')).status_code == 404


def test_reference_whitelist_and_missing_deployment(monkeypatch, tmp_path):
    for document_id in ('../conditions', '/etc/passwd', 'conditions.pdf', 'C:/Windows/win.ini'):
        with pytest.raises(NotFoundException):
            reference_file(document_id)
    monkeypatch.setattr(settings, 'RENSHE_REFERENCE_DIR', str(tmp_path))
    with pytest.raises(NotFoundException, match='未部署'):
        reference_file('conditions')
