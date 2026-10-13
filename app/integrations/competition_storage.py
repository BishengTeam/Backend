"""Private storage boundary for competition attachment PDF originals.

原件仅供留档/重渲染，绝不经 /media 对外暴露；与运营文档、NISP 材料共用
RENSHE_STORAGE_TYPE 开关和同一组 ALIYUN_OSS_* 配置（私有桶）。
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from app.port.config import settings
from app.port.exceptions import ThirdPartyException, ValidationException


class CompetitionAttachmentStorage:

    def __init__(self) -> None:
        self.storage_type = settings.RENSHE_STORAGE_TYPE

    async def save_original(self, *, data: bytes) -> str:
        storage_key = f"competition-attachments/{uuid.uuid4().hex}.pdf"
        await self._put(storage_key, data, "application/pdf")
        return storage_key

    async def _put(self, storage_key: str, data: bytes, content_type: str) -> None:
        if self.storage_type == "local":
            path = self._local_path(storage_key)
            path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(path.write_bytes, data)
            return
        if self.storage_type != "aliyun_oss":
            raise ThirdPartyException("赛事附件 OSS 未配置")

        def _upload() -> None:
            import oss2

            result = self._bucket().put_object(
                storage_key,
                data,
                headers={"Content-Type": content_type},
            )
            if result.status // 100 != 2:
                raise ThirdPartyException("赛事附件上传失败")

        await asyncio.to_thread(_upload)

    @staticmethod
    def _local_path(storage_key: str) -> Path:
        root = (Path(settings.UPLOAD_DIR).resolve() / "private").resolve()
        target = (root / storage_key).resolve()
        if root not in target.parents:
            raise ValidationException("赛事附件对象键无效")
        return target

    @staticmethod
    def _bucket():
        import oss2

        if not all(
            (
                settings.ALIYUN_OSS_ENDPOINT,
                settings.ALIYUN_OSS_BUCKET,
                settings.ALIYUN_OSS_ACCESS_KEY_ID,
                settings.ALIYUN_OSS_ACCESS_KEY_SECRET,
            )
        ):
            raise ThirdPartyException("赛事附件 OSS 配置不完整")
        auth = oss2.Auth(
            settings.ALIYUN_OSS_ACCESS_KEY_ID,
            settings.ALIYUN_OSS_ACCESS_KEY_SECRET,
        )
        return oss2.Bucket(
            auth,
            settings.ALIYUN_OSS_ENDPOINT,
            settings.ALIYUN_OSS_BUCKET,
        )
