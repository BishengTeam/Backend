"""domain/order 公开入口。对外仅暴露此 index。"""

# Models
from app.domain.order.src.model.order import Order
from app.domain.order.src.model.price_config import PriceConfig
from app.domain.order.src.model.inventory import Inventory, InventoryRecord
from app.domain.order.src.model.coupon import Coupon, UserCoupon

# Rules
from app.domain.order.src.rule.order_rules import (
    ORDER_PAYMENT_EXPIRE_MINUTES,
    ORDER_STATUS_TRANSITIONS,
    EXTRA_DATA_SCHEMA,
    validate_extra_data,
)

# Transitions
from app.domain.order.src.transition.order_transitions import (
    DEFAULT_TIMEOUT_CLOSE_REASON,
    apply_order_status_transition,
    close_expired_pending_order,
)
from app.domain.order.src.transition.inventory_transitions import (
    INVENTORY_CONFIRM_ACTION,
    INVENTORY_LOCK_ACTION,
    INVENTORY_RELEASE_ACTION,
    INVENTORY_TYPE_CERTIFICATION,
    InventoryChange,
    add_inventory_record,
    confirm_inventory_sale,
    lock_certification_inventory,
    release_inventory_lock,
)

__all__ = [
    # Models
    "Order",
    "PriceConfig",
    "Inventory",
    "InventoryRecord",
    "Coupon",
    "UserCoupon",
    # Rules
    "ORDER_PAYMENT_EXPIRE_MINUTES",
    "ORDER_STATUS_TRANSITIONS",
    "EXTRA_DATA_SCHEMA",
    "validate_extra_data",
    # Transitions
    "DEFAULT_TIMEOUT_CLOSE_REASON",
    "apply_order_status_transition",
    "close_expired_pending_order",
    "INVENTORY_CONFIRM_ACTION",
    "INVENTORY_LOCK_ACTION",
    "INVENTORY_RELEASE_ACTION",
    "INVENTORY_TYPE_CERTIFICATION",
    "InventoryChange",
    "add_inventory_record",
    "confirm_inventory_sale",
    "lock_certification_inventory",
    "release_inventory_lock",
]
