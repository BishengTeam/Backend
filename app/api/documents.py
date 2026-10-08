from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import get_current_user
from app.schemas.common import APIResponse, success
from app.schemas.document_resource import UserDocumentItem
from app.services.document_resource import DocumentResourceService


router = APIRouter(prefix="/documents", tags=["运营文档"])


@router.get(
    "/{document_key}",
    response_model=APIResponse[UserDocumentItem],
    summary="获取运营 PDF 文档下载信息",
)
async def get_document(
    document_key: str = Path(..., max_length=128),
    _user=Depends(get_current_user),
) -> APIResponse[UserDocumentItem]:
    return success(
        data=await DocumentResourceService().get_user_document(document_key)
    )
