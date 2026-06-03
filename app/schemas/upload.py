from pydantic import BaseModel, Field


class UploadResponse(BaseModel):
    """文件上传响应"""

    file_id: str = Field(..., description="文件唯一标识 (UUID)")
    filename: str = Field(..., description="原始文件名")
    url: str = Field(..., description="文件访问 URL")
    size: int = Field(..., description="文件大小 (bytes)")
