from pydantic import BaseModel


class RegistrationPaymentSummary(BaseModel):
    order_id: int | None = None
    payment_channel: str = "unknown"
    order_status: str | None = None
    offline_status: str | None = None
    review_payment_allowed: bool = False
    review_block_reason: str | None = None
