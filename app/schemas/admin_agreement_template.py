"""Admin schemas for agreement template management."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

AdminAgreementTypeLiteral = Literal["user_terms", "privacy", "identity_auth"]


class AdminAgreementTemplateCreate(BaseModel):
    type: AdminAgreementTypeLiteral
    title: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1)


class AdminAgreementTemplateUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1)


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
