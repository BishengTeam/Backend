from pydantic import BaseModel, Field


class CertificationResponse(BaseModel):
    id: int
    name: str
    code: str
    vendor: str
    price_enterprise: int
    price_student: int
    requires_xuexin: bool
    pay_first: bool

    model_config = {"from_attributes": True}


class CertificationFilter(BaseModel):
    vendor: str | None = Field(None, description="按厂商筛选")
    is_active: bool | None = Field(None, description="按启用状态筛选")


class XuexinGuideResponse(BaseModel):
    title: str
    description: str
    url: str | None = None
    steps: list[str]
