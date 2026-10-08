"""Contracts for generic admin-managed PDF documents."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.integrations.document_storage import validate_document_key


DocumentScene = Literal["h3c_student_xuexin_guide"]
DocumentEntryMode = Literal["required", "optional"]

DOCUMENT_SCENE_CONFIG: dict[str, dict[str, str]] = {
    "h3c_student_xuexin_guide": {
        "default_entry_text": "查看《如何查询学籍在线验证码》PDF",
        "entry_mode": "required",
        "location": "H3C报名表单 / 学生材料",
    },
}


class AdminDocumentCreate(BaseModel):
    document_key: str = Field(max_length=128)
    scene: DocumentScene | None = None
    title: str = Field(min_length=1, max_length=128)
    entry_text: str | None = Field(default=None, max_length=64)
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

    @model_validator(mode="after")
    def apply_scene_defaults(self) -> "AdminDocumentCreate":
        if self.scene is not None and not self.entry_text:
            self.entry_text = DOCUMENT_SCENE_CONFIG[self.scene]["default_entry_text"]
        return self


class AdminDocumentUpdate(BaseModel):
    scene: DocumentScene | None = None
    title: str | None = Field(default=None, min_length=1, max_length=128)
    entry_text: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=512)
    is_active: bool | None = None

    @field_validator("title", "description", "entry_text")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def apply_scene_defaults(self) -> "AdminDocumentUpdate":
        if self.scene is not None and self.entry_text is None:
            self.entry_text = DOCUMENT_SCENE_CONFIG[self.scene]["default_entry_text"]
        return self


class AdminDocumentItem(BaseModel):
    id: int
    document_key: str
    scene: str | None
    title: str
    entry_text: str | None
    entry_mode: str | None
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


class UserDocumentSummary(BaseModel):
    document_key: str
    title: str
    version_no: int
    size_bytes: int


class UserDocumentSceneItem(BaseModel):
    scene: DocumentScene
    entry_text: str
    entry_mode: DocumentEntryMode
    document: UserDocumentSummary | None
