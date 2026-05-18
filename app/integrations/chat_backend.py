from abc import ABC, abstractmethod
from typing import AsyncGenerator, Any

import httpx

from app.core.exceptions import ThirdPartyException


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


class DifyChatBackend(ChatBackend):
    def __init__(self, *, api_base: str, api_key: str) -> None:
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key

    @property
    def type(self) -> str:
        return "dify"

    async def send_message(self, user_id: int, message: str, context: list[dict]) -> str:
        payload = self._payload(user_id=user_id, message=message, response_mode="blocking")
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(self._chat_url, headers=self._headers, json=payload)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ThirdPartyException(f"Dify chat request failed: {exc}") from exc

        data = response.json()
        answer = data.get("answer")
        if not answer:
            raise ThirdPartyException("Dify chat response did not include answer")
        return str(answer)

    async def stream_message(self, user_id: int, message: str, context: list[dict]) -> AsyncGenerator[str, None]:
        payload = self._payload(user_id=user_id, message=message, response_mode="streaming")
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("POST", self._chat_url, headers=self._headers, json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        chunk = self._parse_stream_line(line)
                        if chunk:
                            yield chunk
        except httpx.HTTPError as exc:
            raise ThirdPartyException(f"Dify streaming chat request failed: {exc}") from exc

    @property
    def _chat_url(self) -> str:
        return f"{self.api_base}/chat-messages"

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    @staticmethod
    def _payload(*, user_id: int, message: str, response_mode: str) -> dict[str, Any]:
        return {
            "inputs": {},
            "query": message,
            "response_mode": response_mode,
            "user": str(user_id),
        }

    @staticmethod
    def _parse_stream_line(line: str) -> str | None:
        if not line.startswith("data:"):
            return None
        raw = line.removeprefix("data:").strip()
        if not raw or raw == "[DONE]":
            return None
        try:
            data = httpx.Response(200, content=raw).json()
        except ValueError:
            return None
        if data.get("event") in {"message", "agent_message"}:
            answer = data.get("answer")
            return str(answer) if answer else None
        return None
