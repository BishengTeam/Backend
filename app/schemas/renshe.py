import re
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.order import OrderStatus
from app.schemas.plan import PlanStatus
from app.schemas.consultation_group import ConsultationGroupUserResponse


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
RensheApplicationMaterialKind = Literal[
    "registration_portrait", "graduation_certificate", "education_report", "commitment_letter",
    "social_security_proof", "xuexin_registration", "education_proof",
    "vocational_qualification_certificate", "professional_title_certificate"
]
RensheAllMaterialKind = Literal[
    "id_card_front", "id_card_back", "portrait", "student_card", "xuexin_registration", "education_proof",
    "registration_portrait", "graduation_certificate", "education_report", "commitment_letter",
    "social_security_proof", "vocational_qualification_certificate", "professional_title_certificate",
]
RensheRefundStatus = Literal[
    "requested",
    "approved",
    "processing",
    "succeeded",
    "rejected",
    "failed",
]
RensheAuditResult = Literal["succeeded", "failed"]


class RensheProgramResponse(BaseModel):
    program_code: str
    occupation_name: str
    occupation_code: str
    skill_level: str
    exam_type: str
    application_type: str


class RensheVerificationMaterialResponse(BaseModel):
    kind: RensheMaterialKind
    storage_key: str
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str


class RensheGraduatedEducation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    graduated: Literal[True]
    school: str = Field(..., min_length=1, max_length=128)
    institution_type: Literal["technical", "vocational", "general_higher"]
    education: Literal["secondary_vocational", "technical_school", "vocational_high", "higher_vocational", "associate", "bachelor", "master", "doctorate"]
    major: str = Field(..., min_length=1, max_length=128)
    graduation_date: date
    certificate_number: str = Field(..., min_length=1, max_length=128)

    @field_validator("graduation_date")
    @classmethod
    def validate_graduation_date(cls, value: date) -> date:
        from app.services.renshe_graduated import china_today
        if value > china_today() or value.year < 1900:
            raise ValueError("毕业日期应为1900年至今天，且已取得毕业证")
        return value


class RensheReportingInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    occupation_status: Literal["在校学生", "应届毕业生", "职工", "失业人员", "其他"]
    ethnicity: str = Field(..., min_length=1, max_length=16)
    political_status: str = Field(..., min_length=1, max_length=16)
    has_work_experience: bool = Field(..., strict=True)
    work_start_date: date | None = None
    enrollment_date: date | None = None
    occupation_years: Decimal | None = Field(None, ge=0, max_digits=6, decimal_places=2)
    work_unit: str | None = Field(None, max_length=128)
    social_insurance_unit: str | None = Field(None, max_length=128)

    @field_validator("work_start_date", "enrollment_date")
    @classmethod
    def validate_reporting_date(cls, value: date | None) -> date | None:
        from app.services.renshe_graduated import china_today
        if value is not None and (value.year < 1900 or value > china_today()):
            raise ValueError("日期不得晚于今天且不能早于1900年")
        return value


class RensheDraftUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: int = Field(..., ge=1, description="人社报考批次 ID")
    contact_phone: str = Field(..., description="报名联系电话")
    mailing_address: str = Field(..., min_length=1, max_length=256, description="通讯地址")
    email: str = Field(..., min_length=3, max_length=128, description="电子邮箱")
    education_info: RensheGraduatedEducation | None = None
    reporting_info: RensheReportingInfo | None = None
    reporting_config_id: int | None = Field(None, ge=1)

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
    correction_due_at: datetime | None = None
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


class RensheApplicationPlanSummary(BaseModel):
    id: int
    name: str
    product_type: str | None = None
    project_name: str | None = None
    renshe_config_id: int | None = None
    renshe_config: dict | None = None
    status: PlanStatus
    apply_start: datetime | None = None
    apply_end: datetime | None = None
    exam_date: datetime | None = None
    exam_location: str | None = None
    price_cents: int = Field(..., ge=0)
    occupation_name: str
    occupation_code: str
    skill_level: str
    exam_type: str
    application_type: str

    model_config = {"from_attributes": True}


class RensheApplicationDetailResponse(RensheApplicationResponse):
    plan: RensheApplicationPlanSummary
    versions: list[RensheVersionSummary] = Field(default_factory=list)
    current_order_id: int | None = None
    current_order_status: str | None = None
    current_refund_id: int | None = None
    current_refund_status: str | None = None
    current_refund_last_error: str | None = None
    current_refund_retry_count: int | None = None
    latest_rejection_stage: Literal["initial", "external"] | None = None
    latest_rejection_reason: str | None = None
    required_changes: list[str] | None = None
    consultation_group: ConsultationGroupUserResponse | None = None



