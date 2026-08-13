"""Low-cardinality Prometheus metrics for the frozen quiz API and worker.

Only normalized route templates, HTTP methods/statuses and aggregate worker
counters are exported.  User IDs, administrator IDs, question content,
object keys and request parameters never become metric labels.
"""

from __future__ import annotations

import re
import time
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from prometheus_client import generate_latest

from app.contracts.quiz import QUIZ_API_CONTRACTS


_DYNAMIC_SEGMENT = re.compile(r"\{[^{}]+\}")
_KNOWN_SOURCES = {"process", "redis", "disabled", "unavailable"}


def _compile_route(template: str) -> re.Pattern[str]:
    cursor = 0
    parts: list[str] = []
    for match in _DYNAMIC_SEGMENT.finditer(template):
        parts.append(re.escape(template[cursor : match.start()]))
        parts.append(r"[^/]+")
        cursor = match.end()
    parts.append(re.escape(template[cursor:]))
    return re.compile("^" + "".join(parts) + "$")


# Static paths must precede parameterized siblings such as ``/exams/{id}``.
_ROUTES = tuple(
    (
        contract.method.upper(),
        contract.path,
        _compile_route(contract.path),
    )
    for contract in sorted(
        QUIZ_API_CONTRACTS,
        key=lambda item: (
            item.path.count("{"),
            -len(item.path),
            item.method,
        ),
    )
)


def normalize_quiz_route(method: str, path: str) -> tuple[str, str] | None:
    """Return ``(surface, template)`` without ever using a raw ID as a label."""

    if path.startswith("/admin/quiz"):
        surface = "admin"
        unmatched = "/admin/quiz/_unmatched"
    elif path.startswith("/api/quiz"):
        surface = "user"
        unmatched = "/api/quiz/_unmatched"
    else:
        return None

    normalized_method = method.upper()
    # Prefer a contract with the same method.  A second path-only pass keeps
    # 405 responses attributable to the normalized resource template.
    for route_method, template, pattern in _ROUTES:
        if route_method == normalized_method and pattern.fullmatch(path):
            return surface, template
    for _route_method, template, pattern in _ROUTES:
        if pattern.fullmatch(path):
            return surface, template
    return surface, unmatched


