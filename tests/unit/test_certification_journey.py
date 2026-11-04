import pytest
from pydantic import ValidationError

from app.schemas.certification_journey import JourneyContentUpdate, NoticeBlock
from app.services.certification_journey import notice_digest, progress_state


@pytest.mark.parametrize("status,order,offline,stage,label", [
    ("pending_payment", "pending", "awaiting_proof", 0, "报名已提交，请完成线下转账申报"),
    ("pending_payment", "pending", "under_review", 1, "等待核账"),
    ("pending_payment", "pending", "returned", 1, "转账凭证待补交"),
    ("pending_review", "paid", "confirmed", 2, "等待初审"),
    ("pending_initial_review", "completed", None, 2, "等待初审"),
    ("approved", "paid", "confirmed", 3, "等待后续报送／终审"),
    ("pending_external_review", "paid", "confirmed", 3, "等待后续报送／终审"),
    ("initial_rejected", "paid", "confirmed", 2, "报名资料待补正"),
    ("external_rejected", "paid", "confirmed", 3, "报名资料待补正"),
    ("returned", "paid", "confirmed", 2, "报名资料待补正"),
    ("final_approved", "paid", "confirmed", 4, "报名审核通过"),
    ("external_approved", "completed", None, 4, "报名审核通过"),
    ("approved", "pending", "under_review", 1, "等待核账"),
    ("final_approved", "refunded", "confirmed", None, "已退款"),
    ("approved", "paid", "exception", None, "资金异常"),
    ("refund_processing", "paid", "confirmed", None, "退款处理中"),
    ("cancelled", "closed", None, None, "已关闭"),
])
def test_stage_projection_respects_payment_and_exception_priority(status, order, offline, stage, label):
    state = progress_state(status, order_status=order, offline_status=offline)
    assert (state["stage"], state["label"]) == (stage, label)
    assert "percent" not in state


def test_frozen_registration_cannot_continue_normal_approval():
    assert progress_state("external_approved", order_status="paid", frozen=True)["stage"] is None


def test_unconfirmed_zero_price_and_original_payment_channels_do_not_request_a_transfer():
    assert progress_state("pending_payment", order_status="pending", payment_channel="free")["label"] == "报名已提交，请确认零元报名"
    assert progress_state("pending_payment", order_status="pending", payment_channel="wechat")["description"] == "原支付渠道订单请在付款详情核对，请勿重复转账。"


def test_notice_hash_is_stable_but_tracks_price_rules_and_image_order():
    original = {"price": 1, "blocks": ["text", "image"], "rules": {"required": True}}
    assert notice_digest(original) == notice_digest({"rules": {"required": True}, "blocks": ["text", "image"], "price": 1})
    for changed in ({**original, "price": 2}, {**original, "blocks": ["image", "text"]}, {**original, "rules": {"required": False}}):
        assert notice_digest(changed) != notice_digest(original)


@pytest.mark.parametrize("url", ["javascript:alert(1)", "//example.com/a.png", "http://example.com/a.png", "data:image/png;base64,abc"])
def test_notice_images_reject_unsafe_addresses(url):
    with pytest.raises(ValidationError):
        NoticeBlock(type="image", url=url)


def test_notice_configuration_is_strict_and_versioned():
    assert JourneyContentUpdate(version=0).version == 0
    with pytest.raises(ValidationError):
        JourneyContentUpdate(version=0, unknown="x")
    with pytest.raises(ValidationError):
        NoticeBlock(type="text", text=" ")


@pytest.mark.asyncio
async def test_notice_permissions_follow_vendor_write_permissions():
    from types import SimpleNamespace
    from app.api.certification_journey import journey_admin
    from app.port.exceptions import ForbiddenException
    request = SimpleNamespace(method='PUT')
    with pytest.raises(ForbiddenException):
        await journey_admin(request, 'nisp', SimpleNamespace(role='quiz_admin'))
    admin = SimpleNamespace(role='cert_admin')
    assert await journey_admin(request, 'nisp', admin) is admin
