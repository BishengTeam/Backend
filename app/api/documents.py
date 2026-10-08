from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import get_current_user
from app.schemas.common import APIResponse, success
from app.schemas.document_resource import UserDocumentItem, UserDocumentSceneItem
from app.services.document_resource import DocumentResourceService


router = APIRouter(prefix="/documents", tags=["运营文档"])


@router.get(
    "/scenes/{scene}",
    response_model=APIResponse[UserDocumentSceneItem],
    summary="获取小程序固定文档入口配置",
)
async def get_document_scene(
    scene: str = Path(..., max_length=64),
    _user=Depends(get_current_user),
) -> APIResponse[UserDocumentSceneItem]:
    return success(data=await DocumentResourceService().get_user_scene(scene))


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
