from fastapi import APIRouter, Depends, File, UploadFile

from app.middleware.auth import require_permission
from app.schemas.common import APIResponse, success
from app.schemas.upload import UploadResponse
from app.services.upload import UploadService

router = APIRouter(prefix="/upload", tags=["管理后台-文件上传"])


@router.post("", response_model=APIResponse[UploadResponse])
async def admin_upload(
    file: UploadFile = File(...),
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[UploadResponse]:
    """管理员上传文件"""
    result = await UploadService.save_file(file)
    return success(data=UploadResponse(**result))
