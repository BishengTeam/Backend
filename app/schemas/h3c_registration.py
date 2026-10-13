from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


H3cRegistrationType = Literal["coupon", "student", "full"]
H3cExportRegistrationType = Literal["coupon", "student", "full", "mixed"]
H3cArtifactType = Literal["embedded_xlsx", "images_zip", "full_package"]
H3cRegistrationStatus = Literal[
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
H3cExportStatus = Literal["queued", "running", "succeeded", "failed"]
H3cRejectionReasonCode = Literal[
    "image_unclear",
    "image_incomplete",
    "material_type_mismatch",
    "verify_code_invalid",
    "suspected_forged_material",
]
H3cCorrectionFieldType = Literal["phone", "email", "school", "address", "verify_code"]


class H3cProfileDefaults(BaseModel):
    identity_status: str | None = None
    candidate_name: str | None = None
    gender: str | None = None
    candidate_idcard: str | None = None
    school: str | None = None
    address: str | None = None
    phone: str | None = None
    email: str | None = None
    education: str | None = None
    first_name_en: str | None = None
    last_name_en: str | None = None


class H3cExamBatchBase(BaseModel):
    certification_code: str = Field(..., min_length=1, max_length=32)
    name: str = Field(..., min_length=1, max_length=128)
    apply_start: datetime
    apply_end: datetime
    exam_date: datetime
    capacity: int = Field(..., ge=1)
    exam_location: str | None = Field(None, max_length=256)
    description: str | None = Field(None, max_length=5000)
    sort_order: int = Field(0, ge=0)
    exam_code: str = Field(..., min_length=1, max_length=64)
    identity_tag: str = Field(..., min_length=1, max_length=64)
    country: str = Field("CHN", min_length=1, max_length=8)
    language: str = Field("CHS", min_length=1, max_length=8)
    training_org: str | None = Field(None, max_length=128)
    training_teacher: str | None = Field(None, max_length=64)
    training_address: str | None = Field(None, max_length=256)
    training_start: datetime | None = None
    training_end: datetime | None = None
    coupon_price_cents: int = Field(..., ge=0)
    student_price_cents: int = Field(..., ge=0)
    full_price_cents: int = Field(..., ge=0)
    payment_timeout_minutes: int = Field(30, ge=1, le=1440)
    resubmission_window_hours: int = Field(72, ge=1, le=720)
    max_resubmissions: int = Field(2, ge=0, le=10)
    max_material_bytes: int = Field(10 * 1024 * 1024, ge=1, le=20 * 1024 * 1024)

    @field_validator("name", "exam_code", "identity_tag")
    @classmethod
    def _trim_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("字段不能为空")
        return cleaned

    @model_validator(mode="after")
    def validate_windows(self):
        if self.apply_start >= self.apply_end:
            raise ValueError("报名开始时间必须早于截止时间")
        if self.training_start and self.training_end and self.training_start > self.training_end:
            raise ValueError("培训开始时间不能晚于结束时间")
        return self


class H3cExamBatchCreate(H3cExamBatchBase):
    pass


class H3cExamBatchUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    apply_start: datetime | None = None
    apply_end: datetime | None = None
    exam_date: datetime | None = None
    capacity: int | None = Field(None, ge=1)
    exam_location: str | None = Field(None, max_length=256)
    description: str | None = Field(None, max_length=5000)
    sort_order: int | None = Field(None, ge=0)
    exam_code: str | None = Field(None, min_length=1, max_length=64)
    identity_tag: str | None = Field(None, min_length=1, max_length=64)
    country: str | None = Field(None, min_length=1, max_length=8)
    language: str | None = Field(None, min_length=1, max_length=8)
    training_org: str | None = Field(None, max_length=128)
    training_teacher: str | None = Field(None, max_length=64)
    training_address: str | None = Field(None, max_length=256)
    training_start: datetime | None = None
    training_end: datetime | None = None
    coupon_price_cents: int | None = Field(None, ge=0)
    student_price_cents: int | None = Field(None, ge=0)
    full_price_cents: int | None = Field(None, ge=0)
    payment_timeout_minutes: int | None = Field(None, ge=1, le=1440)
    resubmission_window_hours: int | None = Field(None, ge=1, le=720)
    max_resubmissions: int | None = Field(None, ge=0, le=10)
    max_material_bytes: int | None = Field(None, ge=1, le=20 * 1024 * 1024)


class H3cPriceOption(BaseModel):
    registration_type: H3cRegistrationType
    price_cents: int


class H3cExamBatchResponse(BaseModel):
    id: int
    plan_id: int
    certification_code: str
    name: str
    status: str
    apply_start: datetime
    apply_end: datetime
    exam_date: datetime
    capacity: int
    occupied_count: int
    remaining_count: int
    exam_location: str | None
    description: str | None
    sort_order: int
    exam_code: str
    identity_tag: str
    country: str
    language: str
    training_org: str | None
    training_teacher: str | None
    training_address: str | None
    training_start: datetime | None
    training_end: datetime | None
    payment_timeout_minutes: int
    resubmission_window_hours: int
    max_resubmissions: int
    max_material_bytes: int
    prices: list[H3cPriceOption]
    registration_counts: dict[str, int] = {}
    published_at: datetime | None
    registration_closed_at: datetime | None
    cancelled_at: datetime | None
    finalized_at: datetime | None
    created_at: datetime
    updated_at: datetime


class H3cUserExamBatchResponse(BaseModel):
    id: int
    plan_id: int
    certification_code: str
    name: str
    status: str
    apply_start: datetime
    apply_end: datetime
    exam_date: datetime
    remaining_count: int
    exam_location: str | None
    description: str | None
    prices: list[H3cPriceOption]
    payment_timeout_minutes: int
    max_material_bytes: int


class H3cMaterialUploadResponse(BaseModel):
    material_type: str
    storage_key: str
    size_bytes: int
    sha256: str


class H3cOrderCreate(BaseModel):
    batch_id: int = Field(..., gt=0)
    registration_type: H3cRegistrationType
    candidate_name: str = Field(..., min_length=1, max_length=64)
    gender: str = Field(..., min_length=1, max_length=4)
    candidate_idcard: str = Field(..., min_length=18, max_length=18)
    school: str = Field(..., min_length=1, max_length=128)
    address: str = Field(..., min_length=1, max_length=256)
    phone: str = Field(..., min_length=5, max_length=20)
    email: str = Field(..., min_length=3, max_length=128)
    education: str = Field(..., min_length=1, max_length=32)
    first_name_en: str = Field(..., min_length=1, max_length=64)
    last_name_en: str = Field(..., min_length=1, max_length=64)
    coupon_code: str | None = Field(None, min_length=1, max_length=64)
    verify_code: str | None = Field(None, min_length=1, max_length=64)
    coupon_proof_key: str | None = Field(None, min_length=1, max_length=512)
    student_proof_key: str | None = Field(None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def validate_type_materials(self):
        if self.registration_type == "coupon" and (not self.coupon_code or not self.coupon_proof_key):
            raise ValueError("考券报名必须提供考券号和优惠券证明图片")
        if self.registration_type == "student" and (not self.verify_code or not self.student_proof_key):
            raise ValueError("学生报名必须提供学信网在线验证码和学生证明图片")
        if self.registration_type == "full":
            if any((self.coupon_code, self.verify_code, self.coupon_proof_key, self.student_proof_key)):
                raise ValueError("全额报名不能提交考券或学生证明材料")
        return self


class H3cMaterialResponse(BaseModel):
    id: int
    material_type: str
    version_no: int
    storage_key: str
    preview_url: str | None = None
    original_filename: str
    size_bytes: int
    sha256: str
    is_current: bool
    uploaded_at: datetime


class H3cReviewResponse(BaseModel):
    id: int
    decision: str
    reason_code: str | None
    reason_detail: str | None
    rejected_material_types: list[str] | None
    reviewer_admin_id: int
    reviewed_at: datetime

    model_config = {"from_attributes": True}


class H3cRegistrationResponse(BaseModel):
    id: int
    registration_no: str
    batch_id: int
    plan_id: int
    order_id: int
    registration_type: H3cRegistrationType
    status: H3cRegistrationStatus
    active_refund: "H3cRegistrationRefundSummary | None" = None
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
    exam_date: datetime | None = None
    exam_location: str | None = None
    materials: list[H3cMaterialResponse]
    latest_review: H3cReviewResponse | None = None
    pending_correction: "H3cCorrectionRequestResponse | None" = None
    versions: list["H3cRegistrationVersionResponse"] = []
    final_export_item_id: int | None = None
    latest_valid_export_job_id: int | None = None
    latest_valid_export_finished_at: datetime | None = None
    final_review_ready: bool = False
    final_review_block_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class H3cRegistrationRefundSummary(BaseModel):
    id: int
    status: str
    out_refund_no: str | None
    processing_at: datetime | None
    succeeded_at: datetime | None
    last_error: str | None
    retry_count: int

    model_config = {"from_attributes": True}


class H3cRegistrationVersionResponse(BaseModel):
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


class H3cCorrectionRequestResponse(BaseModel):
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


class H3cResubmissionCreate(BaseModel):
    phone: str | None = Field(None, min_length=5, max_length=20)
    email: str | None = Field(None, min_length=3, max_length=128)
    school: str | None = Field(None, min_length=1, max_length=128)
    address: str | None = Field(None, min_length=1, max_length=256)
    verify_code: str | None = Field(None, min_length=1, max_length=64)
    coupon_proof_key: str | None = Field(None, min_length=1, max_length=512)
    student_proof_key: str | None = Field(None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def validate_nonempty(self):
        values = [
            self.phone, self.email, self.school, self.address, self.verify_code,
            self.coupon_proof_key, self.student_proof_key,
        ]
        if not any(values):
            raise ValueError("请至少提交一项补正内容")
        return self


class H3cReviewDecision(BaseModel):
    decision: Literal["approved", "rejected"]
    reason_code: H3cRejectionReasonCode | None = None
    reason_detail: str | None = Field(None, max_length=1000)
    rejected_material_types: list[Literal["coupon_proof", "student_proof"]] = []
    allowed_fields: list[H3cCorrectionFieldType] = []

    @model_validator(mode="after")
    def validate_rejection(self):
        if self.decision == "rejected" and (
            not self.reason_code
            or (not self.rejected_material_types and not self.allowed_fields)
        ):
            raise ValueError("拒绝时必须选择拒绝原因和至少一项补正内容")
        return self


class H3cRejectRefundRequest(BaseModel):
    reason_code: str = Field(..., min_length=1, max_length=64)
    reason_detail: str = Field(..., min_length=1, max_length=1000)


class H3cFinalReviewRequest(BaseModel):
    decision: Literal["approved", "rejected"] = "approved"
    export_item_id: int = Field(..., gt=0)
    reason_code: str | None = Field(None, max_length=64)
    reason_detail: str | None = Field(None, max_length=1000)


class H3cBatchFinalReviewRequest(BaseModel):
    export_job_id: int | None = Field(None, gt=0)
    registration_ids: list[int] | None = None
    targets: list["H3cBatchFinalReviewTarget"] | None = None

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
        cls, value: list["H3cBatchFinalReviewTarget"] | None
    ) -> list["H3cBatchFinalReviewTarget"] | None:
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


class H3cBatchFinalReviewTarget(BaseModel):
    registration_id: int = Field(..., gt=0)
    export_item_id: int = Field(..., gt=0)


class CertificationFinalReviewResult(BaseModel):
    registration_id: int
    export_item_id: int | None = None
    registration_no: str | None = None
    name: str | None = None
    success: bool
    status: Literal["success", "skipped", "failed"] = "success"
    reason: str | None = None


class H3cBatchFinalReviewResponse(BaseModel):
    items: list[CertificationFinalReviewResult]


class H3cRefundConfirmRequest(BaseModel):
    reason_detail: str | None = Field(None, max_length=500)


class H3cRefundResponse(BaseModel):
    id: int
    registration_id: int
    order_id: int
    request_kind: str
    reason_code: str
    reason_detail: str | None
    amount_cents: int
    status: str
    approved_by_admin_id: int | None
    approved_at: datetime | None
    exam_date: datetime | None = None
    exam_location: str | None = None
    out_refund_no: str | None
    processing_at: datetime | None
    succeeded_at: datetime | None
    last_error: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class H3cCloseRequest(BaseModel):
    reason_detail: str = Field(..., min_length=3, max_length=500)


class H3cExportCreate(BaseModel):
    batch_id: int = Field(..., gt=0)
    registration_type: H3cExportRegistrationType | None = None
    artifact_type: H3cArtifactType
    include_statuses: list[H3cRegistrationStatus] = Field(default=["approved"])
    registration_ids: list[int] | None = Field(None)

    @field_validator("include_statuses")
    @classmethod
    def validate_statuses(cls, value: list[str]) -> list[str]:
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

    @model_validator(mode="after")
    def validate_artifact(self):
        if self.artifact_type == "full_package":
            if self.registration_type is None:
                self.registration_type = "mixed"
        elif self.registration_type in (None, "mixed"):
            raise ValueError("该导出产物必须选择具体报名类型")
        return self


class H3cExportPrecheckRequest(BaseModel):
    batch_id: int = Field(..., gt=0)
    registration_ids: list[int] | None = Field(None)
    registration_type: H3cRegistrationType | None = None
    status: H3cRegistrationStatus | None = "approved"
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


class H3cExportPrecheckPerson(BaseModel):
    registration_id: int
    registration_no: str
    name: str
    registration_type: H3cRegistrationType
    status: H3cRegistrationStatus
    exportable: bool
    reason: str | None = None


class H3cExportPrecheckResponse(BaseModel):
    selected_count: int
    exportable_count: int
    blocked_count: int
    counts_by_type: dict[str, int]
    exportable_registration_ids: list[int]
    people: list[H3cExportPrecheckPerson]


class H3cExportJobResponse(BaseModel):
    id: int
    batch_id: int
    registration_type: H3cExportRegistrationType
    artifact_type: H3cArtifactType
    requested_by_admin_id: int
    requested_by_admin_name: str | None = None
    include_statuses: list[str]
    status: H3cExportStatus
    registration_count: int
    valid_item_count: int = 0
    counts_by_type: dict[str, int] = {}
    registration_ids: list[int] = []
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


class H3cExportItemResponse(BaseModel):
    id: int
    job_id: int
    registration_id: int
    registration_no: str
    name: str
    registration_type: H3cRegistrationType
    current_status: H3cRegistrationStatus
    version_no: int | None = None
    is_valid: bool
    invalidated_reason: str | None = None
    review_ready: bool
    block_reason: str | None = None
    created_at: datetime


class H3cSignedUrlResponse(BaseModel):
    url: str
    expires_in: int
