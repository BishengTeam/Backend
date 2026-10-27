import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.schemas.plan import PlanCreate


class SangforBatchCreate(PlanCreate):
    product_code: str = Field(min_length=1, max_length=32)


class SangforSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    phone: str = Field(pattern=r"^1[3-9]\d{9}$")
    organization: str = Field(min_length=1, max_length=128)
    first_name: str = Field(min_length=1, max_length=64)
    last_name: str = Field(min_length=1, max_length=64)
    exam_voucher_code: str = Field(min_length=1, max_length=128)
    email: str = Field(default="", max_length=128)
    mailing_address: str = Field(default="", max_length=256)
    remark1: str = Field(default="", max_length=500)
    remark2: str = Field(default="", max_length=500)

    @field_validator("first_name", "last_name")
    @classmethod
    def pinyin(cls, value):
        if not re.fullmatch(r"[A-Za-z]+(?:[ '\-][A-Za-z]+)*", value):
            raise ValueError("请填写英文或拼音，可使用空格、短横线或英文单引号")
        return value

    @field_validator("email")
    @classmethod
    def email_format(cls, value):
        if value and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("邮箱格式不正确")
        return value


class SangforCreate(SangforSubmission):
    batch_id: int = Field(gt=0)


class SangforResubmit(SangforSubmission):
    expected_revision: int = Field(gt=0)


class SangforDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_revision: int = Field(gt=0)
    action: Literal["approve", "return", "final_approve", "final_return"]
    reason: str = Field(default="", max_length=1000)
    correction_due_at: datetime | None = None


class SangforExport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    registration_ids: list[int] = Field(min_length=1, max_length=500)
    expected_versions: dict[int, int] | None = None

    @field_validator("registration_ids")
    @classmethod
    def unique_ids(cls, value):
        if any(item <= 0 for item in value) or len(set(value)) != len(value):
            raise ValueError("请选择有效且不重复的报名记录")
        return value


class SangforRefundResponse(BaseModel):
    """A read-only projection of an order's bank refund, not a new request."""
    id: int
    registration_id: int
    order_id: int
    batch_id: int
    batch_name: str
    product_label: str
    payment_channel: Literal["bank_transfer"]
    out_trade_no: str
    registration_no: str
    candidate_name: str | None
    request_kind: Literal["normal", "exception"]
    status: Literal["approved", "exception", "succeeded"]
    amount_cents: int | None
    amount_source: Literal["ledger", "verified", "submission", "order", "unknown"]
    reason_detail: str | None
    requested_at: datetime
    approved_at: datetime | None
    succeeded_at: datetime | None
    last_error: str | None
