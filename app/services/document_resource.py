"""Services for managing and resolving generic operational PDF documents."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

from fastapi import UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.adapter.database import get_db_ctx
from app.domain.content.src.index import DocumentResource
from app.integrations.document_storage import (
    DocumentObjectStorage,
    validate_document_key,
)
from app.port.config import settings
from app.port.exceptions import (
    BusinessException,
    ConflictException,
    NotFoundException,
    ValidationException,
)
from app.schemas.common import PaginatedData
from app.schemas.document_resource import (
    AdminDocumentCreate,
    AdminDocumentItem,
    AdminDocumentUpdate,
    UserDocumentItem,
)


@dataclass(frozen=True)
class DocumentUploadMeta:
    filename: str
    content_type: str | None
    data: bytes


class DocumentResourceService:
    def __init__(self, storage: DocumentObjectStorage | None = None) -> None:
        self.storage = storage or DocumentObjectStorage()

    async def list_documents(
        self,
        *,
        keyword: str | None = None,
        document_key: str | None = None,
        is_active: bool | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedData[AdminDocumentItem]:
        async with get_db_ctx() as db:
            stmt = select(DocumentResource)
            if keyword:
                pattern = f"%{keyword.strip()}%"
                stmt = stmt.where(
                    or_(
                        DocumentResource.title.ilike(pattern),
                        DocumentResource.document_key.ilike(pattern),
                        DocumentResource.original_filename.ilike(pattern),
                    )
                )
            if document_key:
                stmt = stmt.where(DocumentResource.document_key == document_key.strip())
            if is_active is not None:
                stmt = stmt.where(DocumentResource.is_active == is_active)

            total = (
                await db.execute(select(func.count()).select_from(stmt.subquery()))
            ).scalar() or 0
            rows = (
                await db.scalars(
                    stmt.order_by(DocumentResource.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).all()
            return PaginatedData(
                items=[AdminDocumentItem.model_validate(row) for row in rows],
                total=total,
                page=page,
                page_size=page_size,
            )

    async def get_admin_document(self, document_id: int) -> AdminDocumentItem:
        async with get_db_ctx() as db:
            document = await self._get_document(db, document_id)
            item = AdminDocumentItem.model_validate(document)
            if document.is_active:
                item.download_url = await self._signed_url(document)
            return item

    async def create(
        self,
        data: AdminDocumentCreate,
        *,
        file: UploadFile,
        admin_id: int,
    ) -> AdminDocumentItem:
        file_meta = await self._read_upload(file)
        storage_key, size_bytes, sha256 = await self.storage.save(
            document_key=data.document_key,
            filename=file_meta.filename,
            content_type=file_meta.content_type,
            data=file_meta.data,
        )
        async with get_db_ctx() as db:
            document = DocumentResource(
                document_key=data.document_key,
                title=data.title,
                description=data.description,
                storage_key=storage_key,
                original_filename=file_meta.filename,
                content_type="application/pdf",
                size_bytes=size_bytes,
                sha256=sha256,
                version_no=1,
                is_active=data.is_active,
                created_by=admin_id,
                updated_by=admin_id,
            )
            db.add(document)
            try:
                await db.commit()
            except IntegrityError as exc:
                await db.rollback()
                raise ConflictException("文档键名已存在") from exc
            await db.refresh(document)
            item = AdminDocumentItem.model_validate(document)
            item.download_url = await self._signed_url(document)
            return item

    async def update(
        self, document_id: int, data: AdminDocumentUpdate, *, admin_id: int
    ) -> AdminDocumentItem:
        update_data = data.model_dump(exclude_unset=True)
        if not update_data:
            raise ValidationException("没有需要更新的内容")
        async with get_db_ctx() as db:
            document = await self._get_document(db, document_id)
            for field, value in update_data.items():
                setattr(document, field, value)
            document.updated_by = admin_id
            await db.commit()
            await db.refresh(document)
            item = AdminDocumentItem.model_validate(document)
            if document.is_active:
                item.download_url = await self._signed_url(document)
            return item

    async def replace_file(
        self,
        document_id: int,
        *,
        file: UploadFile,
        admin_id: int,
    ) -> AdminDocumentItem:
        file_meta = await self._read_upload(file)
        storage_key, size_bytes, sha256 = await self.storage.save(
            document_key=(await self._document_key(document_id)),
            filename=file_meta.filename,
            content_type=file_meta.content_type,
            data=file_meta.data,
        )
        async with get_db_ctx() as db:
            document = await self._get_document(db, document_id)
            document.storage_key = storage_key
            document.original_filename = file_meta.filename
            document.content_type = "application/pdf"
            document.size_bytes = size_bytes
            document.sha256 = sha256
            document.version_no += 1
            document.is_active = True
            document.updated_by = admin_id
            await db.commit()
            await db.refresh(document)
            item = AdminDocumentItem.model_validate(document)
            item.download_url = await self._signed_url(document)
            return item

    async def get_user_document(self, document_key: str) -> UserDocumentItem:
        normalized = validate_document_key(document_key)
        async with get_db_ctx() as db:
            document = await db.scalar(
                select(DocumentResource).where(
                    DocumentResource.document_key == normalized
                )
            )
            if document is None:
                raise BusinessException("教程文档暂未配置，请联系管理员")
            if not document.is_active:
                raise BusinessException("教程文档暂未配置，请联系管理员")
            url = await self._signed_url(document)
            expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=settings.ALIYUN_OSS_SIGNED_URL_TTL_SECONDS
            )
            return UserDocumentItem.from_document(document, url, expires_at)

    async def _document_key(self, document_id: int) -> str:
        async with get_db_ctx() as db:
            document = await self._get_document(db, document_id)
            return document.document_key

    @staticmethod
    async def _get_document(db, document_id: int) -> DocumentResource:
        document = await db.get(DocumentResource, document_id)
        if document is None:
            raise NotFoundException("运营文档")
        return document

    async def _signed_url(self, document: DocumentResource) -> str:
        return await self.storage.signed_get_url(
            document.storage_key, filename=document.original_filename
        )

    @staticmethod
    async def _read_upload(file: UploadFile) -> DocumentUploadMeta:
        filename = (file.filename or "").strip()
        if not filename or len(filename) > 256:
            raise ValidationException("PDF 文件名无效")
        data = await file.read()
        return DocumentUploadMeta(
            filename=filename,
            content_type=file.content_type,
            data=data,
        )
