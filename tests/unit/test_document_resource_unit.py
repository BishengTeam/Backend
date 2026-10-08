import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

from app.integrations.document_storage import (
    DocumentObjectStorage,
    validate_document_key,
)
from app.port.exceptions import BusinessException, ValidationException
from app.schemas.document_resource import AdminDocumentCreate, AdminDocumentUpdate
from app.services.document_resource import DocumentResourceService


REPO_ROOT = Path(__file__).resolve().parents[2]


async def _fake_put(_self, key, data, content_type):
    assert key.startswith("documents/")
    assert data.startswith(b"%PDF-")
    assert content_type == "application/pdf"


def test_document_keys_use_a_stable_namespace():
    assert validate_document_key(" h3c.xuexin_verification_guide ") == (
        "h3c.xuexin_verification_guide"
    )
    for invalid in ("", "h3c", "1h3c.guide", "h3c.验证码", "h3c.guide!"):
        try:
            validate_document_key(invalid)
        except ValidationException:
            continue
        raise AssertionError(f"key should be rejected: {invalid}")


def test_document_storage_accepts_only_pdf_extension_mime_and_magic():
    original_put = DocumentObjectStorage._put
    DocumentObjectStorage._put = _fake_put
    valid_data = b"%PDF-1.4 tutorial"
    try:
        result = asyncio.run(
            DocumentObjectStorage().save(
                document_key="h3c.xuexin_verification_guide",
                filename="guide.pdf",
                content_type="application/pdf",
                data=valid_data,
            )
        )
        assert result[1] == len(valid_data)
        assert len(result[2]) == 64

        invalid_cases = [
            ("guide.docx", "application/pdf", b"%PDF-1.4"),
            ("guide.pdf", "image/jpeg", b"%PDF-1.4"),
            ("guide.pdf", "application/pdf", b"<pdf>"),
            ("guide.pdf", "application/pdf", b""),
            ("guide.pdf", "application/octet-stream", b"%PDF-1.4"),
        ]
        for filename, content_type, data in invalid_cases:
            try:
                asyncio.run(
                    DocumentObjectStorage().save(
                        document_key="h3c.xuexin_verification_guide",
                        filename=filename,
                        content_type=content_type,
                        data=data,
                    )
                )
            except ValidationException:
                continue
            raise AssertionError(f"file should be rejected: {filename}/{content_type}")
    finally:
        DocumentObjectStorage._put = original_put


def test_document_update_contract_keeps_document_key_immutable():
    assert "document_key" not in AdminDocumentUpdate.model_fields


def test_user_document_requires_configuration_and_active_status(monkeypatch):
    service = DocumentResourceService()
    async def signed_get_url(*_args, **_kwargs):
        return "https://oss/pdf"
    service.storage.signed_get_url = signed_get_url
    document = SimpleNamespace(
        document_key="h3c.xuexin_verification_guide",
        title="如何查询学籍在线验证码",
        description=None,
        storage_key="documents/h3c/xuexin_verification_guide/a.pdf",
        original_filename="如何查询学籍在线验证码.pdf",
        content_type="application/pdf",
        size_bytes=9,
        sha256="a" * 64,
        version_no=1,
        is_active=True,
    )

    @asynccontextmanager
    async def active_ctx():
        async def scalar(_stmt):
            return document
        yield SimpleNamespace(scalar=scalar)

    @asynccontextmanager
    async def missing_ctx():
        async def scalar(_stmt):
            return None
        yield SimpleNamespace(scalar=scalar)

    inactive = SimpleNamespace(is_active=False)

    @asynccontextmanager
    async def inactive_ctx():
        async def scalar(_stmt):
            return inactive
        yield SimpleNamespace(scalar=scalar)

    monkeypatch.setattr("app.services.document_resource.get_db_ctx", active_ctx)
    item = asyncio.run(service.get_user_document("h3c.xuexin_verification_guide"))
    assert item.download_url == "https://oss/pdf"
    assert item.title == "如何查询学籍在线验证码"

    monkeypatch.setattr("app.services.document_resource.get_db_ctx", missing_ctx)
    try:
        asyncio.run(service.get_user_document("h3c.xuexin_verification_guide"))
    except BusinessException as exc:
        assert "暂未配置" in exc.message
    else:
        raise AssertionError("missing document should fail")

    monkeypatch.setattr("app.services.document_resource.get_db_ctx", inactive_ctx)
    try:
        asyncio.run(service.get_user_document("h3c.xuexin_verification_guide"))
    except BusinessException as exc:
        assert "暂未配置" in exc.message
    else:
        raise AssertionError("inactive document should fail")


