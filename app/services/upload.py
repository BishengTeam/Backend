import os
import uuid
from pathlib import Path

from fastapi import UploadFile

from app.core.config import settings

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
    def get_file_path(file_id: str) -> Path:
        """根据 file_id 返回完整文件路径"""
        return Path(UPLOAD_DIR) / file_id

    @staticmethod
    def file_exists(file_id: str) -> bool:
        """检查文件是否存在"""
        return Path(UPLOAD_DIR, file_id).is_file()
