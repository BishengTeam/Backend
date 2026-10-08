from fastapi import APIRouter, Depends, File, Form, Path, Query, UploadFile

from app.middleware.auth import require_permission
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.document_resource import (
    AdminDocumentCreate,
    AdminDocumentItem,
    AdminDocumentUpdate,
)
from app.services.document_resource import DocumentResourceService


router = APIRouter(prefix="/documents", tags=["管理后台-文档管理"])


@router.get("", response_model=APIResponse[PaginatedData[AdminDocumentItem]])
async def list_documents(
    keyword: str | None = Query(None, max_length=128),
    document_key: str | None = Query(None, max_length=128),
    is_active: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin=Depends(require_permission("document:read")),
):
    return success(
        data=await DocumentResourceService().list_documents(
            keyword=keyword,
            document_key=document_key,
            is_active=is_active,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/{document_id}", response_model=APIResponse[AdminDocumentItem])
async def get_document(
    document_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("document:read")),
):
    return success(data=await DocumentResourceService().get_admin_document(document_id))


@router.post("", response_model=APIResponse[AdminDocumentItem])
async def create_document(
    document_key: str = Form(..., max_length=128),
    title: str = Form(..., min_length=1, max_length=128),
    description: str | None = Form(None, max_length=512),
    is_active: bool = Form(True),
    file: UploadFile = File(...),
    admin=Depends(require_permission("document:write")),
):
    body = AdminDocumentCreate(
        document_key=document_key,
        title=title,
        description=description,
        is_active=is_active,
    )
    item = await DocumentResourceService().create(
        body, file=file, admin_id=admin.id
    )
    return success(data=item, message="创建成功")


@router.put("/{document_id}", response_model=APIResponse[AdminDocumentItem])
async def update_document(
    document_id: int = Path(..., ge=1),
    body: AdminDocumentUpdate = ...,
    admin=Depends(require_permission("document:write")),
):
    item = await DocumentResourceService().update(
        document_id, body, admin_id=admin.id
    )
    return success(data=item, message="保存成功")


@router.post("/{document_id}/file", response_model=APIResponse[AdminDocumentItem])
async def replace_document_file(
    document_id: int = Path(..., ge=1),
    file: UploadFile = File(...),
    admin=Depends(require_permission("document:write")),
):
    item = await DocumentResourceService().replace_file(
        document_id, file=file, admin_id=admin.id
    )
    return success(data=item, message="新文件已生效")
