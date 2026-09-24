# Abstract layer only — import concrete implementations from app.repositories.sqlalchemy
from app.repositories.protocols.order import OrderRepository
from app.repositories.protocols.points_mall import (
    PointsMallItemRepository,
    PointsMallRedemptionRepository,
)

__all__ = [
    "OrderRepository",
    "PointsMallItemRepository",
    "PointsMallRedemptionRepository",
]
