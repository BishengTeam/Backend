"""Admin schemas for agreement template management."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.utils.html_sanitize import sanitize_html


def _sanitize_content(value: str) -> str:
    cleaned = sanitize_html(value)
    if not cleaned.strip():
        raise ValueError("协议正文不能为空")
    return cleaned

AdminAgreementTypeLiteral = Literal[
    "user_terms", "privacy", "identity_auth", "cert_registration"
]


class AdminAgreementTemplateCreate(BaseModel):
    type: AdminAgreementTypeLiteral
    title: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1)

    @field_validator("content")
    @classmethod
    def _sanitize(cls, value: str) -> str:
        return _sanitize_content(value)


class AdminAgreementTemplateUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1)

    @field_validator("content")
    @classmethod
    def _sanitize(cls, value: str) -> str:
        return _sanitize_content(value)


class AdminAgreementTemplateItem(BaseModel):
    id: int
    type: str
    title: str
    content: str
    version: int
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("content")
    @classmethod
    def _sanitize(cls, value: str) -> str:
        return sanitize_html(value)
