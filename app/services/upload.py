import os
import uuid
from pathlib import Path

from fastapi import UploadFile

from app.port.config import settings

UPLOAD_DIR = settings.UPLOAD_DIR


class UploadService:
    """文件上传服务 — 本地文件存储"""

    @staticmethod
    async def save_file(file: UploadFile, sub_dir: str = "") -> dict:
        """保存上传文件到本地目录，返回文件元信息"""
        # 确保目标目录存在
        target_dir = Path(UPLOAD_DIR) / sub_dir if sub_dir else Path(UPLOAD_DIR)
        target_dir.mkdir(parents=True, exist_ok=True)

        # 生成 UUID 文件名，保留原始扩展名
        _, ext = os.path.splitext(file.filename or "file")
        file_id = f"{uuid.uuid4().hex}{ext}"

        # 写入文件
        file_path = target_dir / file_id
        content = await file.read()
        file_path.write_bytes(content)

        return {
            "file_id": file_id,
            "filename": file.filename or file_id,
            "url": f"/api/media/{file_id}",
            "size": len(content),
        }

    @staticmethod
    def save_bytes(content: bytes, extension: str = "") -> dict:
        """保存内存中的字节到公开本地媒体目录"""

        normalized_ext = extension.lower()
        if normalized_ext and not normalized_ext.startswith("."):
            normalized_ext = f".{normalized_ext}"
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
        file_id = url.removeprefix(prefix)
        if not UploadService._is_public_file_id(file_id):
            return False
        path = Path(UPLOAD_DIR) / file_id
        if not path.is_file():
            return False
        path.unlink()
        return True

    @staticmethod
    def _is_public_file_id(file_id: str) -> bool:
        return bool(file_id) and Path(file_id).name == file_id and ".." not in file_id
