from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ConsultationGroupUserResponse(BaseModel):
    qrcode_url: str
    join_instructions: str | None = None
    expires_at: datetime | None = None
    contact_name: str | None = None
    contact_phone: str | None = None


class ConsultationGroupAdminResponse(BaseModel):
    plan_id: int
    qrcode_url: str
    join_instructions: str | None = None
    expires_at: datetime | None = None
    version: int
    status: Literal["configured", "expired"]
    updated_by: int | None = None
    updated_at: datetime


class ConsultationGroupEmptyResponse(BaseModel):
    plan_id: int
    status: Literal["not_configured"] = "not_configured"
    version: int = 0


class ConsultationGroupUpdate(BaseModel):
    qrcode_url: str | None = Field(None, max_length=512)
    join_instructions: str | None = Field(None, max_length=1000)
    expires_at: datetime | None = None
    version: int = Field(..., ge=0)

    @field_validator("qrcode_url")
    @classmethod
    def normalize_qrcode_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("join_instructions")
    @classmethod
    def normalize_instructions(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None
