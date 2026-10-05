"""NISP certification schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

NispLevel = Literal["1", "2"]
NispRegistrationStatus = Literal[
    "pending_payment",
    "pending_review",
    "rejected_awaiting_resubmission",
    "pending_refund_confirmation",
    "refund_processing",
    "approved",
    "refunded_closed",
    "cancelled",
]
NispReviewDecision = Literal["approved", "rejected"]


# ── Admin: batch management ──

class NispExamBatchBase(BaseModel):
    level: NispLevel = "1"
    training_org: str | None = Field(None, max_length=128)
    training_teacher: str | None = Field(None, max_length=64)
    training_address: str | None = Field(None, max_length=256)
    training_start: datetime | None = None
    training_end: datetime | None = None
    level1_price_cents: int = Field(ge=0)
    level2_price_cents: int = Field(ge=0)
    payment_timeout_minutes: int = Field(default=30, ge=1, le=1440)
    resubmission_window_hours: int = Field(default=72, ge=1, le=720)
    max_resubmissions: int = Field(default=2, ge=0, le=10)
    max_material_bytes: int = Field(default=10485760, ge=1, le=20971520)


class NispExamBatchCreate(NispExamBatchBase):
    plan_id: int


class NispExamBatchUpdate(BaseModel):
    training_org: str | None = Field(None, max_length=128)
    training_teacher: str | None = Field(None, max_length=64)
    training_address: str | None = Field(None, max_length=256)
    training_start: datetime | None = None
    training_end: datetime | None = None
    level1_price_cents: int | None = Field(None, ge=0)
    level2_price_cents: int | None = Field(None, ge=0)


class NispExamBatchResponse(BaseModel):
    id: int
    plan_id: int
    level: NispLevel
    plan_name: str
    plan_status: str
    apply_start: datetime | None
    apply_end: datetime | None
    exam_date: datetime | None
    exam_location: str | None
    capacity: int
    occupied_count: int
    training_org: str | None
    training_teacher: str | None
    training_address: str | None
    training_start: datetime | None
    training_end: datetime | None
    level1_price_cents: int
    level2_price_cents: int
    payment_timeout_minutes: int
    resubmission_window_hours: int
    max_resubmissions: int
    max_material_bytes: int
    created_at: datetime
    updated_at: datetime


# ── User: batch listing ──

class NispBatchListItem(BaseModel):
    id: int
    level: NispLevel
    name: str
    status: str
    apply_start: datetime | None
    apply_end: datetime | None
    exam_date: datetime | None
    exam_location: str | None
    remaining_count: int
    level1_price_cents: int
    level2_price_cents: int
    payment_timeout_minutes: int
    max_material_bytes: int


# ── User: order creation ──

class NispOrderCreate(BaseModel):
    batch_id: int
    level: NispLevel

    # Common fields (all levels)
    name: str = Field(min_length=1, max_length=64)
    pinyin: str = Field(min_length=1, max_length=128)
    major: str = Field(min_length=1, max_length=64)
    school: str = Field(min_length=1, max_length=128)
    id_card: str = Field(min_length=18, max_length=18)
    phone: str = Field(min_length=11, max_length=11)
    email: str = Field(min_length=3, max_length=128)
    province: str = Field(min_length=1, max_length=32)

    # Level 2 extra fields
    gender: str | None = Field(None, max_length=2)
    age: int | None = Field(None, ge=16, le=80)
    education: str | None = Field(None, max_length=32)
    address: str | None = Field(None, max_length=256)
    zip_code: str | None = Field(None, max_length=6)

    # Material keys (uploaded to OSS beforehand)
    id_card_both_sides_key: str = Field(min_length=1, max_length=512)
    portrait_photo_key: str = Field(min_length=1, max_length=512)
    xuexin_report_key: str | None = Field(None, min_length=1, max_length=512)
    application_form_key: str | None = Field(None, min_length=1, max_length=512)


# ── Registration response ──

class NispRegistrationResponse(BaseModel):
    id: int
    registration_no: str
    batch_id: int
    plan_id: int
    order_id: int
    level: NispLevel
    status: NispRegistrationStatus
    candidate_snapshot: dict
    order_status: str
    price_cents: int
    out_trade_no: str | None
    paid_at: datetime | None
    resubmission_count: int
    rejection_count: int
    resubmission_due_at: datetime | None
    last_reviewed_at: datetime | None
    approved_at: datetime | None
    latest_review: "NispReviewResponse | None" = None
    created_at: datetime
    updated_at: datetime


class NispReviewResponse(BaseModel):
    decision: NispReviewDecision
    reason_code: str | None
    reason_detail: str | None
    rejected_material_types: list[str] | None
    reviewer_admin_id: int
    reviewed_at: datetime

    model_config = {"from_attributes": True}


# ── Admin: review ──

class NispReviewDecisionRequest(BaseModel):
    decision: NispReviewDecision
    reason_code: str | None = Field(None, max_length=64)
    reason_detail: str | None = Field(None, max_length=2000)
    rejected_material_types: list[str] | None = None


# ── User: resubmit materials ──

class NispResubmitRequest(BaseModel):
    id_card_both_sides_key: str | None = Field(None, min_length=1, max_length=512)
    portrait_photo_key: str | None = Field(None, min_length=1, max_length=512)
    xuexin_report_key: str | None = Field(None, min_length=1, max_length=512)
    application_form_key: str | None = Field(None, min_length=1, max_length=512)


# ── Export ──

class NispExportCreate(BaseModel):
    batch_id: int
    level: NispLevel
    include_statuses: list[NispRegistrationStatus] = Field(
        default=["approved"], min_length=1
    )


class NispExportJobResponse(BaseModel):
    id: int
    batch_id: int
    level: NispLevel
    include_statuses: list[str]
    status: str
    registration_count: int
    started_at: datetime | None
    finished_at: datetime | None
    storage_key: str | None
    artifact_sha256: str | None
    artifact_bytes: int | None
    expires_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class NispSignedUrlResponse(BaseModel):
    url: str
    expires_at: datetime


class NispRefundResponse(BaseModel):
    id: int
    registration_id: int
    order_id: int
    request_kind: str
    reason_code: str
    reason_detail: str | None
    amount_cents: int
    status: str
    requested_by_admin_id: int | None
    requested_at: datetime
    approved_by_admin_id: int | None
    approved_at: datetime | None
    out_refund_no: str | None
    processing_at: datetime | None
    succeeded_at: datetime | None
    last_error: str | None
    retry_count: int

    model_config = {"from_attributes": True}


class NispRefundConfirmRequest(BaseModel):
    refund_id: int