def test_fixed_scene_stays_visible_without_an_active_document(monkeypatch):
    service = DocumentResourceService()
    active_document = SimpleNamespace(
        document_key="h3c.xuexin_verification_guide",
        title="如何查询学籍在线验证码",
        entry_text="查看学信网教程PDF",
        is_active=True,
        version_no=2,
        size_bytes=652910,
    )
    inactive_document = SimpleNamespace(is_active=False)

    @asynccontextmanager
    async def active_ctx():
        async def scalar(_stmt):
            return active_document
        yield SimpleNamespace(scalar=scalar)

    @asynccontextmanager
    async def missing_ctx():
        async def scalar(_stmt):
            return None
        yield SimpleNamespace(scalar=scalar)

    @asynccontextmanager
    async def inactive_ctx():
        async def scalar(_stmt):
            return inactive_document
        yield SimpleNamespace(scalar=scalar)

    monkeypatch.setattr("app.services.document_resource.get_db_ctx", active_ctx)
    configured = asyncio.run(service.get_user_scene("h3c_student_xuexin_guide"))
    assert configured.entry_text == "查看学信网教程PDF"
    assert configured.entry_mode == "required"
    assert configured.document is not None
    assert configured.document.version_no == 2

    monkeypatch.setattr("app.services.document_resource.get_db_ctx", missing_ctx)
    missing = asyncio.run(service.get_user_scene("h3c_student_xuexin_guide"))
    assert missing.entry_mode == "required"
    assert missing.document is None
    assert missing.entry_text == "查看《如何查询学籍在线验证码》PDF"

    monkeypatch.setattr("app.services.document_resource.get_db_ctx", inactive_ctx)
    disabled = asyncio.run(service.get_user_scene("h3c_student_xuexin_guide"))
    assert disabled.document is None


def test_initial_h3c_document_is_seeded_from_the_repository_pdf():
    source = (REPO_ROOT / "scripts/seed_h3c_document.py").read_text(encoding="utf-8")
    pdf = REPO_ROOT / "docs/h3c/如何查询学籍在线验证码.pdf"

    assert 'DOCUMENT_KEY = "h3c.xuexin_verification_guide"' in source
    assert "import app.models" in source
    assert "如何查询学籍在线验证码" in source
    assert pdf.is_file()
    assert pdf.stat().st_size > 100000


def test_document_api_requires_login_and_admin_permissions():
    user_api = (REPO_ROOT / "app/api/documents.py").read_text(encoding="utf-8")
    admin_api = (REPO_ROOT / "app/api/admin/documents.py").read_text(encoding="utf-8")
    permissions = (REPO_ROOT / "app/policy/permissions.py").read_text(encoding="utf-8")

    assert "Depends(get_current_user)" in user_api
    assert 'require_permission("document:read")' in admin_api
    assert 'require_permission("document:write")' in admin_api
    assert '"document:read"' in permissions
    assert '"document:write"' in permissions


def test_document_scene_api_is_explicit_and_authenticated():
    source = (REPO_ROOT / "app/api/documents.py").read_text(encoding="utf-8")

    assert '"/scenes/{scene}"' in source
    assert "get_document_scene" in source
    assert "Depends(get_current_user)" in source


def test_replacement_upload_takes_effect_immediately():
    service = (REPO_ROOT / "app/services/document_resource.py").read_text(
        encoding="utf-8"
    )

    assert "document.version_no += 1" in service
    assert "document.is_active = True" in service


def test_admin_document_upload_rejects_invalid_files_before_database_write():
    class InvalidPdfUpload:
        filename = "guide.pdf"
        content_type = "application/pdf"

        async def read(self) -> bytes:
            return b"not a pdf"

    service = DocumentResourceService()

    try:
        asyncio.run(
            service.create(
                AdminDocumentCreate(
                    document_key="h3c.xuexin_verification_guide",
                    title="如何查询学籍在线验证码",
                ),
                file=InvalidPdfUpload(),
                admin_id=1,
            )
        )
    except ValidationException:
        return
    raise AssertionError("invalid PDF content should be rejected")
