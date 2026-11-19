"""公开材料审计脚本的可测试部分：引用遍历、公开根目录范围、受控删除。"""
from pathlib import Path

import scripts.audit_public_verification_materials as audit_module


def test_walk_json_finds_nested_media_references():
    payload = {
        "materials": [{"url": "/api/media/a.pdf"}, {"url": "https://cdn/b.pdf"}],
        "nested": {"url": "/api/media/c.jpg"},
    }
    found = [value for _, value in audit_module._walk_json(payload, "root")]

    assert "/api/media/a.pdf" in found
    assert "/api/media/c.jpg" in found
    assert "https://cdn/b.pdf" in found


def test_orphan_scan_only_covers_public_roots(tmp_path, monkeypatch):
    root = tmp_path / "uploads"
    (root / "cert-attachments").mkdir(parents=True)
    (root / "private").mkdir()
    (root / "used.jpg").write_bytes(b"x")
    (root / "orphan.jpg").write_bytes(b"x")
    (root / "legacy.html").write_bytes(b"x")
    (root / "cert-attachments" / "notice.pdf").write_bytes(b"x")
    (root / "private" / "secret.jpg").write_bytes(b"x")
    monkeypatch.setattr(audit_module, "UPLOAD_DIR", str(root))

    dangerous, orphans = audit_module._scan_public_files(
        {"used.jpg", "notice.pdf"}
    )

    assert [path.name for path in dangerous] == ["legacy.html"]
    assert [path.name for path in orphans] == ["orphan.jpg"]
    # 私有目录永远不进入公开扫描范围。
    assert all("private" not in str(path) for path in dangerous + orphans)


def test_delete_media_url_is_limited_to_upload_root(tmp_path, monkeypatch):
    root = tmp_path / "uploads"
    (root / "cert-attachments").mkdir(parents=True)
    target = root / "cert-attachments" / "notice.pdf"
    target.write_bytes(b"x")
    monkeypatch.setattr(audit_module, "UPLOAD_DIR", str(root))

    assert audit_module.delete_media_url("/api/media/cert-attachments/notice.pdf")
    assert not target.exists()

    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    assert not audit_module.delete_media_url("/api/media/../../outside.txt")
    assert outside.exists()

    dangerous = root / "legacy.html"
    dangerous.write_bytes(b"x")
    # 清理脚本允许删除历史危险扩展名（与公开可读性白名单解耦）。
    assert audit_module.delete_media_url("/api/media/legacy.html")
    assert not dangerous.exists()


def test_public_value_helper_only_matches_media_prefix():
    assert audit_module._public_value("/api/media/x.jpg") == "/api/media/x.jpg"
    assert audit_module._public_value("https://cdn/x.jpg") is None
    assert audit_module._public_value(None) is None
