from pydantic import BaseModel, Field


class AdminPriceCreate(BaseModel):
    product_type: str = Field(..., min_length=1, max_length=64)
    user_type: str = Field(..., min_length=1, max_length=16)
    price: int = Field(..., ge=0)


class AdminPriceUpdate(BaseModel):
    product_type: str | None = Field(None, min_length=1, max_length=64)
    user_type: str | None = Field(None, min_length=1, max_length=16)
    price: int | None = Field(None, ge=0)
    is_active: bool | None = None
