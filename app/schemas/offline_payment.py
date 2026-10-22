from datetime import datetime, timedelta, timezone
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

RequiredText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
Money = Annotated[int, Field(strict=True, gt=0, le=2147483647)]


class SettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_name: RequiredText
    bank_name: RequiredText
    account_number: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9]{8,40}$")]
    transfer_note: str = Field(default="转账请备注订单号", max_length=500)
    contact_name: RequiredText
    contact_phone: str = Field(default="", max_length=32)
    contact_wechat: str = Field(default="", max_length=64)
    service_hours: str = Field(default="", max_length=128)
    submit_hours: int = Field(default=24, ge=1, le=168)
    correction_hours: int = Field(default=48, ge=1, le=336)
    version: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def contact(self):
        if not self.contact_wechat.strip() and not self.contact_phone.strip():
            raise ValueError("至少配置电话或微信联系方式")
        return self


class SubmissionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payer: RequiredText
    transfer_time: AwareDatetime
    amount_cents: Money
    bank_reference: str = Field(default="", max_length=128)
    note: str = Field(default="", max_length=1000)
    material_ids: list[int] = Field(min_length=1, max_length=3)
    expected_version: int = Field(ge=0)
    request_key: RequiredText

    @field_validator("transfer_time")
    @classmethod
    def not_future(cls, value):
        if value > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError("转账时间不能晚于当前时间")
        return value

    @field_validator("material_ids")
    @classmethod
    def unique_materials(cls, value):
        if len(set(value)) != len(value) or any(v <= 0 for v in value):
            raise ValueError("凭证附件无效或重复")
        return value


class DecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    request_key: RequiredText
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    deadline: AwareDatetime | None = None


class ConfirmInput(DecisionInput):
    amount_cents: Money
    bank_time: AwareDatetime
    bank_reference: RequiredText
    _not_future = field_validator("bank_time")(SubmissionInput.not_future.__func__)


class RefundInput(ConfirmInput):
    material_ids: list[int] = Field(min_length=1, max_length=3)
    refund_kind: Annotated[str, StringConstraints(pattern=r"^(h3c|nisp|renshe|order)$")]
    refund_id: int = Field(gt=0)


class ExceptionRefundInput(ConfirmInput):
    material_ids: list[int] = Field(min_length=1, max_length=3)
    refund_bank_time: AwareDatetime
    refund_bank_reference: RequiredText
    _refund_not_future = field_validator("refund_bank_time")(SubmissionInput.not_future.__func__)
