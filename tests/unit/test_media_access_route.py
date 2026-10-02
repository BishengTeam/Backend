"""公开媒体文件访问接口的回归测试。

历史缺陷：接口中残留了对已被删除的 ``Course.video_url`` 字段的查询，
导致所有已存在的媒体文件访问都抛 ``AttributeError`` 并返回 HTTP 500。
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.upload import media_router
from app.middleware.error_handler import app_exception_handler
from app.port.exceptions import AppException
from app.services import upload as upload_service


@pytest.fixture()
def media_client(monkeypatch: pytest.MonkeyPatch, tmp_path) -> TestClient:
    monkeypatch.setattr(upload_service, "UPLOAD_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(media_router, prefix="/api")
    app.add_exception_handler(AppException, app_exception_handler)
    return TestClient(app)


def test_existing_media_file_returns_image(media_client: TestClient, tmp_path) -> None:
    payload = b"\x89PNG\r\n\x1a\nregression-body"
    (tmp_path / "bd804f8ede9346c4b704e0c415d3e275.png").write_bytes(payload)

    response = media_client.get("/api/media/bd804f8ede9346c4b704e0c415d3e275.png")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.content == payload


def test_missing_media_file_returns_not_found(media_client: TestClient) -> None:
    response = media_client.get("/api/media/not-exists.png")

    assert response.status_code == 404
    assert response.json()["code"] == 40300
