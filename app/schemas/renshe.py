import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


RensheApplicationStatus = Literal[
    "draft",
    "pending_payment",
    "pending_initial_review",
    "initial_rejected",
    "pending_external_review",
    "external_rejected",
    "external_approved",
    "closed",
]
RensheMaterialKind = Literal[
    "id_card_front",
    "id_card_back",
    "portrait",
    "student_card",
    "xuexin_registration",
    "education_proof",
]


class RensheVerificationMaterialResponse(BaseModel):
    kind: RensheMaterialKind
    storage_key: str
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str


class RensheDraftUpsert(BaseModel):
    plan_id: int = Field(..., ge=1, description="RS-ZY 批次 ID")
    contact_phone: str = Field(..., description="报名联系电话")
    mailing_address: str = Field(..., min_length=1, max_length=256, description="通讯地址")
    email: str = Field(..., min_length=3, max_length=128, description="电子邮箱")

    @field_validator("contact_phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"1[3-9]\d{9}", value):
            raise ValueError("手机号格式不正确")
        return value

    @field_validator("mailing_address", "email")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("字段不能为空")
        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("邮箱格式不正确")
        return value.lower()


class RensheVersionSummary(BaseModel):
    id: int
    version_no: int
    submitted_at: datetime

    model_config = {"from_attributes": True}


class RensheApplicationResponse(BaseModel):
    id: int
    plan_id: int
    user_id: int
    current_version_id: int | None = None
    status: RensheApplicationStatus
    draft_data: dict | None = None
    submitted_at: datetime | None = None
    frozen_at: datetime | None = None
    freeze_reason: str | None = None
    closed_at: datetime | None = None
    close_reason: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RensheApplicationSubmitResponse(BaseModel):
    application: RensheApplicationResponse
    version: RensheVersionSummary
    order_id: int
    order_status: str
    order_expires_at: datetime | None = None
    payment_required: bool


class RensheApplicationDetailResponse(RensheApplicationResponse):
    versions: list[RensheVersionSummary] = Field(default_factory=list)
    current_order_id: int | None = None
    current_order_status: str | None = None
    current_refund_id: int | None = None
    current_refund_status: str | None = None
    latest_rejection_stage: Literal["initial", "external"] | None = None
    latest_rejection_reason: str | None = None
    required_changes: list[str] | None = None


class RensheAdminApplicationListItem(BaseModel):
    id: int
    plan_id: int
    user_id: int
    current_version_id: int | None = None
    status: RensheApplicationStatus
    candidate_name: str | None = None
    id_card_masked: str | None = None
    contact_phone_masked: str | None = None
    payment_order_id: int | None = None
    payment_status: str | None = None
    submitted_at: datetime | None = None
    frozen_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class RensheReviewCreate(BaseModel):
    decision: Literal["approved", "rejected"]
    reason: str | None = Field(None, max_length=2000)
    required_changes: list[str] | None = Field(None, max_length=64)

    @model_validator(mode="after")
    def require_rejection_details(self) -> "RensheReviewCreate":
        if self.decision == "rejected":
            if not self.reason or not self.reason.strip():
                raise ValueError("驳回必须填写原因")
            if not self.required_changes:
                raise ValueError("驳回必须填写待修改项")
            self.reason = self.reason.strip()
        return self


class RensheReviewResponse(BaseModel):
    id: int
    application_id: int
    version_id: int
    stage: Literal["initial", "external"]
    decision: Literal["approved", "rejected"]
    reason: str | None = None
    required_changes: list[str] | None = None
    reviewer_id: int
    reviewed_at: datetime

    model_config = {"from_attributes": True}


class RensheReviewCorrectionCreate(BaseModel):
    to_decision: Literal["approved", "rejected"]
    reason: str = Field(..., min_length=1, max_length=2000)


class RensheReviewCorrectionResponse(BaseModel):
    id: int
    review_id: int
    application_id: int
    corrected_by_admin_id: int
    from_decision: str
    to_decision: str
    reason: str
    created_at: datetime

    model_config = {"from_attributes": True}


class RensheRefundCreate(BaseModel):
    request_kind: Literal["normal", "exception"] = "normal"
    reason_code: str = Field(..., min_length=1, max_length=64)
    reason_detail: str | None = Field(None, max_length=2000)

    @model_validator(mode="after")
    def require_exception_detail(self) -> "RensheRefundCreate":
        if self.request_kind == "exception" and (
            not self.reason_detail or not self.reason_detail.strip()
        ):
            raise ValueError("例外退款必须填写详细原因")
        return self


class RensheRefundDecision(BaseModel):
    decision: Literal["approved", "rejected"]
    reason: str | None = Field(None, max_length=2000)

    @model_validator(mode="after")
    def require_rejection_reason(self) -> "RensheRefundDecision":
        if self.decision == "rejected" and (not self.reason or not self.reason.strip()):
            raise ValueError("驳回退款必须填写原因")
        return self


class RensheRefundResponse(BaseModel):
    id: int
    application_id: int
    order_id: int
    user_id: int
    request_kind: str
    reason_code: str
    reason_detail: str | None = None
    amount_cents: int
    status: str
    requested_at: datetime
    due_at: datetime
    rejection_reason: str | None = None
    succeeded_at: datetime | None = None
    last_error: str | None = None

    model_config = {"from_attributes": True}


class RensheMaterialMetadataResponse(BaseModel):
    id: int
    application_id: int
    version_id: int
    kind: RensheMaterialKind
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str
    is_deleted: bool
    deleted_at: datetime | None = None

    model_config = {"from_attributes": True}


class RensheAdminVersionDetailResponse(BaseModel):
    id: int
    version_no: int
    submitted_at: datetime
    realname_snapshot: dict
    student_snapshot: dict
    form_data: dict
    sensitive_cleared_at: datetime | None = None
    materials: list[RensheMaterialMetadataResponse] = Field(default_factory=list)
    reviews: list[RensheReviewResponse] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class RensheAdminApplicationDetailResponse(RensheApplicationResponse):
    versions: list[RensheAdminVersionDetailResponse] = Field(default_factory=list)
    current_order_id: int | None = None
    current_order_status: str | None = None


class RensheSignedUrlResponse(BaseModel):
    url: str
    expires_in: int = Field(..., ge=1, le=300)


class RensheExportVolumeResponse(BaseModel):
    id: int
    job_id: int
    volume_no: int
    status: Literal["queued", "running", "succeeded", "failed"]
    candidate_count: int
    size_bytes: int | None = None
    sha256: str | None = None
    finished_at: datetime | None = None
    last_error: str | None = None
    download_available: bool = False

    model_config = {"from_attributes": True}


class RensheExportJobResponse(BaseModel):
    id: int
    plan_id: int
    generation_no: int
    requested_by_admin_id: int
    status: Literal["queued", "running", "succeeded", "failed"]
    candidate_total: int
    candidate_processed: int
    volume_count: int
    heartbeat_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    retry_count: int
    last_error: str | None = None
    volumes: list[RensheExportVolumeResponse] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class RensheCleanupRunResponse(BaseModel):
    id: int
    plan_id: int
    run_no: int
    status: Literal["scheduled", "running", "paused", "succeeded", "failed"]
    due_at: datetime
    paused_reason: str | None = None
    heartbeat_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    retry_count: int
    rebase_count: int
    last_error: str | None = None

    model_config = {"from_attributes": True}
