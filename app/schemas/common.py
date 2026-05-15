from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class APIResponse(BaseModel, Generic[T]):
    code: int = 200
    message: str = "ok"
    data: T | None = None


class PaginatedData(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class PaginatedResponse(APIResponse[PaginatedData[T]], Generic[T]):
    data: PaginatedData[T]


def success(data: Any = None, message: str = "ok", code: int = 200) -> APIResponse:
    return APIResponse(code=code, message=message, data=data)


def created(data: Any = None, message: str = "创建成功") -> APIResponse:
    return APIResponse(code=201, message=message, data=data)
