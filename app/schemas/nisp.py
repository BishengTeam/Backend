from app.schemas.registration_payment import RegistrationPaymentSummary
"""NISP certification schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.consultation_group import ConsultationGroupUserResponse

NispLevel = Literal["1", "2"]
NispRegistrationStatus = Literal[
    "pending_payment",
    "pending_review",
    "rejected_awaiting_resubmission",
    "pending_refund_confirmation",
    "refund_processing",
    "approved",
    "final_approved",
    "refunded_closed",
    "cancelled",
]
NispReviewDecision = Literal["approved", "rejected", "rejected_refund"]
NispCorrectionFieldType = Literal[
    "pinyin", "phone", "email", "school", "major", "province",
    "gender", "age", "education", "address", "zip_code",
]


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
    consultation_group_qrcode_url: str | None = Field(None, max_length=512)


class NispExamBatchCreate(NispExamBatchBase):
    plan_id: int


class NispExamBatchFullCreate(BaseModel):
    """Create a level-specific NISP batch and its Plan in one request."""

    level: NispLevel
    name: str = Field(min_length=1, max_length=128)
    training_org: str | None = Field(None, max_length=128)
    apply_start: datetime
    apply_end: datetime
    exam_date: datetime | None = None
    exam_location: str | None = Field(None, max_length=256)
    capacity: int = Field(ge=1)
    price_cents: int = Field(ge=0)
    payment_timeout_minutes: int = Field(default=30, ge=1, le=1440)
    resubmission_window_hours: int = Field(default=72, ge=1, le=720)
    max_resubmissions: int = Field(default=2, ge=0, le=10)
    consultation_group_qrcode_url: str | None = Field(None, max_length=512)
    publish: bool = False

    @field_validator("name")
    @classmethod
    def _trim_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("批次名称不能为空")
        return cleaned

    @model_validator(mode="after")
    def validate_schedule(self):
        if self.apply_start >= self.apply_end:
            raise ValueError("报名开始时间必须早于截止时间")
        if self.exam_date is not None and self.exam_date < self.apply_end:
            raise ValueError("考试时间不能早于报名截止时间")
        if self.publish and self.exam_date is None:
            raise ValueError("保存并发布前必须填写考试时间")
        return self


class NispExamBatchUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    apply_start: datetime | None = None
    apply_end: datetime | None = None
    exam_date: datetime | None = None
    exam_location: str | None = Field(None, max_length=256)
    capacity: int | None = Field(None, ge=1)
    training_org: str | None = Field(None, max_length=128)
    training_teacher: str | None = Field(None, max_length=64)
    training_address: str | None = Field(None, max_length=256)
    training_start: datetime | None = None
    training_end: datetime | None = None
    price_cents: int | None = Field(None, ge=0)
    level1_price_cents: int | None = Field(None, ge=0)
    level2_price_cents: int | None = Field(None, ge=0)
    payment_timeout_minutes: int | None = Field(None, ge=1, le=1440)
    resubmission_window_hours: int | None = Field(None, ge=1, le=720)
    max_resubmissions: int | None = Field(None, ge=0, le=10)
    consultation_group_qrcode_url: str | None = Field(None, max_length=512)

    @field_validator("name")
    @classmethod
    def _trim_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("批次名称不能为空")
        return cleaned


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
    consultation_group_qrcode_url: str | None
    level1_price_cents: int
    level2_price_cents: int
    payment_timeout_minutes: int
    resubmission_window_hours: int
    max_resubmissions: int
    max_material_bytes: int
    registration_counts: dict[str, int] = {}
    created_at: datetime
    updated_at: datetime


class NispReviewBatchOption(BaseModel):
    id: int
    name: str
    level: NispLevel
    status: str


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
    payment_summary: RegistrationPaymentSummary | None = None
    id: int
    registration_no: str
    batch_id: int
    batch_name: str | None = None
    plan_id: int
    order_id: int
    level: NispLevel
    status: NispRegistrationStatus
    consultation_group_qrcode_url: str | None = None
    consultation_group: ConsultationGroupUserResponse | None = None
    active_refund: "NispRegistrationRefundSummary | None" = None
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
    materials: list["NispMaterialResponse"] = []
    pending_correction: "NispCorrectionRequestResponse | None" = None
    versions: list["NispRegistrationVersionResponse"] = []
    final_export_item_id: int | None = None
    latest_valid_export_job_id: int | None = None
    latest_valid_export_finished_at: datetime | None = None
    final_review_ready: bool = False
    final_review_block_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class NispRegistrationRefundSummary(BaseModel):
    id: int
    status: str
    out_refund_no: str | None
    processing_at: datetime | None
    succeeded_at: datetime | None
    last_error: str | None
    retry_count: int

    model_config = {"from_attributes": True}


class NispReviewResponse(BaseModel):
    decision: NispReviewDecision
    reason_code: str | None
    reason_detail: str | None
    rejected_material_types: list[str] | None
    reviewer_admin_id: int
    reviewed_at: datetime

    model_config = {"from_attributes": True}


class NispMaterialUploadResponse(BaseModel):
    """NISP 材料上传结果（统一 APIResponse 信封内）"""

    material_type: str
    material_id: int
    storage_key: str
    original_filename: str | None
    content_type: str | None
    size_bytes: int
    sha256: str


class NispMaterialResponse(BaseModel):
    id: int
    material_type: str
    version_no: int | None
    storage_key: str
    original_filename: str | None
    content_type: str | None
    size_bytes: int | None
    sha256: str | None
    is_current: bool
    preview_url: str | None = None
    uploaded_at: datetime | None = None

    model_config = {"from_attributes": True}


# ── Admin: review ──

class NispReviewDecisionRequest(BaseModel):
    decision: NispReviewDecision
    reason_code: str | None = Field(None, max_length=64)
    reason_detail: str | None = Field(None, max_length=2000)
    rejected_material_types: list[str] | None = None
    allowed_fields: list[NispCorrectionFieldType] = []


# ── User: resubmit materials ──

class NispResubmitRequest(BaseModel):
    pinyin: str | None = Field(None, min_length=1, max_length=128)
    phone: str | None = Field(None, min_length=11, max_length=11)
    email: str | None = Field(None, min_length=3, max_length=128)
    school: str | None = Field(None, min_length=1, max_length=128)
    major: str | None = Field(None, min_length=1, max_length=64)
    province: str | None = Field(None, min_length=1, max_length=32)
    gender: str | None = Field(None, min_length=1, max_length=2)
    age: int | None = Field(None, ge=16, le=80)
    education: str | None = Field(None, min_length=1, max_length=32)
    address: str | None = Field(None, min_length=1, max_length=256)
    zip_code: str | None = Field(None, min_length=6, max_length=6)
    id_card_both_sides_key: str | None = Field(None, min_length=1, max_length=512)
    portrait_photo_key: str | None = Field(None, min_length=1, max_length=512)
    xuexin_report_key: str | None = Field(None, min_length=1, max_length=512)
    application_form_key: str | None = Field(None, min_length=1, max_length=512)



class NispRegistrationVersionResponse(BaseModel):
    id: int
    registration_id: int
    version_no: int
    candidate_snapshot: dict
    material_versions: dict | None = None
    source: str
    submitted_at: datetime
    superseded_at: datetime | None = None
    is_current: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class NispCorrectionRequestResponse(BaseModel):
    id: int
    registration_id: int
    allowed_fields: list[str]
    allowed_material_types: list[str] | None = None
    reason_code: str
    reason_detail: str | None
    due_at: datetime
    status: str
    created_by_admin_id: int | None = None
    submitted_version_id: int | None = None
    submitted_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class NispRejectRefundRequest(BaseModel):
    reason_code: str = Field(..., min_length=1, max_length=64)
    reason_detail: str = Field(..., min_length=1, max_length=2000)


class NispFinalReviewRequest(BaseModel):
    decision: Literal["approved", "rejected"] = "approved"
    export_item_id: int = Field(..., gt=0)
    reason_code: str | None = Field(None, max_length=64)
    reason_detail: str | None = Field(None, max_length=2000)


class NispBatchFinalReviewRequest(BaseModel):
    export_job_id: int | None = Field(None, gt=0)
    registration_ids: list[int] | None = None
    targets: list["NispBatchFinalReviewTarget"] | None = None

    @field_validator("registration_ids")
    @classmethod
    def _validate_registration_ids(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        if not value or any(item <= 0 for item in value):
            raise ValueError("报名 ID 必须为正整数且不能为空")
        if len(set(value)) != len(value):
            raise ValueError("报名 ID 不能重复")
        return value

    @field_validator("targets")
    @classmethod
    def _validate_targets(
        cls, value: list["NispBatchFinalReviewTarget"] | None
    ) -> list["NispBatchFinalReviewTarget"] | None:
        if value is None:
            return None
        if not value:
            raise ValueError("至少选择一名报名人员")
        registration_ids = [item.registration_id for item in value]
        if len(set(registration_ids)) != len(registration_ids):
            raise ValueError("同一报名不能重复终审")
        return value

    @model_validator(mode="after")
    def validate_request(self):
        if self.targets:
            return self
        if self.export_job_id is None or not self.registration_ids:
            raise ValueError("请提交逐人导出版本或使用兼容的批量终审参数")
        return self


class NispBatchFinalReviewTarget(BaseModel):
    registration_id: int = Field(..., gt=0)
    export_item_id: int = Field(..., gt=0)


class NispFinalReviewResult(BaseModel):
    registration_id: int
    export_item_id: int | None = None
    registration_no: str | None = None
    name: str | None = None
    success: bool
    status: Literal["success", "skipped", "failed"] = "success"
    reason: str | None = None


class NispBatchFinalReviewResponse(BaseModel):
    items: list[NispFinalReviewResult]


# ── Export ──

class NispExportCreate(BaseModel):
    batch_id: int
    level: NispLevel | None = None
    include_statuses: list[NispRegistrationStatus] = Field(
        default=["approved"], min_length=1
    )
    registration_ids: list[int] | None = Field(None)

    @field_validator("include_statuses")
    @classmethod
    def _validate_statuses(cls, value: list[NispRegistrationStatus]) -> list[NispRegistrationStatus]:
        if not value:
            raise ValueError("至少选择一个导出状态")
        return list(dict.fromkeys(value))

    @field_validator("registration_ids")
    @classmethod
    def _validate_registration_ids(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        if not value or any(item <= 0 for item in value):
            raise ValueError("报名 ID 必须为正整数且不能为空")
        if len(set(value)) != len(value):
            raise ValueError("报名 ID 不能重复")
        return value


class NispExportPrecheckRequest(BaseModel):
    batch_id: int
    registration_ids: list[int] | None = Field(None)
    status: NispRegistrationStatus | None = None
    payment_state: str | None = None
    has_valid_export: bool | None = None
    keyword: str | None = Field(None, max_length=64)

    @field_validator("registration_ids")
    @classmethod
    def _validate_registration_ids(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        if not value or any(item <= 0 for item in value):
            raise ValueError("报名 ID 必须为正整数且不能为空")
        if len(set(value)) != len(value):
            raise ValueError("报名 ID 不能重复")
        return value


class NispExportPrecheckPerson(BaseModel):
    registration_id: int
    registration_no: str
    name: str
    level: NispLevel
    status: NispRegistrationStatus
    exportable: bool
    reason: str | None = None


class NispExportPrecheckResponse(BaseModel):
    selected_count: int
    exportable_count: int
    blocked_count: int
    level: NispLevel
    exportable_registration_ids: list[int]
    people: list[NispExportPrecheckPerson]


class NispExportJobResponse(BaseModel):
    id: int
    batch_id: int
    level: NispLevel
    include_statuses: list[str]
    status: str
    artifact_type: str
    registration_count: int
    requested_by_admin_id: int
    requested_by_admin_name: str | None = None
    valid_item_count: int = 0
    registration_ids: list[int] = []
    file_expired: bool = False
    download_available: bool = False
    started_at: datetime | None
    finished_at: datetime | None
    storage_key: str | None
    artifact_sha256: str | None
    artifact_bytes: int | None
    expires_at: datetime | None
    last_error: str | None
    result_summary: dict | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class NispExportItemResponse(BaseModel):
    id: int
    job_id: int
    registration_id: int
    registration_no: str
    name: str
    level: NispLevel
    current_status: NispRegistrationStatus
    version_no: int | None = None
    is_valid: bool
    invalidated_reason: str | None = None
    review_ready: bool
    block_reason: str | None = None
    created_at: datetime


class NispSignedUrlResponse(BaseModel):
    url: str
    expires_at: datetime


class NispRefundResponse(BaseModel):
    payment_channel: str = "unknown"
    id: int
    registration_id: int
    order_id: int
    out_trade_no: str | None = None
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
    candidate_name: str | None = None
    registration_no: str | None = None
    batch_id: int | None = None
    batch_name: str | None = None
    product_label: str | None = None

    model_config = {"from_attributes": True}


class NispRefundConfirmRequest(BaseModel):
    refund_id: int
