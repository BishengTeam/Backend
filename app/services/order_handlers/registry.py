"""Order handler registry — replaces if/elif branching (OCP)."""

from typing import Callable, Type

from app.services.order_handlers.base import OrderHandler


class OrderHandlerRegistry:
    _handlers: dict[str, Type[OrderHandler]] = {}

    @classmethod
    def register(cls, order_kind: str) -> Callable[[Type[OrderHandler]], Type[OrderHandler]]:
        def decorator(handler_class: Type[OrderHandler]) -> Type[OrderHandler]:
            if order_kind in cls._handlers:
                raise ValueError(
                    f"Duplicate order handler registration for '{order_kind}': "
                    f"{cls._handlers[order_kind].__name__} and {handler_class.__name__}"
                )
            # Validate order_kind consistency at registration time
            instance = handler_class()
            if instance.order_kind != order_kind:
                raise ValueError(
                    f"Handler order_kind mismatch: registered as '{order_kind}' "
                    f"but handler declares '{instance.order_kind}'"
                )
            cls._handlers[order_kind] = handler_class
            return handler_class
        return decorator

    @classmethod
    def get(cls, order_kind: str) -> OrderHandler:
        handler_class = cls._handlers.get(order_kind)
        if handler_class is None:
            from app.port.exceptions import BusinessException
            raise BusinessException(f"不支持的订单类型: {order_kind}")
        return handler_class()

    @classmethod
    def supported_kinds(cls) -> list[str]:
        return sorted(cls._handlers.keys())


order_handler_registry = OrderHandlerRegistry
