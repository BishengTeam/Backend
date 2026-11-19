import asyncio
import logging
import os
import re
import uuid
from pathlib import Path

from fastapi import UploadFile

from app.port.config import settings
from app.port.exceptions import BusinessException

UPLOAD_DIR = settings.UPLOAD_DIR

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# 公开媒体只允许这些扩展名；SVG/HTML/JS/压缩包等会形成存储型 XSS 或任意投递。
ALLOWED_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt",
    ".mp4", ".mov", ".mp3", ".m4a", ".wav",
})

# 只允许已知业务子目录，避免任意路径成为公开导出入口。
ALLOWED_KEY_PREFIXES = ("cert-attachments/",)

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

_MAGIC_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".webp": (b"RIFF",),
    ".bmp": (b"BM",),
    ".pdf": (b"%PDF-",),
}


class UploadService:
    """文件上传服务 — 本地文件存储"""

    @staticmethod
    async def save_file(file: UploadFile, sub_dir: str = "") -> dict:
        """保存上传文件；校验扩展名/文件头/大小并流式写盘。"""
        if sub_dir and not sub_dir.endswith("/"):
            sub_dir = f"{sub_dir}/"
        if sub_dir and not sub_dir.startswith(ALLOWED_KEY_PREFIXES):
            raise BusinessException("不支持的上传目录")

        _, ext = os.path.splitext(file.filename or "file")
        ext = ext.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise BusinessException("不支持的文件类型")

        target_dir = Path(UPLOAD_DIR) / sub_dir.rstrip("/") if sub_dir else Path(UPLOAD_DIR)
        await asyncio.to_thread(target_dir.mkdir, parents=True, exist_ok=True)

        file_id = f"{uuid.uuid4().hex}{ext}"
        storage_key = f"{sub_dir}{file_id}"
        file_path = target_dir / file_id

        size = 0
        header = b""
        try:
            with file_path.open("wb") as handle:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    if not header:
                        header = chunk[:32]
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise BusinessException("文件超过大小限制（20MB）")
                    await asyncio.to_thread(handle.write, chunk)
        except Exception:
            file_path.unlink(missing_ok=True)
            raise

        signatures = _MAGIC_SIGNATURES.get(ext)
        if signatures and not any(header.startswith(item) for item in signatures):
            file_path.unlink(missing_ok=True)
            raise BusinessException("文件内容与扩展名不匹配")

        return {
            "file_id": storage_key,
            "filename": file.filename or file_id,
            "url": f"/api/media/{storage_key}",
            "size": size,
        }

    @staticmethod
    def save_bytes(content: bytes, extension: str = "") -> dict:
        """保存内存中的字节到公开本地媒体目录"""

        normalized_ext = extension.lower()
        if normalized_ext and not normalized_ext.startswith("."):
            normalized_ext = f".{normalized_ext}"
        if normalized_ext and normalized_ext not in ALLOWED_EXTENSIONS:
            raise BusinessException("不支持的文件类型")
        if len(content) > MAX_UPLOAD_BYTES:
            raise BusinessException("文件超过大小限制（20MB）")
        file_id = f"{uuid.uuid4().hex}{normalized_ext}"

        Path(UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
        file_path = Path(UPLOAD_DIR) / file_id
        file_path.write_bytes(content)

        return {
            "file_id": file_id,
            "filename": file_id,
            "url": f"/api/media/{file_id}",
            "size": len(content),
        }

    @staticmethod
    def get_file_path(file_id: str) -> Path:
        """根据 file_id 返回完整文件路径"""
        if not UploadService._is_public_file_id(file_id):
            return Path(UPLOAD_DIR) / ".invalid-public-file"
        return Path(UPLOAD_DIR) / file_id

    @staticmethod
    def file_exists(file_id: str) -> bool:
        """检查文件是否存在"""
        return UploadService._is_public_file_id(file_id) and Path(UPLOAD_DIR, file_id).is_file()

    @staticmethod
    def delete_file_by_url(url: str) -> bool:
        """仅删除可安全解析为basename的公开媒体URL"""

        prefix = "/api/media/"
        if not url.startswith(prefix):
            return False
        return UploadService.delete_file_by_key(url.removeprefix(prefix))

    @staticmethod
    def delete_file_by_key(storage_key: str) -> bool:
        if not UploadService._is_public_file_id(storage_key):
            return False
        path = Path(UPLOAD_DIR) / storage_key
        if not path.is_file():
            return False
        try:
            path.unlink()
        except OSError:
            logger.warning(
                "public media delete failed: key=%s", storage_key, exc_info=True
            )
            return False
        return True

    @staticmethod
    def _is_public_file_id(file_id: str) -> bool:
        if not file_id or ".." in file_id or "\\" in file_id or file_id.startswith("/"):
            return False
        segments = file_id.split("/")
        if not all(_SAFE_SEGMENT.match(segment) for segment in segments):
            return False
        # 历史遗留的 .html/.js/.svg 等危险类型不再对外提供。
        if os.path.splitext(segments[-1])[1].lower() not in ALLOWED_EXTENSIONS:
            return False
        if len(segments) > 1:
            prefix = "/".join(segments[:-1]) + "/"
            return prefix.startswith(ALLOWED_KEY_PREFIXES)
        return True
