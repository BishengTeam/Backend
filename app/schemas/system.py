from pydantic import BaseModel, Field


class PosterResponse(BaseModel):
    url: str | None = Field(None, description="登录页海报图片 URL")
