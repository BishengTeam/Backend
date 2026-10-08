"""NISP 材料上传接口响应信封回归测试。

线上问题（2026-10-06）：/api/nisp/materials/upload 直接返回裸 dict，
没有 {code: 0, data, message} 信封。小程序按 code !== 0 判定失败，
导致“材料上传失败”——尽管 OSS 里对象实际上已写入成功。
"""

from types import SimpleNamespace
from typing import Any

import pytest
from contextlib import asynccontextmanager

import app.api.nisp as nisp_api
import app.adapter.database as database_module
import app.integrations.nisp_storage as nisp_storage_module
from app.port.exceptions import BusinessException
from app.schemas.common import APIResponse


class _FakeUploadFile:
    def __init__(self, filename: str, content_type: str, data: bytes) -> None:
        self.filename = filename
        self.content_type = content_type
        self._data = data

    async def read(self) -> bytes:
        return self._data


class _FakeStorage:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def save_source(self, **kwargs: Any) -> tuple[str, int, str]:
        self.calls.append(kwargs)
        return ("nisp/materials/1/abc.jpg", 123, "sha256" * 8)


class _FakeSession:
    def add(self, material: Any) -> None:
        material.id = 91

    async def commit(self) -> None:
        return None

    async def refresh(self, _material: Any) -> None:
        return None


@asynccontextmanager
async def _fake_db_ctx():
    yield _FakeSession()


@pytest.mark.asyncio
async def test_upload_returns_api_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    """响应必须是 APIResponse 信封且 data 带 storage_key。"""

    storage = _FakeStorage()
    monkeypatch.setattr(nisp_storage_module, "NispObjectStorage", lambda: storage)
    monkeypatch.setattr(database_module, "get_db_ctx", _fake_db_ctx)

    result = await nisp_api.upload_material(
        material_type="portrait_photo",
        file=_FakeUploadFile("photo.jpg", "image/jpeg", b"jpeg-bytes"),
        current_user=SimpleNamespace(id=1),
    )

    assert isinstance(result, APIResponse)
    assert result.code == 0
    assert result.data.storage_key == "nisp/materials/1/abc.jpg"
    assert result.data.material_type == "portrait_photo"
    assert result.data.material_id == 91
    assert result.data.original_filename == "photo.jpg"
    assert storage.calls[0]["user_id"] == 1


@pytest.mark.asyncio
async def test_upload_rejects_empty_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """空文件必须拒绝且不触达存储。"""

    storage = _FakeStorage()
    monkeypatch.setattr(nisp_storage_module, "NispObjectStorage", lambda: storage)

    with pytest.raises(BusinessException, match="文件不能为空"):
        await nisp_api.upload_material(
            material_type="portrait_photo",
            file=_FakeUploadFile("photo.jpg", "image/jpeg", b""),
            current_user=SimpleNamespace(id=1),
        )
    assert storage.calls == []
