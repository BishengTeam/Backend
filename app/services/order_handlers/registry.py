"""Order handler registry — replaces if/elif branching (OCP)."""

from app.services.order_handlers.base import OrderHandler


class OrderHandlerRegistry:
    _handlers: dict[str, type] = {}

    @classmethod
    def register(cls, order_kind: str):
        def decorator(handler_class):
            cls._handlers[order_kind] = handler_class
            return handler_class
        return decorator

    @classmethod
    def get(cls, order_kind: str) -> OrderHandler:
        handler = cls._handlers.get(order_kind)
        if handler is None:
            from app.port.exceptions import BusinessException
            raise BusinessException(f"不支持的订单类型: {order_kind}")
        return handler()

    @classmethod
    def supported_kinds(cls) -> list[str]:
        return list(cls._handlers.keys())


order_handler_registry = OrderHandlerRegistry
