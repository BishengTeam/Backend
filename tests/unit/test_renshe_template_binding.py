from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace
import zipfile

import httpx
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from sqlalchemy import select

from app.api.admin.renshe import router as admin_router
from app.api.renshe import router as user_router
from app.domain.plan.src.index import Plan
from app.domain.renshe.src.index import RensheApplicationVersion
from app.middleware.auth import require_normal_admin, get_current_user
from app.middleware.error_handler import app_exception_handler, validation_exception_handler
from app.models.renshe_project import RensheProjectConfig
from app.port.exceptions import AppException, BusinessException
from app.schemas.renshe_project import RensheProjectWrite
from app.services.renshe_application import RensheApplicationService
from app.services.renshe_graduated import save_template, template_path, commitment_policy, verified_template
from app.services.renshe_project import RensheProjectService
from tests.unit.test_renshe_projects import isolated
from tests.unit.test_renshe_graduated import graduated_batch, uploads, draft_payload


def docx():
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>')
    return output.getvalue()


def app():
    instance = FastAPI()
    instance.include_router(admin_router)
    instance.include_router(user_router)
    instance.add_exception_handler(AppException, app_exception_handler)
    instance.add_exception_handler(RequestValidationError, validation_exception_handler)
    instance.dependency_overrides[require_normal_admin] = lambda: SimpleNamespace(id=1, role="cert_admin")
    instance.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    return instance


@pytest.mark.asyncio
async def test_product_only_binding_user_download_and_historical_template(isolated, monkeypatch):
    project, batch = await graduated_batch(isolated, monkeypatch)
    initial = commitment_policy(project.config.model_dump())["commitment_template"]
    application = RensheApplicationService(isolated.storage)
    await uploads(application, batch)
    draft = await application.save_draft(1, draft_payload(batch))
    submitted = await application.submit(1, draft.id)
    other = await RensheProjectService().save(RensheProjectWrite(chinese_name="另一产品", selection_id="network_security:4:学历型"), admin_id=1)
    async with isolated.db() as db:
        original = deepcopy((await db.get(RensheProjectConfig, project.config_id)).config)
        frozen = deepcopy((await db.get(Plan, batch.id)).renshe_config)
    pdf = b"%PDF-1.4\nnew blank commitment"
    instance = app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=instance), base_url="http://test") as client:
        uploaded = await client.post('/cert-products/renshe/commitment-templates', files={"file": ("新模板.pdf", pdf, "application/pdf")})
        assert uploaded.status_code == 200
        metadata = uploaded.json()["data"]
        assert metadata["size_bytes"] == len(pdf)
        # Upload without binding must not change the user's current download.
        old_download = await client.get(f'/renshe/batches/{batch.id}/commitment-template')
        assert old_download.content == template_path({"commitment_template": initial}).read_bytes()
        before = next(item for item in await RensheProjectService().list_projects() if item.id == project.id)
        assert before.version == project.version
        result = await client.put(f'/cert-products/renshe/projects/{project.id}/commitment-template', json={"expected_version": project.version, "commitment_template": {**metadata, "size_bytes": 1, "content_type": "untrusted"}})
        assert result.status_code == 200, result.text
        updated = result.json()["data"]
        assert updated["version"] == project.version + 1
        assert updated["commitment_template"]["size_bytes"] == len(pdf)
        assert updated["commitment_template"]["content_type"] == 'application/pdf'
        assert updated["chinese_name"] == project.chinese_name and updated["status"] == project.status
        assert {key: value for key, value in updated["config"].items() if key != 'commitment_template'} == {key: value for key, value in original.items() if key != 'commitment_template'}
        assert (await client.get(f'/renshe/batches/{batch.id}/commitment-template')).content == pdf
        stale = await client.put(f'/cert-products/renshe/projects/{project.id}/commitment-template', json={"expected_version": project.version, "commitment_template": initial})
        assert stale.status_code == 409
        word = save_template('另一承诺书.docx', docx())
        bound_other = await client.put(f'/cert-products/renshe/projects/{other.id}/commitment-template', json={"expected_version": other.version, "commitment_template": word})
        assert bound_other.status_code == 200
        assert bound_other.json()["data"]["commitment_template"]["filename"].endswith('.docx')
        assert (await client.get(f'/renshe/batches/{batch.id}/commitment-template')).content == pdf
        forbidden_fields = await client.put(f'/cert-products/renshe/projects/{project.id}/commitment-template', json={"expected_version": updated["version"], "commitment_template": metadata, "status": "disabled", "config": {}})
        assert forbidden_fields.status_code == 422
        bad_hash = await client.put(f'/cert-products/renshe/projects/{project.id}/commitment-template', json={"expected_version": updated["version"], "commitment_template": {**metadata, "sha256": "a" * 64}})
        assert bad_hash.status_code == 409
    async with isolated.db() as db:
        assert (await db.get(Plan, batch.id)).renshe_config == frozen
        assert (await db.get(RensheProjectConfig, project.config_id)).config == original
        version = await db.get(RensheApplicationVersion, submitted.version.id)
        assert version.form_data["commitment_template"]["sha256"] == initial["sha256"]
        assert template_path({"commitment_template": version.form_data["commitment_template"]}).is_file()


@pytest.mark.asyncio
async def test_binding_route_preserves_content_permissions(isolated):
    instance = app()
    url = '/cert-products/renshe/projects/1/commitment-template'
    payload = {"expected_version": 1, "commitment_template": {"filename": f"commitment_{'a'*64}.pdf", "sha256": 'a'*64}}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=instance), base_url='http://test') as client:
        instance.dependency_overrides.pop(require_normal_admin)
        assert (await client.put(url, json=payload)).status_code == 401
        instance.dependency_overrides[require_normal_admin] = lambda: SimpleNamespace(id=1, role='quiz_admin')
        assert (await client.put(url, json=payload)).status_code == 403


@pytest.mark.parametrize('filename,content', [('test.pdf', b'not pdf'), ('test.docx', b'not zip'), ('test.jpg', b'%PDF-1.4'), ('test.pdf', b''), ('test.pdf', b'%PDF-' + b'a'*(10*1024*1024))], ids=['bad_pdf', 'bad_docx', 'bad_extension', 'empty', 'oversized'])
def test_file_validation_is_reused_for_upload_and_binding(filename, content):
    with pytest.raises(BusinessException):
        save_template(filename, content)


def test_binding_rechecks_actual_stored_content_not_only_client_metadata(tmp_path, monkeypatch):
    import hashlib
    from app.port.config import settings
    monkeypatch.setattr(settings, 'RENSHE_COMMITMENT_TEMPLATE_DIR', str(tmp_path))
    content = b'not actually a PDF'
    digest = hashlib.sha256(content).hexdigest()
    path = tmp_path / f'commitment_{digest}.pdf'
    path.write_bytes(content)
    with pytest.raises(BusinessException, match='PDF格式不正确'):
        verified_template({'filename': path.name, 'sha256': digest})
