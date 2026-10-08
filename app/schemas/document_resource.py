"""Contracts for generic admin-managed PDF documents."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.integrations.document_storage import validate_document_key


class AdminDocumentCreate(BaseModel):
    document_key: str = Field(max_length=128)
    title: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    is_active: bool = True

    @field_validator("document_key")
    @classmethod
    def normalize_key(cls, value: str) -> str:
        return validate_document_key(value)

    @field_validator("title", "description")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("title")
    @classmethod
    def require_title(cls, value: str | None) -> str:
        if not value:
            raise ValueError("标题不能为空")
        return value


class AdminDocumentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    is_active: bool | None = None

    @field_validator("title", "description")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class AdminDocumentItem(BaseModel):
    id: int
    document_key: str
    title: str
    description: str | None
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str
    version_no: int
    is_active: bool
    created_by: int | None
    updated_by: int | None
    created_at: datetime
    updated_at: datetime
    download_url: str | None = None

    model_config = {"from_attributes": True}


class UserDocumentItem(BaseModel):
    document_key: str
    title: str
    description: str | None
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str
    version_no: int
    download_url: str
    expires_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_document(
        cls, document: Any, download_url: str, expires_at: datetime
    ) -> "UserDocumentItem":
        return cls(
            document_key=document.document_key,
            title=document.title,
            description=document.description,
            original_filename=document.original_filename,
            content_type=document.content_type,
            size_bytes=document.size_bytes,
            sha256=document.sha256,
            version_no=document.version_no,
            download_url=download_url,
            expires_at=expires_at,
        )
