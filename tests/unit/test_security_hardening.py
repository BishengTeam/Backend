"""安全整改回归：HTML 消毒、上传白名单、可信客户端 IP。"""
import asyncio
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import UploadFile
from pydantic import ValidationError

from app.middleware.client_ip import trusted_client_ip
from app.schemas.admin_agreement_template import AdminAgreementTemplateCreate
from app.utils.html_sanitize import sanitize_html


def test_html_sanitizer_removes_scripts_and_event_handlers():
    dirty = (
        '<p onclick="steal()">hello<script>alert(1)</script></p>'
        '<img src="javascript:alert(1)" onerror="alert(2)">'
        '<a href="javascript:alert(3)">click</a>'
        '<iframe src="https://evil.example"></iframe>'
    )
    cleaned = sanitize_html(dirty)

    assert "<script" not in cleaned
    assert "onclick" not in cleaned
    assert "onerror" not in cleaned
    assert "javascript:" not in cleaned
    assert "<iframe" not in cleaned
    assert "hello" in cleaned
    assert "click" in cleaned


def test_html_sanitizer_keeps_layout_and_hardens_links():
    cleaned = sanitize_html(
        '<p style="text-align:center;background:url(x)">A</p>'
        '<a href="https://example.com" title="t">link</a>'
        '<table><tr><td colspan="2">cell</td></tr></table>'
    )

    assert 'style="text-align: center"' in cleaned
    assert "url(" not in cleaned
    assert 'rel="noopener noreferrer"' in cleaned
    assert 'target="_blank"' in cleaned
    assert 'colspan="2"' in cleaned


def test_agreement_content_is_sanitized_on_write():
    model = AdminAgreementTemplateCreate(
        type="user_terms",
        title="协议",
        content='<p>正文</p><script>alert(1)</script>',
    )

    assert "<script" not in model.content
    assert "正文" in model.content

    with pytest.raises(ValidationError):
        AdminAgreementTemplateCreate(
            type="user_terms",
            title="协议",
            content="<script>alert(1)</script>",
        )


def test_upload_rejects_executable_and_spoofed_files(tmp_path, monkeypatch):
    import app.services.upload as upload_module

    monkeypatch.setattr(upload_module, "UPLOAD_DIR", str(tmp_path))
    service = upload_module.UploadService

    html_file = UploadFile(filename="evil.html", file=BytesIO(b"<script>x</script>"))
    with pytest.raises(Exception, match="不支持的文件类型"):
        asyncio.run(service.save_file(html_file))

    spoofed = UploadFile(filename="fake.png", file=BytesIO(b"<html>not an image</html>"))
    with pytest.raises(Exception, match="文件内容与扩展名不匹配"):
        asyncio.run(service.save_file(spoofed))


def test_upload_subdirectory_keys_resolve_for_media_and_delete(tmp_path, monkeypatch):
    import app.services.upload as upload_module

    monkeypatch.setattr(upload_module, "UPLOAD_DIR", str(tmp_path))
    service = upload_module.UploadService
    upload = UploadFile(
        filename="guide.pdf",
        file=BytesIO(b"%PDF-1.7\n" + b"x" * 32),
    )

    saved = asyncio.run(service.save_file(upload, sub_dir="cert-attachments"))

    assert saved["file_id"].startswith("cert-attachments/")
    assert saved["url"] == f"/api/media/{saved['file_id']}"
    assert service.file_exists(saved["file_id"]) is True
    assert service.get_file_path(saved["file_id"]).is_file()
    assert service.file_exists("course/secret.mp4") is False
    assert service.delete_file_by_url(saved["url"]) is True
    assert service.file_exists(saved["file_id"]) is False


def test_media_endpoint_serves_cert_attachment_subdirectory(tmp_path, monkeypatch):
    import app.services.upload as upload_module
    from app.api.upload import get_file

    monkeypatch.setattr(upload_module, "UPLOAD_DIR", str(tmp_path))
    upload = UploadFile(
        filename="notice.pdf",
        file=BytesIO(b"%PDF-1.7\n" + b"y" * 32),
    )
    saved = asyncio.run(upload_module.UploadService.save_file(
        upload, sub_dir="cert-attachments"
    ))

    response = asyncio.run(get_file(saved["file_id"]))
    assert response.path.endswith(saved["file_id"])
    with pytest.raises(Exception):
        asyncio.run(get_file("course/secret.mp4"))


def test_legacy_dangerous_media_files_are_not_served(tmp_path, monkeypatch):
    import app.services.upload as upload_module
    from app.api.upload import get_file
    from app.port.exceptions import NotFoundException

    monkeypatch.setattr(upload_module, "UPLOAD_DIR", str(tmp_path))
    (tmp_path / "legacy.html").write_text("<script>alert(1)</script>", encoding="utf-8")
    (tmp_path / "legacy.svg").write_text("<svg onload=alert(1)>", encoding="utf-8")

    assert upload_module.UploadService.file_exists("legacy.html") is False
    assert upload_module.UploadService.file_exists("legacy.svg") is False
    with pytest.raises(NotFoundException):
        asyncio.run(get_file("legacy.html"))


def _request(client_host: str, headers: dict[str, str]):
    return SimpleNamespace(
        client=SimpleNamespace(host=client_host),
        headers=headers,
    )


def test_trusted_client_ip_ignores_forwarding_from_public_peers():
    request = _request("203.0.113.9", {"x-forwarded-for": "1.1.1.1"})
    assert trusted_client_ip(request) == "203.0.113.9"


def test_trusted_client_ip_uses_forwarding_behind_private_proxy():
    request = _request("172.18.0.2", {"x-forwarded-for": "198.51.100.7, 172.18.0.2"})
    assert trusted_client_ip(request) == "198.51.100.7"

    real_ip = _request("127.0.0.1", {"x-real-ip": "198.51.100.8"})
    assert trusted_client_ip(real_ip) == "198.51.100.8"


def test_request_id_middleware_normalizes_untrusted_values():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.middleware.request_id import RequestIDMiddleware

    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/ping")
    def ping():
        return {"ok": True}

    client = TestClient(app)
    accepted = client.get("/ping", headers={"X-Request-ID": "trace-01:abc"})
    assert accepted.headers["x-request-id"] == "trace-01:abc"

    rejected = client.get("/ping", headers={"X-Request-ID": "x" * 100})
    assert rejected.headers["x-request-id"] != "x" * 100
    assert len(rejected.headers["x-request-id"]) == 36
