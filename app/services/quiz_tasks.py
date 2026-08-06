"""Process-level runner shared by quiz background processors."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.adapter.redis import redis_ping
from app.port.config import settings


logger = logging.getLogger(__name__)
QuizTaskProcessor = Callable[[], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class QuizTaskRuntime:
    poll_seconds: int
    heartbeat_seconds: int
    stale_seconds: int
    max_retries: int


QUIZ_TASK_RUNTIME = QuizTaskRuntime(
    poll_seconds=settings.QUIZ_WORKER_POLL_SECONDS,
    heartbeat_seconds=settings.QUIZ_WORKER_HEARTBEAT_SECONDS,
    stale_seconds=settings.QUIZ_WORKER_STALE_SECONDS,
    max_retries=settings.QUIZ_WORKER_MAX_RETRIES,
)


class QuizTaskRegistry:
    """Ordered processor registry; database row locking remains processor-owned."""

    def __init__(self) -> None:
        self._processors: dict[str, QuizTaskProcessor] = {}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._processors)

    def register(self, name: str, processor: QuizTaskProcessor) -> None:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("quiz task processor name cannot be empty")
        if normalized_name in self._processors:
            raise ValueError(f"quiz task processor already registered: {normalized_name}")
        self._processors[normalized_name] = processor

    async def run_once(self) -> bool:
        did_work = False
        for name, processor in self._processors.items():
            try:
                did_work = await processor() or did_work
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("quiz task processor failed: processor=%s", name)
        return did_work


quiz_task_registry = QuizTaskRegistry()


async def ensure_quiz_runtime_ready() -> None:
    """Fail production startup when the shared Redis dependency is unavailable."""
    if not settings.QUIZ_TASKS_ENABLED or settings.APP_ENV != "production":
        return
    if not await redis_ping():
        raise RuntimeError("Redis is required for quiz tasks in production")


async def quiz_worker_loop(registry: QuizTaskRegistry | None = None) -> None:
    active_registry = registry or quiz_task_registry
    logger.info(
        "quiz task loop started: processors=%s poll_seconds=%d",
        active_registry.names,
        QUIZ_TASK_RUNTIME.poll_seconds,
    )
    while True:
        did_work = await active_registry.run_once()
        await asyncio.sleep(0 if did_work else QUIZ_TASK_RUNTIME.poll_seconds)
