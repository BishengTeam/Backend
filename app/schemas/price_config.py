from pydantic import BaseModel, Field


class PriceResponse(BaseModel):
    cert_type: str
    user_type: str
    price: int

    model_config = {"from_attributes": True}


class PriceFilter(BaseModel):
    cert_type: str | None = Field(None, description="按认证类型筛选")
    user_type: str | None = Field(None, description="按用户类型筛选")
