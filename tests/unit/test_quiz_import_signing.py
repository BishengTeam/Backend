"""本地签名导入文件链接的纯逻辑校验（不依赖数据库）。"""

from __future__ import annotations

import time

import pytest

from app.port.exceptions import ValidationException
from app.services.admin_quiz import AdminQuizService


@pytest.mark.asyncio
async def test_local_signed_import_object_rejects_expired_link() -> None:
    expires = int(time.time()) - 10
    with pytest.raises(ValidationException, match="已过期"):
        await AdminQuizService()._read_local_signed_import_object(
            1,
            object_kind="source",
            expires=expires,
            admin_id=1,
            token="x",
        )


@pytest.mark.asyncio
async def test_local_signed_import_object_rejects_invalid_token() -> None:
    expires = int(time.time()) + 3600
    with pytest.raises(ValidationException, match="无效"):
        await AdminQuizService()._read_local_signed_import_object(
            1,
            object_kind="source",
            expires=expires,
            admin_id=1,
            token="bad-token",
        )
