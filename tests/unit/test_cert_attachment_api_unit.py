from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.api.admin.cert_attachment import AttachmentCreate
from app.api.certification_journey import _resolve_plan_id


class FakeDb:
    async def get(self, model, ident):
        return SimpleNamespace(plan_id=99) if ident == 7 else None


@pytest.mark.parametrize("vendor", ["h3c", "nisp"])
async def test_legacy_batch_ids_resolve_to_plan(vendor):
    assert await _resolve_plan_id(FakeDb(), vendor, 7) == 99
    # 批次不存在时不得把数字直接当 Plan ID（否则可能读到其它批次附件）。
    assert await _resolve_plan_id(FakeDb(), vendor, 8) is None


async def test_sangfor_batch_id_is_already_a_plan_id():
    assert await _resolve_plan_id(FakeDb(), "sangfor", 7) == 7


@pytest.mark.parametrize("url", [
    "javascript:alert(1)",
    "https://evil.example/guide.pdf",
    "/api/media/cert-attachments/../secret.pdf",
    "/api/media/cert-attachments/guide.exe",
    "/api/media/guide.pdf",
])
def test_attachment_url_must_be_local_uploaded_material(url):
    with pytest.raises(ValidationError):
        AttachmentCreate(vendor="sangfor", title="材料", file_url=url)


def test_attachment_url_accepts_generated_media_key():
    model = AttachmentCreate(
        vendor="sangfor",
        title="材料",
        file_url="/api/media/cert-attachments/1a2b3c.pdf",
    )
    assert model.file_url.endswith("1a2b3c.pdf")

    # 与 UploadService 白名单共用：gif/mov 等能上传即可挂载，不再出现双份白名单漂移。
    assert AttachmentCreate(
        vendor="sangfor", title="材料", file_url="/api/media/cert-attachments/1a2b3c.gif"
    ).file_url.endswith(".gif")
