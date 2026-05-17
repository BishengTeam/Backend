from abc import ABC, abstractmethod
from typing import AsyncGenerator


class ChatBackend(ABC):
    @property
    @abstractmethod
    def type(self) -> str:
        ...

    @abstractmethod
    async def send_message(self, user_id: int, message: str, context: list[dict]) -> str:
        ...

    @abstractmethod
    async def stream_message(self, user_id: int, message: str, context: list[dict]) -> AsyncGenerator[str, None]:
        ...


class ManualChatBackend(ChatBackend):
    @property
    def type(self) -> str:
        return "manual"

    async def send_message(self, user_id: int, message: str, context: list[dict]) -> str:
        return "您好，人工客服正在赶来，请稍候..."

    async def stream_message(self, user_id: int, message: str, context: list[dict]) -> AsyncGenerator[str, None]:
        parts = ["您好", "，", "人工", "客服", "正在", "赶来", "，", "请稍候", "..."]
        for part in parts:
            yield part
