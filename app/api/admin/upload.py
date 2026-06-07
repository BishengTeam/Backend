from fastapi import APIRouter, Depends, File, UploadFile

from app.middleware.auth import require_permission
from app.schemas.common import APIResponse, success
from app.schemas.upload import UploadResponse
from app.services.upload import UploadService

router = APIRouter(prefix="/upload", tags=["管理后台-文件上传"])


@router.post("",
    response_model=APIResponse[UploadResponse],
    summary="上传文件",
    description="""
管理后台 **各管理页面** 的 ImageUpload 组件使用。

**页面路径**: `/admin/*`

**使用场景**: 管理员在题库管理、商品管理等页面上传封面图或附件，返回文件访问 URL
    """,
)
async def admin_upload(
    file: UploadFile = File(...),
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[UploadResponse]:
    """管理员上传文件"""
    result = await UploadService.save_file(file)
    return success(data=UploadResponse(**result))