class RensheApplicationOrderSummary(BaseModel):
    id: int
    status: OrderStatus
    price_cents: int = Field(..., ge=0)
    expires_at: datetime | None = None
    paid_at: datetime | None = None
    closed_at: datetime | None = None


class RensheApplicationRefundSummary(BaseModel):
    id: int
    status: Literal[
        "requested", "approved", "processing", "succeeded", "rejected", "failed"
    ]
    request_kind: Literal["normal", "exception", "batch_cancel", "batch_finalize"]
    amount_cents: int = Field(..., ge=0)
    requested_at: datetime
    due_at: datetime
    out_refund_no: str | None = None
    succeeded_at: datetime | None = None
    last_error: str | None = None
    retry_count: int = 0


class RensheApplicationListItem(RensheApplicationResponse):
    """A recoverable application entry for a newly signed-in client."""

    plan: RensheApplicationPlanSummary
    current_order: RensheApplicationOrderSummary | None = None
    current_refund: RensheApplicationRefundSummary | None = None


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
    latest_export_job_id: int | None = None
    latest_export_generation_no: int | None = None
    latest_export_finished_at: datetime | None = None
    current_version_exported: bool = False


class RensheReviewCreate(BaseModel):
    expected_version_id: int | None = Field(None, ge=1)
    decision: Literal["approved", "rejected"]
    reason: str | None = Field(None, max_length=2000)
    required_changes: list[str] | None = Field(None, max_length=64)
    correction_due_at: datetime | None = None
    checklist: dict[str, bool] | None = None

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
    correction_due_at: datetime | None = None
    checklist: dict[str, bool] | None = None
    reviewer_id: int
    reviewed_at: datetime
    export_item_id: int | None = None

    model_config = {"from_attributes": True}


class RensheExportCreate(BaseModel):
    application_ids: list[int] | None = Field(None, min_length=0, max_length=1000)


class RensheBatchExternalReviewTarget(BaseModel):
    application_id: int = Field(..., ge=1)
    version_id: int | None = Field(None, ge=1)


class RensheBatchExternalReviewResult(BaseModel):
    application_id: int
    success: bool
    status: Literal["success", "failed"]
    reason: str | None = None
    review: RensheReviewResponse | None = None


class RensheBatchExternalReviewResponse(BaseModel):
    items: list[RensheBatchExternalReviewResult] = Field(default_factory=list)


class RensheBatchExternalReviewRequest(BaseModel):
    targets: list[RensheBatchExternalReviewTarget] = Field(..., min_length=1, max_length=100)
    decision: Literal["approved", "rejected"]
    reason: str | None = Field(None, max_length=2000)
    required_changes: list[str] | None = Field(None, max_length=64)
    correction_due_at: datetime | None = None


class RensheReviewCorrectionCreate(BaseModel):
    to_decision: Literal["approved", "rejected"]
    reason: str = Field(..., min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def require_non_blank_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("更正审核结果必须填写原因")
        return value


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
    out_trade_no: str | None = None
    user_id: int
    request_kind: str
    reason_code: str
    reason_detail: str | None = None
    amount_cents: int
    status: RensheRefundStatus
    requested_at: datetime
    due_at: datetime
    rejection_reason: str | None = None
    approved_by_admin_id: int | None = None
    decided_at: datetime | None = None
    out_refund_no: str | None = None
    wechat_refund_id: str | None = None
    processing_at: datetime | None = None
    succeeded_at: datetime | None = None
    last_error: str | None = None
    retry_count: int
    candidate_name: str | None = None
    registration_no: str | None = None
    batch_id: int | None = None
    batch_name: str | None = None
    product_label: str | None = None

    model_config = {"from_attributes": True}


class RensheMaterialMetadataResponse(BaseModel):
    id: int
    application_id: int
    version_id: int
    kind: RensheAllMaterialKind
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
    plan: RensheApplicationPlanSummary
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
    items: list["RensheExportItemResponse"] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class RensheExportItemResponse(BaseModel):
    id: int
    job_id: int
    application_id: int
    version_id: int
    status: Literal["queued", "running", "succeeded", "failed"]
    estimated_size_bytes: int | None = None
    last_error: str | None = None

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


class RensheAuditLogResponse(BaseModel):
    id: int
    actor_type: Literal["user", "admin", "system"]
    actor_id: int | None = None
    action: str
    object_type: str
    object_id: int
    application_id: int | None = None
    version_id: int | None = None
    material_id: int | None = None
    ip_address: str | None = None
    result: RensheAuditResult
    summary: dict | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
