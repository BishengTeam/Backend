"""课堂问答题附件：规则、消毒、归属过滤与链路接线。"""

from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def test_attachment_rules_are_strict() -> None:
    from app.port.exceptions import ValidationException
    from app.services.classroom_attachment import validate_attachment

    assert validate_attachment(
        filename="shot.png", content_type="image/png", size_bytes=1024
    ) == (".png", "image", "image/png")
    assert validate_attachment(
        filename="作业.docx",
        content_type=(
            "application/vnd.openxmlformats-officedocument"
            ".wordprocessingml.document"
        ),
        size_bytes=1024,
    )[:2] == (".docx", "document")
    assert validate_attachment(
        filename="project.zip", content_type="application/zip", size_bytes=1024
    )[:2] == (".zip", "archive")

    with pytest.raises(ValidationException, match="JPG"):
        validate_attachment(
            filename="a.gif", content_type="image/gif", size_bytes=10
        )
    with pytest.raises(ValidationException, match="10MB"):
        validate_attachment(
            filename="a.png", content_type="image/png", size_bytes=10 * 1024 * 1024 + 1
        )
    with pytest.raises(ValidationException, match="20MB"):
        validate_attachment(
            filename="a.docx",
            content_type="application/vnd.openxmlformats-officedocument"
            ".wordprocessingml.document",
            size_bytes=20 * 1024 * 1024 + 1,
        )
    with pytest.raises(ValidationException, match="50MB"):
        validate_attachment(
            filename="a.zip",
            content_type="application/zip",
            size_bytes=50 * 1024 * 1024 + 1,
        )
    with pytest.raises(ValidationException, match="仅支持"):
        validate_attachment(
            filename="a.rar", content_type="application/octet-stream", size_bytes=10
        )


def test_normalize_filename_strips_paths() -> None:
    from app.services.classroom_attachment import normalize_filename

    assert normalize_filename("../../etc/passwd") == "passwd"
    assert normalize_filename("C:\\Users\\stu\\作业.docx") == "作业.docx"
    assert normalize_filename("  答案.zip ") == "答案.zip"


def test_sanitize_short_answer_html_whitelist() -> None:
    from app.services.classroom_attachment import sanitize_short_answer_html

    raw = (
        "<p>步骤<strong>一</strong></p>"
        "<script>alert(1)</script>"
        '<img src="https://evil.example/x.png" onerror="alert(2)">'
        '<h2 onclick="hack()">标题</h2>'
        "<table><tr><td>表格</td></tr></table>"
    )
    clean = sanitize_short_answer_html(raw)
    assert "<script" not in clean
    assert "onerror" not in clean
    assert "onclick" not in clean
    assert "<table" not in clean
    assert "alert(1)" in clean  # 脚本文本保留为转义文本，不执行
    assert "<p>步骤<strong>一</strong></p>" in clean
    assert "<h2>标题</h2>" in clean
    assert 'src="https://evil.example/x.png"' in clean  # 属性保留，归属过滤在下一层


def test_attachment_key_extraction() -> None:
    from app.services.classroom_attachment import extract_attachment_key

    key = "classroom-attachments/inst/c1/q2/u3/9/abc.png"
    assert extract_attachment_key(key) == key
    assert extract_attachment_key(
        "https://bucket.oss.example.com/classroom-attachments/inst/c1/a.png"
        "?Expires=1&Signature=%2Babc"
    ) == "classroom-attachments/inst/c1/a.png"
    assert extract_attachment_key("https://cdn.example.com/other.png") is None
    assert extract_attachment_key("") is None


def test_canonicalize_strips_foreign_and_resign_rewrites(monkeypatch) -> None:
    from app.services import classroom_attachment as service

    own = "classroom-attachments/inst/c1/q2/u3/9/own.png"
    foreign = "https://cdn.example.com/foreign.png"
    html = (
        f'<p>看图</p><img src="{own}?Expires=1"><img src="{foreign}">'
        f'<img src="classroom-attachments/inst/c1/q2/u3/9/own.png">'
    )
    canonical = service.canonicalize_short_answer_html(html, {own})
    assert canonical.count("<img") == 2  # own key 两种写法都保留
    assert foreign not in canonical
    assert f'src="{own}"' in canonical

    class _FakeBucket:
        def sign_url(self, method, key, ttl, slash_safe=False):
            assert method == "GET"
            return f"https://signed.example.com/{key}?sig=1"

    monkeypatch.setattr(
        service.CourseStorage, "_bucket", staticmethod(lambda: _FakeBucket())
    )
    resigned = service.resign_short_answer_html(
        canonical, service.make_read_signer()
    )
    assert f'src="https://signed.example.com/{own}?sig=1"' in resigned


def test_attachment_flow_wiring_present() -> None:
    api = (BACKEND_ROOT / "app" / "api" / "classroom.py").read_text(encoding="utf-8")
    service = (BACKEND_ROOT / "app" / "services" / "classroom.py").read_text(
        encoding="utf-8"
    )
    admin = (BACKEND_ROOT / "app" / "services" / "classroom_admin.py").read_text(
        encoding="utf-8"
    )
    cleanup = (BACKEND_ROOT / "app" / "services" / "cleanup.py").read_text(
        encoding="utf-8"
    )
    migration = (
        BACKEND_ROOT
        / "alembic"
        / "versions"
        / "cls003_classroom_quiz_attachment.py"
    )

    assert "/quizzes/{quiz_id}/attachments/upload-url" in api
    assert "/quizzes/{quiz_id}/submission" in api
    assert "body.attachments" in api
    assert "bind_submitted_attachments" in service
    assert "quiz_submission_detail" in service
    assert 'sub.status != "approved"' in service
    assert "attachments_by_submission" in admin
    assert "resign_short_answer_html" in admin
    assert "cleanup_classroom_attachments" in cleanup
    assert migration.exists()
    assert "ck_classroom_attachment_kind" in migration.read_text(encoding="utf-8")
