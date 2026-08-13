"""Standalone entry point for quiz background processing.

Run with ``python -m app.quiz_worker``.  The HTTP process must set
``QUIZ_EMBEDDED_WORKER_ENABLED=false`` when this worker is deployed.
"""

from __future__ import annotations

import asyncio
import logging

from app.adapter.database import engine
from app.adapter.logging import setup_logging
from app.adapter.redis import redis_client
from app.port.config import settings
from app.services.quiz_tasks import ensure_quiz_runtime_ready, quiz_worker_loop


logger = logging.getLogger(__name__)


async def run() -> None:
    if not settings.QUIZ_TASKS_ENABLED:
        raise RuntimeError("quiz tasks are disabled")
    if not settings.QUIZ_WORKER_PROCESS:
        raise RuntimeError("QUIZ_WORKER_PROCESS must be true for the quiz worker")

    setup_logging()
    await ensure_quiz_runtime_ready()
    logger.info("standalone quiz worker starting")
    try:
        await quiz_worker_loop()
    finally:
        await engine.dispose()
        await redis_client.aclose()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("standalone quiz worker stopped")


if __name__ == "__main__":
    main()
