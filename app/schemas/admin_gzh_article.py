from datetime import datetime

from pydantic import BaseModel, Field
from pydantic import field_validator


def _validate_https_url(v: str | None, field_name: str) -> str | None:
    if v is not None and not v.startswith("https://"):
        raise ValueError(f"{field_name}必须以 https:// 开头")
    return v


def _validate_thumb_url(v: str | None) -> str | None:
    """封面允许 https 外链（微信图床）或本地上传的站内相对地址 /api/media/<file>。

    小程序端经 resolveMediaUrl 按-API-域名补全，Admin 端同源代理直接可渲染。
    """
    if v is not None and not (
        v.startswith("https://") or v.startswith("/api/media/")
    ):
        raise ValueError("封面图须为 https:// 外链或 /api/media/ 站内上传地址")
    return v


class GzhArticleCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=256, description="文章标题")
    url: str = Field(..., min_length=1, max_length=512, description="文章链接（微信推文 https 链接）")
    thumb_url: str | None = Field(None, max_length=512, description="封面图 URL")
    publish_time: datetime | None = Field(None, description="发布时间，ISO 8601")

    @field_validator("url")
    @classmethod
    def validate_url_https(cls, v: str) -> str:
        return _validate_https_url(v, "文章链接")

    @field_validator("thumb_url")
    @classmethod
    def validate_thumb_url(cls, v: str | None) -> str | None:
        return _validate_thumb_url(v)


class GzhArticleUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=256, description="文章标题")
    url: str | None = Field(None, min_length=1, max_length=512, description="文章链接")
    thumb_url: str | None = Field(None, max_length=512, description="封面图 URL")
    publish_time: datetime | None = Field(None, description="发布时间，ISO 8601")

    @field_validator("url")
    @classmethod
    def validate_url_https(cls, v: str | None) -> str | None:
        return _validate_https_url(v, "文章链接")

    @field_validator("thumb_url")
    @classmethod
    def validate_thumb_url(cls, v: str | None) -> str | None:
        return _validate_thumb_url(v)


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

    @field_validator("url")
    @classmethod
    def validate_url_https(cls, v: str) -> str:
        return _validate_https_url(v, "文章链接")

    @field_validator("thumb_url")
    @classmethod
    def validate_thumb_url(cls, v: str | None) -> str | None:
        return _validate_thumb_url(v)


class GzhArticleBatchImportRequest(BaseModel):
    items: list[GzhArticleImportItem] = Field(
        ..., min_length=1, max_length=200, description="待导入文章列表，单次最多 200 条"
    )


class GzhArticleBatchImportResult(BaseModel):
    created: int = Field(..., description="成功导入条数（含软删行复活）")
    skipped: int = Field(..., description="因链接已存在而跳过的条数")
    errors: list[GzhArticleImportError] = Field(default_factory=list, description="失败明细")


class GzhArticleFetchMetaRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=512, description="微信文章链接（https://mp.weixin.qq.com/...）")

    @field_validator("url")
    @classmethod
    def validate_url_https(cls, v: str) -> str:
        return _validate_https_url(v, "文章链接")


class GzhArticleFetchMetaResult(BaseModel):
    title: str = Field(..., description="文章标题")
    thumb_url: str | None = Field(None, description="封面图 URL")
    publish_time: datetime | None = Field(None, description="发布时间")
    exists: bool = Field(..., description="链接是否已存在于资讯库（true 时其余字段为库内条目值，未抓取）")
    source: str | None = Field(None, description="已存在条目的来源：sync/manual；不存在时为空")
