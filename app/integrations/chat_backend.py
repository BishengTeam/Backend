import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncGenerator

import httpx

from app.port.exceptions import ThirdPartyException


@dataclass(slots=True)
class ChatReply:
    answer: str
    conversation_id: str | None = None


_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"


def strip_think_blocks(text: str) -> str:
    # Reasoning models (e.g. DeepSeek) inline their chain of thought in Dify's
    # `answer` field; that text must never reach end users.
    return _THINK_BLOCK_RE.sub("", text).strip()


class _ThinkStripper:
    """Streaming counterpart of strip_think_blocks: tags may split across chunks."""

    def __init__(self) -> None:
        self._in_think = False
        self._pending = ""
        self._emitted = False

    def feed(self, chunk: str) -> str:
        self._pending += chunk
        out: list[str] = []
        while self._pending:
            if self._in_think:
                end = self._pending.find(_THINK_CLOSE)
                if end < 0:
                    # Keep a suffix long enough to hold a split closing tag.
                    self._pending = self._pending[-(len(_THINK_CLOSE) - 1):]
                    break
                self._pending = self._pending[end + len(_THINK_CLOSE):]
                self._in_think = False
                continue
            start = self._pending.find(_THINK_OPEN)
            if start < 0:
                # Emit everything except a possible split opening tag suffix.
                safe = len(self._pending) - (len(_THINK_OPEN) - 1)
                if safe > 0:
                    self._emit(out, self._pending[:safe])
                    self._pending = self._pending[safe:]
                break
            if start > 0:
                self._emit(out, self._pending[:start])
            self._pending = self._pending[start + len(_THINK_OPEN):]
            self._in_think = True
        return "".join(out)

    def finish(self) -> str:
        tail, self._pending = self._pending, ""
        out: list[str] = []
        if not self._in_think:
            self._emit(out, tail)
        return "".join(out)

    def _emit(self, out: list[str], text: str) -> None:
        if not self._emitted:
            text = text.lstrip()
            if not text:
                return
            self._emitted = True
        out.append(text)


class ChatBackend(ABC):
    @property
    @abstractmethod
    def type(self) -> str:
        ...

    @abstractmethod
    async def send_message(
        self, user_id: int, message: str, context: list[dict], conversation_id: str | None = None,
    ) -> ChatReply:
        ...

    @abstractmethod
    async def stream_message(
        self, user_id: int, message: str, context: list[dict], conversation_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        ...


class DifyChatBackend(ChatBackend):
    # Dify chat runs (RAG + reasoning models) regularly exceed 15s; keep reads
    # well above that while staying bounded.
    _TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0)

    def __init__(self, *, api_base: str, api_key: str) -> None:
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key

    @property
    def type(self) -> str:
        return "dify"

    async def send_message(
        self, user_id: int, message: str, context: list[dict], conversation_id: str | None = None,
    ) -> ChatReply:
        payload = self._payload(
            user_id=user_id, message=message, response_mode="blocking", conversation_id=conversation_id,
        )
        response = await self._post_chat(payload)
        if response.status_code == 404 and conversation_id:
            # Stored conversation no longer exists on Dify side; restart a fresh one.
            payload = self._payload(user_id=user_id, message=message, response_mode="blocking")
            response = await self._post_chat(payload)
        self._raise_if_failed(response)

        data = response.json()
        answer = strip_think_blocks(str(data.get("answer") or ""))
        if not answer:
            raise ThirdPartyException("Dify chat response did not include answer")
        return ChatReply(answer=answer, conversation_id=data.get("conversation_id"))

    async def stream_message(
        self, user_id: int, message: str, context: list[dict], conversation_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        payload = self._payload(
            user_id=user_id, message=message, response_mode="streaming", conversation_id=conversation_id,
        )
        try:
            async for text in self._stream_stripped(payload):
                yield text
            return
        except httpx.HTTPStatusError as exc:
            if not (conversation_id and exc.response.status_code == 404):
                raise ThirdPartyException(f"Dify streaming chat request failed: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ThirdPartyException(f"Dify streaming chat request failed: {exc}") from exc
        try:
            async for text in self._stream_stripped(
                self._payload(user_id=user_id, message=message, response_mode="streaming"),
            ):
                yield text
        except httpx.HTTPError as exc:
            raise ThirdPartyException(f"Dify streaming chat request failed: {exc}") from exc

    async def _post_chat(self, payload: dict[str, Any]) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=self._TIMEOUT) as client:
                return await client.post(self._chat_url, headers=self._headers, json=payload)
        except httpx.HTTPError as exc:
            raise ThirdPartyException(f"Dify chat request failed: {exc}") from exc

    @staticmethod
    def _raise_if_failed(response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ThirdPartyException(f"Dify chat request failed: {exc}") from exc

    async def _stream_stripped(self, payload: dict[str, Any]) -> AsyncGenerator[str, None]:
        stripper = _ThinkStripper()
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", self._chat_url, headers=self._headers, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    piece = self._parse_stream_line(line)
                    if piece:
                        text = stripper.feed(piece)
                        if text:
                            yield text
        tail = stripper.finish()
        if tail:
            yield tail

    @property
    def _chat_url(self) -> str:
        return f"{self.api_base}/chat-messages"

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    @staticmethod
    def _payload(
        *, user_id: int, message: str, response_mode: str, conversation_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "inputs": {},
            "query": message,
            "response_mode": response_mode,
            "user": str(user_id),
        }
        if conversation_id:
            payload["conversation_id"] = conversation_id
        return payload

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
