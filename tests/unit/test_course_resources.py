from pathlib import Path
from unittest.mock import Mock

import pytest

from app.port.exceptions import ValidationException
from app.services.course_storage import validate_upload
from app.services.course_storage import CourseStorage
from app.services.course_resource_validation import inspect_resource


def test_pdf_is_parsed_and_counted_without_faking_duration(tmp_path):
    import pypdfium2 as pdfium
    path = tmp_path / "基础知识.pdf"
    with pdfium.PdfDocument.new() as document:
        document.new_page(600, 800).close()
        document.new_page(600, 800).close()
        document.save(str(path))
    assert inspect_resource(path) == {"resource_type": "document", "page_count": 2, "duration": None, "content_type": "application/pdf"}


@pytest.mark.parametrize("content", [b"%PDF-1.4 invalid", b"not a pdf", b""])
def test_bad_pdf_is_rejected(tmp_path, content):
    path = tmp_path / "broken.pdf"
    path.write_bytes(content)
    with pytest.raises(ValidationException, match="PDF"):
        inspect_resource(path)


def test_no_pages_pdf_is_rejected(tmp_path):
    import pypdfium2 as pdfium
    path = tmp_path / "empty.pdf"
    with pdfium.PdfDocument.new() as document:
        document.save(str(path))
    with pytest.raises(ValidationException):
        inspect_resource(path)


def test_pdf_limit_and_office_extensions_are_rejected():
    for name, size in [("large.pdf", 50 * 1024 * 1024 + 1), ("office.docx", 100), ("slides.pptx", 100)]:
        with pytest.raises(ValidationException):
            validate_upload(kind="chapter_file", filename=name, content_type="application/octet-stream", size_bytes=size)
    assert validate_upload(kind="chapter_file", filename="OK.PDF", content_type="application/pdf", size_bytes=50 * 1024 * 1024) == ".pdf"


def test_pdf_upload_can_be_explicitly_paused_without_blocking_video(monkeypatch):
    from app.port.config import settings
    monkeypatch.setattr(settings, "COURSE_DOCUMENT_UPLOADS_ENABLED", False)
    with pytest.raises(ValidationException, match="PDF 上传暂未开放"):
        validate_upload(kind="chapter_file", filename="chapter.pdf", content_type="application/pdf", size_bytes=100)
    assert validate_upload(kind="chapter_file", filename="chapter.mp4", content_type="video/mp4", size_bytes=100) == ".mp4"
    monkeypatch.setattr(settings, "COURSE_DOCUMENT_UPLOADS_ENABLED", True)
    assert validate_upload(kind="chapter_file", filename="chapter.pdf", content_type="application/pdf", size_bytes=100) == ".pdf"


def test_a_renamed_pdf_does_not_pass_video_validation(tmp_path, monkeypatch):
    path = tmp_path / "renamed.mp4"
    path.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr("app.services.course_resource_validation.subprocess.run", Mock(return_value=Mock(stdout='{"format":{"duration":"100","format_name":"pdf"},"streams":[]}')))
    with pytest.raises(ValidationException):
        inspect_resource(path)


@pytest.mark.asyncio
async def test_lost_multipart_completion_response_requires_matching_actual_object(monkeypatch):
    from types import SimpleNamespace
    class AlreadyCompleted(Exception):
        code = 'NoSuchUpload'
    bucket = SimpleNamespace(head_object=Mock(return_value=SimpleNamespace(content_length=100, headers={'Content-Type': 'application/pdf'})))
    monkeypatch.setattr(CourseStorage, '_bucket', staticmethod(lambda: bucket))
    monkeypatch.setattr(CourseStorage, '_list_all_parts', staticmethod(Mock(side_effect=AlreadyCompleted())))
    assert (await CourseStorage.complete('private/doc.pdf', 'lost-response', 100)).size_bytes == 100
    with pytest.raises(ValidationException):
        await CourseStorage.complete('private/doc.pdf', 'lost-response', 101)
