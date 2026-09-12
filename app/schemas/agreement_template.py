"""User-facing schemas for P0 agreement templates and acceptances."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

AgreementTypeLiteral = Literal[
    "user_terms", "privacy", "identity_auth", "cert_registration"
]


class AgreementTemplatePublic(BaseModel):
    """Current active template shown to users (login page needs it pre-auth)."""

    type: AgreementTypeLiteral
    title: str
    content: str
    version: int

    model_config = {"from_attributes": True}


class AgreementAcceptItem(BaseModel):
    type: AgreementTypeLiteral
    version: int = Field(ge=1, description="签署时基于的模板版本")


class AgreementAcceptRequest(BaseModel):
    items: list[AgreementAcceptItem] = Field(min_length=1, max_length=8)


class AgreementAcceptanceItem(BaseModel):
    """Signed record shown in 我的协议."""

    id: int
    type: AgreementTypeLiteral
    title: str
    version: int
    accepted_at: datetime

    model_config = {"from_attributes": True}
