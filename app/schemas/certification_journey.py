from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

CertificationVendor = Literal["h3c", "nisp", "renshe", "sangfor"]


class NoticeBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    type: Literal["text", "image"]
    text: str = Field(default="", max_length=5000)
    url: str = Field(default="", max_length=512)
    caption: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def validate_content(self):
        if self.type == "text" and (not self.text or self.url):
            raise ValueError("文字段落不能为空，且不能包含图片地址")
        if self.type == "image":
            parsed = urlsplit(self.url)
            local = self.url.startswith("/api/media/") and not parsed.netloc
            remote = parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password
            if not (local or remote) or self.text:
                raise ValueError("图片须使用已上传的图片地址或HTTPS地址")
        return self


class JourneyContent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(default="报名须知", min_length=1, max_length=100)
    blocks: list[NoticeBlock] = Field(default_factory=list, max_length=50)
    contact_name: str = Field(default="", max_length=64)
    contact_phone: str = Field(default="", max_length=32)
    contact_wechat: str = Field(default="", max_length=64)
    service_hours: str = Field(default="", max_length=128)


class JourneyContentUpdate(JourneyContent):
    version: int = Field(ge=0)


class NoticeSubmission(BaseModel):
    notice_hash: str = Field(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")
