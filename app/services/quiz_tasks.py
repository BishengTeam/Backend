"""Process-level runner and observability for quiz background processors.

The worker is intentionally small (the database services own row claiming and
transaction boundaries), but every processor is wrapped with the same
monitoring and retry accounting.  ``snapshot()`` is safe to expose from a
health/metrics endpoint and contains no user or question content.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.adapter.redis import redis_ping
from app.port.config import settings


logger = logging.getLogger(__name__)
QuizTaskProcessor = Callable[[], Awaitable[bool]]
QuizQueueDepthReader = Callable[[], Awaitable[int]]
QUIZ_TASK_METRICS_KEY = "quiz:worker:metrics:v1"


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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_metric_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _metric_age_seconds(value: Any, *, now: datetime) -> float | None:
    parsed = _parse_metric_time(value)
    if parsed is None:
        return None
    return round(max(0.0, (now - parsed).total_seconds()), 3)


@dataclass(slots=True)
class QuizTaskMetric:
    """Mutable counters for one processor (kept process-local by design)."""

    name: str
    runs: int = 0
    successes: int = 0
    failures: int = 0
    retries: int = 0
    total_runtime_seconds: float = 0.0
    last_runtime_seconds: float | None = None
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    last_error: str | None = None
    queue_depth: int = 0
    did_work: bool = False

    def as_dict(self) -> dict[str, Any]:
        def iso(value: datetime | None) -> str | None:
            return value.isoformat() if value is not None else None

        return {
            "name": self.name,
            "runs": self.runs,
            "successes": self.successes,
            "failures": self.failures,
            # Keep the verbose names as the canonical contract and expose the
            # short aliases used by older monitoring dashboards.  Both values
            # are counters for the same processor-local metric.
            "failure_count": self.failures,
            "retries": self.retries,
            "retry_count": self.retries,
            "total_runtime_seconds": round(self.total_runtime_seconds, 6),
            "runtime_seconds": round(self.total_runtime_seconds, 6),
            "last_runtime_seconds": (
                round(self.last_runtime_seconds, 6)
                if self.last_runtime_seconds is not None
                else None
            ),
            "last_started_at": iso(self.last_started_at),
            "last_finished_at": iso(self.last_finished_at),
            "last_heartbeat_at": iso(self.last_heartbeat_at),
            "last_error": self.last_error,
            "last_error_type": self.last_error,
            "queue_depth": self.queue_depth,
            "did_work": self.did_work,
        }


class QuizTaskRegistry:
    """Ordered processor registry with uniform metrics and retry logging."""

    def __init__(self) -> None:
        self._processors: dict[str, QuizTaskProcessor] = {}
        self._queue_readers: dict[str, QuizQueueDepthReader] = {}
        self._metrics: dict[str, QuizTaskMetric] = {}
        self._last_queue_refresh = 0.0
        self._last_heartbeat: datetime | None = None

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._processors)

    @property
    def metrics(self) -> dict[str, QuizTaskMetric]:
        """Read-only-by-convention metric objects for tests/diagnostics."""

        return self._metrics

    def register(
        self,
        name: str,
        processor: QuizTaskProcessor,
        *,
        queue_depth: QuizQueueDepthReader | None = None,
    ) -> None:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("quiz task processor name cannot be empty")
        if normalized_name in self._processors:
            raise ValueError(f"quiz task processor already registered: {normalized_name}")
        self._processors[normalized_name] = processor
        if queue_depth is not None:
            self._queue_readers[normalized_name] = queue_depth
        self._metrics[normalized_name] = QuizTaskMetric(name=normalized_name)

    async def refresh_queue_depths(self) -> None:
        """Refresh queue gauges, isolating a dependency outage per task."""

        for name, reader in self._queue_readers.items():
            metric = self._metrics[name]
            try:
                metric.queue_depth = max(
                    0, int(await asyncio.wait_for(reader(), timeout=1.0))
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A queue gauge must never take down the worker.  ``-1`` is
                # represented as zero externally and the failure is visible
                # through the processor's log/metric on the next run.
                logger.error(
                    "quiz task queue depth read failed: processor=%s exception_type=%s",
                    name,
                    type(exc).__name__,
                )

    async def run_once(self) -> bool:
        did_work = False
        now_monotonic = time.monotonic()
        if now_monotonic - self._last_queue_refresh >= min(
            QUIZ_TASK_RUNTIME.heartbeat_seconds, 15
        ):
            await self.refresh_queue_depths()
            self._last_queue_refresh = now_monotonic

        for name, processor in self._processors.items():
            metric = self._metrics[name]
            started_at = _utc_now()
            started = time.monotonic()
            metric.runs += 1
            metric.last_started_at = started_at
            metric.last_heartbeat_at = started_at
            try:
                worked = bool(await processor())
                metric.successes += 1
                metric.did_work = worked
                did_work = worked or did_work
                metric.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                metric.failures += 1
                metric.retries += 1
                metric.did_work = False
                # Keep diagnostics safe: exception text is not returned to
                # clients and is redacted by the regular application logger.
                metric.last_error = type(exc).__name__
                logger.error(
                    "quiz task processor failed: processor=%s retry=%d exception_type=%s",
                    name,
                    metric.retries,
                    type(exc).__name__,
                )
            finally:
                elapsed = time.monotonic() - started
                metric.total_runtime_seconds += elapsed
                metric.last_runtime_seconds = elapsed
                metric.last_finished_at = _utc_now()
                metric.last_heartbeat_at = metric.last_finished_at
        self._last_heartbeat = _utc_now()
        return did_work

    def heartbeat(self) -> None:
        """Record a loop heartbeat without performing I/O."""

        heartbeat = _utc_now()
        self._last_heartbeat = heartbeat
        for metric in self._metrics.values():
            metric.last_heartbeat_at = heartbeat

    def snapshot(self) -> dict[str, Any]:
        """Return JSON-safe queue depth, runtime, failure and heartbeat data."""

        return {
            "source": "process",
            "heartbeat_at": (
                self._last_heartbeat.isoformat() if self._last_heartbeat else None
            ),
            "processors": {
                name: metric.as_dict() for name, metric in self._metrics.items()
            },
        }


async def _import_queue_depth() -> int:
    from sqlalchemy import func, or_, select

    from app.adapter.database import get_db_ctx
    from app.domain.community.src.index import QuizImportJob

    now = _utc_now()
    from datetime import timedelta

    stale_at = now - timedelta(seconds=settings.QUIZ_WORKER_STALE_SECONDS)
    async with get_db_ctx() as db:
        value = await db.scalar(
            select(func.count())
            .select_from(QuizImportJob)
            .where(
                or_(
                    QuizImportJob.status == "queued",
                    (
                        QuizImportJob.status.in_(("validating", "importing"))
                        & (
                            QuizImportJob.execution_protected_until.is_(None)
                            | (QuizImportJob.execution_protected_until <= now)
                        )
                        & (
                            QuizImportJob.heartbeat_at.is_(None)
                            | (QuizImportJob.heartbeat_at < stale_at)
                        )
                    ),
                )
            )
        )
    return int(value or 0)


async def _import_cleanup_queue_depth() -> int:
    from sqlalchemy import exists, func, select

    from app.adapter.database import get_db_ctx
    from app.domain.community.src.index import QuizAdminAuditLog, QuizImportJob

    now = _utc_now()
    from datetime import timedelta

    waiting_before = now - timedelta(days=settings.QUIZ_IMPORT_RETENTION_DAYS)
    async with get_db_ctx() as db:
        already_cleaned = exists(
            select(1).where(
                QuizAdminAuditLog.object_type == "import_job",
                QuizAdminAuditLog.object_id == QuizImportJob.id,
                QuizAdminAuditLog.action == "import.cleanup",
                QuizAdminAuditLog.result == "succeeded",
            )
        )
        value = await db.scalar(
            select(func.count())
            .select_from(QuizImportJob)
            .where(
                (
                    (
                        QuizImportJob.expires_at <= now
                    )
                    & QuizImportJob.status.in_(
                        (
                            "validation_failed",
                            "succeeded",
                            "failed",
                            "cancelled",
                            "expired",
                        )
                    )
                )
                | (
                    (QuizImportJob.status == "awaiting_category_confirmation")
                    & (QuizImportJob.updated_at <= waiting_before)
                ),
                ~already_cleaned,
            )
        )
    return int(value or 0)


async def _exam_timeout_queue_depth() -> int:
    from sqlalchemy import func, select

    from app.adapter.database import get_db_ctx
    from app.domain.community.src.index import QuizExam

    async with get_db_ctx() as db:
        value = await db.scalar(
            select(func.count())
            .select_from(QuizExam)
            .where(
                QuizExam.status == "in_progress",
                QuizExam.deadline_at <= _utc_now(),
            )
        )
    return int(value or 0)


async def _question_stats_queue_depth() -> int:
    """Approximate dirty-stat queue depth from recent immutable events."""

    from datetime import timedelta
    from sqlalchemy import func, select, union

    from app.adapter.database import get_db_ctx
    from app.domain.community.src.index import (
        QuizExam,
        QuizExamAnswer,
        QuizExamQuestion,
        QuizPracticeAttempt,
        QuizPracticeSessionQuestion,
    )

    since = _utc_now() - timedelta(seconds=60)
    async with get_db_ctx() as db:
        practice = select(QuizPracticeSessionQuestion.question_id).join(
            QuizPracticeAttempt,
            (QuizPracticeAttempt.session_question_id == QuizPracticeSessionQuestion.id)
            & (QuizPracticeAttempt.session_id == QuizPracticeSessionQuestion.session_id),
        ).where(QuizPracticeAttempt.submitted_at > since)
        exam = select(QuizExamQuestion.question_id).join(
            QuizExamAnswer,
            (QuizExamAnswer.exam_question_id == QuizExamQuestion.id)
            & (QuizExamAnswer.exam_id == QuizExamQuestion.exam_id),
        ).join(QuizExam, QuizExam.id == QuizExamQuestion.exam_id).where(
            QuizExam.status.in_(("completed", "timed_out")),
            QuizExamAnswer.updated_at > since,
        )
        dirty = union(practice, exam).cte("quiz_stats_queue")
        value = await db.scalar(select(func.count()).select_from(dirty))
    return int(value or 0)


quiz_task_registry = QuizTaskRegistry()


async def _process_import_jobs() -> bool:
    from app.services.admin_quiz import AdminQuizService

    return await AdminQuizService().process_next_import_job()


quiz_task_registry.register(
    "quiz-import", _process_import_jobs, queue_depth=_import_queue_depth
)


def _metrics_ttl_seconds() -> int:
    return max(
        QUIZ_TASK_RUNTIME.stale_seconds * 2,
        QUIZ_TASK_RUNTIME.heartbeat_seconds * 4,
        60,
    )


async def publish_quiz_task_snapshot(
    registry: QuizTaskRegistry | None = None,
) -> None:
    """Publish the worker snapshot for Web probes and Admin monitoring.

    The payload contains only aggregate counters and exception class names.
    A TTL prevents a dead worker from leaving a permanently healthy-looking
    document behind.
    """

    from app.adapter.redis import redis_client

    active_registry = registry or quiz_task_registry
    snapshot = active_registry.snapshot()
    snapshot["source"] = "redis"
    await redis_client.setex(
        QUIZ_TASK_METRICS_KEY,
        _metrics_ttl_seconds(),
        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
    )


async def read_quiz_task_snapshot() -> dict[str, Any]:
    """Read the independent worker's shared metrics without leaking Redis data."""

    if not settings.QUIZ_TASKS_ENABLED:
        return {
            "source": "disabled",
            "heartbeat_at": None,
            "processors": {},
        }

    if settings.QUIZ_EMBEDDED_WORKER_ENABLED:
        return quiz_task_registry.snapshot()

    from app.adapter.redis import redis_client

    try:
        raw = await redis_client.get(QUIZ_TASK_METRICS_KEY)
        payload = json.loads(raw) if raw else None
        if not isinstance(payload, dict):
            raise ValueError("missing quiz worker metrics")
        processors = payload.get("processors")
        if not isinstance(processors, dict):
            raise ValueError("invalid quiz worker metrics")
        return {
            "source": "redis",
            "heartbeat_at": payload.get("heartbeat_at"),
            "processors": processors,
        }
    except Exception as exc:
        logger.warning(
            "quiz worker shared metrics unavailable: exception_type=%s",
            type(exc).__name__,
        )
        return {
            "source": "unavailable",
            "heartbeat_at": None,
            "processors": {},
        }


