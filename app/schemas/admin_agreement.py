from datetime import datetime

from pydantic import BaseModel, Field


class AdminAgreementCreate(BaseModel):
    type: str = Field(..., min_length=1, max_length=32)
    content: str | None = None
    user_id: int | None = None


class AdminAgreementReview(BaseModel):
    status: str = Field(..., min_length=1, max_length=16)
    signature_image: str | None = Field(None, max_length=512)


class AdminAgreementListItem(BaseModel):
    id: int
    user_id: int | None = None
    type: str
    content: str | None = None
    signature_image: str | None = None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}