def _metric_number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class QuizMetrics:
    """Own the quiz metric registry so tests and applications can isolate it."""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry(auto_describe=True)
        request_labels = ("surface", "method", "route", "status_code")
        route_labels = ("surface", "method", "route")
        self.requests = Counter(
            "quiz_api_requests_total",
            "Completed quiz API requests by normalized route and status.",
            request_labels,
            registry=self.registry,
        )
        self.duration = Histogram(
            "quiz_api_request_duration_seconds",
            "End-to-end quiz API response duration in seconds.",
            route_labels,
            buckets=(
                0.01,
                0.025,
                0.05,
                0.1,
                0.2,
                0.3,
                0.5,
                0.75,
                1.0,
                2.0,
                5.0,
                10.0,
            ),
            registry=self.registry,
        )
        self.in_progress = Gauge(
            "quiz_api_in_progress_requests",
            "Quiz API requests currently being handled.",
            route_labels,
            registry=self.registry,
        )

        self.worker_ready = Gauge(
            "quiz_worker_ready",
            "Whether the configured quiz worker snapshot is complete and fresh.",
            registry=self.registry,
        )
        self.worker_info = Gauge(
            "quiz_worker_info",
            "Current source of the quiz worker snapshot.",
            ("source",),
            registry=self.registry,
        )
        self.worker_heartbeat_age = Gauge(
            "quiz_worker_heartbeat_age_seconds",
            "Age of the shared worker heartbeat; -1 means unavailable.",
            registry=self.registry,
        )
        self.worker_queue_depth = Gauge(
            "quiz_worker_processor_queue_depth",
            "Pending work reported by each quiz processor.",
            ("processor",),
            registry=self.registry,
        )
        self.worker_failures = Gauge(
            "quiz_worker_processor_failures",
            "Process-lifetime failures reported by each quiz processor.",
            ("processor",),
            registry=self.registry,
        )
        self.worker_retries = Gauge(
            "quiz_worker_processor_retries",
            "Process-lifetime retries reported by each quiz processor.",
            ("processor",),
            registry=self.registry,
        )
        self.worker_last_runtime = Gauge(
            "quiz_worker_processor_last_runtime_seconds",
            "Runtime of the most recent processor iteration; -1 means unknown.",
            ("processor",),
            registry=self.registry,
        )
        self.worker_stuck = Gauge(
            "quiz_worker_stuck_processors",
            "Number of processors with backlog and a stale heartbeat.",
            registry=self.registry,
        )
        self.worker_stats_lag = Gauge(
            "quiz_worker_stats_lag_seconds",
            "Age of the most recent statistics aggregation; -1 means unknown.",
            registry=self.registry,
        )
        self.worker_stats_lagging = Gauge(
            "quiz_worker_stats_lagging",
            "Whether pending statistics have exceeded the one-minute SLA.",
            registry=self.registry,
        )
        self.worker_exam_timeout_queue = Gauge(
            "quiz_worker_exam_timeout_queue_depth",
            "Expired in-progress exams waiting for settlement.",
            registry=self.registry,
        )
        self.worker_oss_cleanup_queue = Gauge(
            "quiz_worker_oss_cleanup_queue_depth",
            "Expired import artifacts waiting for OSS cleanup.",
            registry=self.registry,
        )

    def request_started(self, surface: str, method: str, route: str) -> None:
        self.in_progress.labels(surface, method, route).inc()

    def request_finished(
        self,
        surface: str,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        labels = (surface, method, route)
        self.in_progress.labels(*labels).dec()
        self.duration.labels(*labels).observe(max(0.0, duration_seconds))
        self.requests.labels(*labels, str(status_code)).inc()

    def update_worker(self, snapshot: dict[str, Any]) -> None:
        """Replace worker gauges from one safe `/health`-style snapshot."""

        signals_raw = snapshot.get("signals")
        signals = signals_raw if isinstance(signals_raw, dict) else {}
        source_raw = str(snapshot.get("source") or "unavailable")
        source = source_raw if source_raw in _KNOWN_SOURCES else "unavailable"
        self.worker_info.clear()
        self.worker_info.labels(source).set(1)
        self.worker_ready.set(1 if signals.get("ready") is True else 0)
        self.worker_heartbeat_age.set(
            _metric_number(signals.get("heartbeat_age_seconds"), -1.0)
        )
        self.worker_stuck.set(len(signals.get("stuck_processors") or ()))
        self.worker_stats_lag.set(
            _metric_number(signals.get("stats_lag_seconds"), -1.0)
        )
        self.worker_stats_lagging.set(
            1 if signals.get("stats_lagging") is True else 0
        )
        self.worker_exam_timeout_queue.set(
            max(0.0, _metric_number(signals.get("exam_timeout_queue_depth")))
        )
        self.worker_oss_cleanup_queue.set(
            max(0.0, _metric_number(signals.get("oss_cleanup_queue_depth")))
        )

        self.worker_queue_depth.clear()
        self.worker_failures.clear()
        self.worker_retries.clear()
        self.worker_last_runtime.clear()
        processors_raw = snapshot.get("processors")
        processors = processors_raw if isinstance(processors_raw, dict) else {}
        for name, raw_metric in processors.items():
            if not isinstance(raw_metric, dict):
                continue
            processor = str(name)
            self.worker_queue_depth.labels(processor).set(
                max(0.0, _metric_number(raw_metric.get("queue_depth")))
            )
            self.worker_failures.labels(processor).set(
                max(0.0, _metric_number(raw_metric.get("failures")))
            )
            self.worker_retries.labels(processor).set(
                max(0.0, _metric_number(raw_metric.get("retries")))
            )
            self.worker_last_runtime.labels(processor).set(
                _metric_number(raw_metric.get("last_runtime_seconds"), -1.0)
            )

    def render(self) -> bytes:
        return generate_latest(self.registry)


quiz_metrics = QuizMetrics()


class QuizMetricsMiddleware:
    """ASGI middleware that also sees responses produced by SlowAPI."""

    def __init__(self, app, metrics: QuizMetrics | None = None) -> None:
        self.app = app
        self.metrics = metrics or quiz_metrics

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        route = normalize_quiz_route(
            str(scope.get("method") or "GET"),
            str(scope.get("path") or ""),
        )
        if route is None:
            await self.app(scope, receive, send)
            return

        surface, template = route
        method = str(scope.get("method") or "GET").upper()
        status_code = 500
        response_started = False
        started = time.perf_counter()
        self.metrics.request_started(surface, method, template)

        async def send_with_status(message) -> None:
            nonlocal response_started, status_code
            if message.get("type") == "http.response.start":
                response_started = True
                status_code = int(message.get("status") or 500)
            await send(message)

        try:
            await self.app(scope, receive, send_with_status)
        except BaseException as exc:
            # Client disconnect/cancellation is conventionally represented as
            # 499; all other unhandled failures count as 500.
            if type(exc).__name__ == "CancelledError" and not response_started:
                status_code = 499
            raise
        finally:
            self.metrics.request_finished(
                surface,
                method,
                template,
                status_code,
                time.perf_counter() - started,
            )


__all__ = [
    "QuizMetrics",
    "QuizMetricsMiddleware",
    "normalize_quiz_route",
    "quiz_metrics",
]