def quiz_task_snapshot_ready(snapshot: dict[str, Any]) -> bool:
    """Return whether the configured worker has a fresh, complete heartbeat."""

    if not settings.QUIZ_TASKS_ENABLED:
        return True
    expected_source = "process" if settings.QUIZ_EMBEDDED_WORKER_ENABLED else "redis"
    if snapshot.get("source") != expected_source:
        return False
    processors = snapshot.get("processors")
    if not isinstance(processors, dict) or set(processors) != set(quiz_task_registry.names):
        return False
    heartbeat = _parse_metric_time(snapshot.get("heartbeat_at"))
    if heartbeat is None:
        return False
    age = (_utc_now() - heartbeat).total_seconds()
    return 0 <= age <= QUIZ_TASK_RUNTIME.stale_seconds


def quiz_task_snapshot_signals(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Derive alert-friendly, content-free signals from a worker snapshot."""

    now = _utc_now()
    processors_raw = snapshot.get("processors")
    processors = processors_raw if isinstance(processors_raw, dict) else {}
    heartbeat_age = _metric_age_seconds(snapshot.get("heartbeat_at"), now=now)
    stale = heartbeat_age is None or heartbeat_age > QUIZ_TASK_RUNTIME.stale_seconds
    total_queue_depth = 0
    total_failures = 0
    stuck_processors: list[str] = []

    for name, metric_raw in processors.items():
        if not isinstance(metric_raw, dict):
            continue
        queue_depth = max(0, int(metric_raw.get("queue_depth") or 0))
        failures = max(0, int(metric_raw.get("failures") or 0))
        total_queue_depth += queue_depth
        total_failures += failures
        processor_age = _metric_age_seconds(
            metric_raw.get("last_heartbeat_at"),
            now=now,
        )
        if queue_depth > 0 and (
            processor_age is None
            or processor_age > QUIZ_TASK_RUNTIME.stale_seconds
        ):
            stuck_processors.append(str(name))

    stats = processors.get("quiz-question-stats")
    stats_queue_depth = (
        max(0, int(stats.get("queue_depth") or 0))
        if isinstance(stats, dict)
        else 0
    )
    stats_lag_seconds = (
        _metric_age_seconds(stats.get("last_finished_at"), now=now)
        if isinstance(stats, dict)
        else None
    )
    stats_lagging = stats_queue_depth > 0 and (
        stats_lag_seconds is None or stats_lag_seconds > 60
    )

    def queue_depth(name: str) -> int:
        metric = processors.get(name)
        return (
            max(0, int(metric.get("queue_depth") or 0))
            if isinstance(metric, dict)
            else 0
        )

    return {
        "ready": quiz_task_snapshot_ready(snapshot),
        "stale": stale,
        "heartbeat_age_seconds": heartbeat_age,
        "total_queue_depth": total_queue_depth,
        "total_failures": total_failures,
        "stuck_processors": sorted(stuck_processors),
        "stats_lag_seconds": stats_lag_seconds,
        "stats_lagging": stats_lagging,
        "exam_timeout_queue_depth": queue_depth("quiz-exam-timeout"),
        "oss_cleanup_queue_depth": queue_depth("quiz-import-cleanup"),
    }


def enrich_quiz_task_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(snapshot)
    enriched["signals"] = quiz_task_snapshot_signals(enriched)
    return enriched


async def _cleanup_expired_imports() -> bool:
    from app.services.admin_quiz import AdminQuizService

    return await AdminQuizService().cleanup_expired_import_job()


quiz_task_registry.register(
    "quiz-import-cleanup",
    _cleanup_expired_imports,
    queue_depth=_import_cleanup_queue_depth,
)


async def _settle_expired_exams() -> bool:
    """Claim and settle a bounded batch of exams whose deadline has passed."""

    from app.services.quiz_exam import QuizExamService

    settled = await QuizExamService().settle_expired_exams()
    return settled > 0


quiz_task_registry.register(
    "quiz-exam-timeout",
    _settle_expired_exams,
    queue_depth=_exam_timeout_queue_depth,
)


_last_question_stats_run: float | None = None


async def _aggregate_question_stats() -> bool:
    global _last_question_stats_run

    loop_time = asyncio.get_running_loop().time()
    if (
        _last_question_stats_run is not None
        and loop_time - _last_question_stats_run < QUIZ_TASK_RUNTIME.poll_seconds
    ):
        return False
    _last_question_stats_run = loop_time

    from app.services.admin_quiz import AdminQuizService

    await AdminQuizService().aggregate_question_stats()
    return False


quiz_task_registry.register(
    "quiz-question-stats",
    _aggregate_question_stats,
    queue_depth=_question_stats_queue_depth,
)


async def ensure_quiz_runtime_ready() -> None:
    """Fail production startup when the shared Redis dependency is unavailable."""

    if not settings.QUIZ_TASKS_ENABLED or settings.APP_ENV != "production":
        return
    if not await redis_ping():
        raise RuntimeError("Redis is required for quiz tasks in production")


async def quiz_worker_loop(registry: QuizTaskRegistry | None = None) -> None:
    active_registry = registry or quiz_task_registry
    loop = asyncio.get_running_loop()
    last_heartbeat = loop.time()
    logger.info(
        "quiz task loop started: processors=%s poll_seconds=%d",
        active_registry.names,
        QUIZ_TASK_RUNTIME.poll_seconds,
    )
    while True:
        did_work = await active_registry.run_once()
        try:
            await publish_quiz_task_snapshot(active_registry)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "quiz task metrics publish failed: exception_type=%s",
                type(exc).__name__,
            )
        current = loop.time()
        if current - last_heartbeat >= QUIZ_TASK_RUNTIME.heartbeat_seconds:
            active_registry.heartbeat()
            logger.info(
                "quiz task heartbeat: processors=%s did_work=%s metrics=%s",
                active_registry.names,
                did_work,
                active_registry.snapshot(),
            )
            last_heartbeat = current
        await asyncio.sleep(0 if did_work else QUIZ_TASK_RUNTIME.poll_seconds)


__all__ = [
    "QUIZ_TASK_METRICS_KEY",
    "QUIZ_TASK_RUNTIME",
    "QuizTaskMetric",
    "QuizTaskRegistry",
    "enrich_quiz_task_snapshot",
    "ensure_quiz_runtime_ready",
    "publish_quiz_task_snapshot",
    "quiz_task_snapshot_ready",
    "quiz_task_snapshot_signals",
    "quiz_task_registry",
    "read_quiz_task_snapshot",
    "quiz_worker_loop",
]
