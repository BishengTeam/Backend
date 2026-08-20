H3C_EXPORTABLE_STATUSES = (
    "pending_payment",
    "pending_review",
    "rejected_awaiting_resubmission",
    "pending_refund_confirmation",
    "refund_processing",
    "approved",
    "refunded_closed",
    "cancelled",
)

H3C_ACTIVE_REGISTRATION_STATUSES = (
    "pending_payment",
    "pending_review",
    "rejected_awaiting_resubmission",
    "pending_refund_confirmation",
    "refund_processing",
    "approved",
)

H3C_TYPE_LABELS = {
    "coupon": "考券报名",
    "student": "学生报名",
    "full": "全额报名",
}

H3C_TYPE_CODES = {
    "coupon": "COUPON",
    "student": "STUDENT",
    "full": "FULL",
}
