import inspect

from app.api.admin import points_mall as admin_points_mall
from app.schemas.points_virtual import (
    PointsVirtualOrderDetailResponse,
    PointsVirtualOrderResponse,
    PointsVirtualProductResponse,
)
from app.services.points_virtual import PointsVirtualService


def test_virtual_product_list_supports_admin_browsing_filters():
    signature = inspect.signature(admin_points_mall.list_virtual_products)

    assert "keyword" in signature.parameters
    assert "status" in signature.parameters
    assert "product_type" in signature.parameters
    assert "availability" in signature.parameters


def test_virtual_order_list_supports_lookup_filters():
    signature = inspect.signature(admin_points_mall.list_virtual_orders)

    assert "keyword" in signature.parameters
    assert "product_id" in signature.parameters
    assert "status" in signature.parameters
    assert "created_from" in signature.parameters
    assert "created_to" in signature.parameters


def test_admin_detail_responses_explain_redemption_and_refund_state():
    product_fields = PointsVirtualProductResponse.model_fields
    order_fields = PointsVirtualOrderDetailResponse.model_fields

    assert "content_name" in product_fields
    assert "remaining_stock" in product_fields
    assert "redeemable" in product_fields
    assert "redeem_block_reason" in product_fields
    assert "refunded_count" in product_fields
    assert "user_id" in PointsVirtualOrderResponse.model_fields
    assert "user_nickname" in order_fields
    assert "spend_history" in order_fields
    assert "refund_history" in order_fields
    assert "refund" in order_fields


def test_masks_standard_phone_numbers_in_order_details():
    service = PointsVirtualService()

    assert service._mask_phone("13900000000") == "139****0000"
    assert service._mask_phone(None) is None
