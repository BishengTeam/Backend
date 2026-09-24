from app.repositories.protocols.order import OrderRepository
from app.repositories.protocols.points_mall import PointsMallRepository
from app.repositories.sqlalchemy.order import SqlAlchemyOrderRepository
from app.repositories.sqlalchemy.points_mall import SqlAlchemyPointsMallRepository

__all__ = [
    "OrderRepository",
    "PointsMallRepository",
    "SqlAlchemyOrderRepository",
    "SqlAlchemyPointsMallRepository",
]
