from datetime import datetime

from pydantic import BaseModel, Field


class GzhArticleCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=256, description="文章标题")
    url: str = Field(..., min_length=1, max_length=512, description="文章链接（微信推文 https 链接）")
    thumb_url: str | None = Field(None, max_length=512, description="封面图 URL")
    publish_time: datetime | None = Field(None, description="发布时间，ISO 8601")


class GzhArticleUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=256, description="文章标题")
    url: str | None = Field(None, min_length=1, max_length=512, description="文章链接")
    thumb_url: str | None = Field(None, max_length=512, description="封面图 URL")
    publish_time: datetime | None = Field(None, description="发布时间，ISO 8601")


class GzhArticleListItem(BaseModel):
    id: int = Field(..., description="文章 ID")
    title: str = Field(..., description="文章标题")
    url: str = Field(..., description="文章链接")
    thumb_url: str | None = Field(None, description="封面图 URL")
    publish_time: datetime | None = Field(None, description="发布时间")
    source: str = Field(..., description="来源：sync=同步 worker 维护 / manual=Admin 手动维护")
    created_at: datetime = Field(..., description="创建时间")
    updated_at: datetime = Field(..., description="更新时间")

    model_config = {"from_attributes": True}


class GzhArticleImportError(BaseModel):
    index: int = Field(..., description="items 中的下标（从 0 起）")
    title: str = Field(..., description="该条标题")
    message: str = Field(..., description="失败原因")


class GzhArticleImportItem(BaseModel):
    title: str = Field(..., min_length=1, max_length=256, description="文章标题")
    url: str = Field(..., min_length=1, max_length=512, description="文章链接")
    thumb_url: str | None = Field(None, max_length=512, description="封面图 URL")
    publish_time: datetime | None = Field(None, description="发布时间，ISO 8601")


class GzhArticleBatchImportRequest(BaseModel):
    items: list[GzhArticleImportItem] = Field(
        ..., min_length=1, max_length=200, description="待导入文章列表，单次最多 200 条"
    )


class GzhArticleBatchImportResult(BaseModel):
    created: int = Field(..., description="成功导入条数（含软删行复活）")
    skipped: int = Field(..., description="因链接已存在而跳过的条数")
    errors: list[GzhArticleImportError] = Field(default_factory=list, description="失败明细")
