"""Acceptance checks for QF-52 quiz API and worker Prometheus metrics."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from prometheus_client import CollectorRegistry

from app.main import app
from app.port.config import settings
from app.services.quiz_metrics import (
    QuizMetrics,
    QuizMetricsMiddleware,
    normalize_quiz_route,
)
from app.services.quiz_tasks import quiz_task_registry


def test_quiz_route_labels_are_contract_templates_not_resource_ids() -> None:
    assert normalize_quiz_route("GET", "/api/quiz/exams/current") == (
        "user",
        "/api/quiz/exams/current",
    )
    assert normalize_quiz_route("PUT", "/api/quiz/exams/17/answers/29") == (
        "user",
        "/api/quiz/exams/{exam_id}/answers/{exam_question_id}",
    )
    assert normalize_quiz_route("DELETE", "/admin/quiz/questions/991") == (
        "admin",
        "/admin/quiz/questions/{question_id}",
    )
    assert normalize_quiz_route("GET", "/api/users/17") is None


@pytest.mark.asyncio
async def test_middleware_counts_status_latency_and_unmatched_without_raw_id() -> None:
    metrics = QuizMetrics(CollectorRegistry())
    test_app = FastAPI()

    @test_app.get("/api/quiz/exams/{exam_id}")
    async def exam(exam_id: int):
        return {"id": exam_id}

    @test_app.get("/api/quiz/custom/{raw_id}")
    async def custom(raw_id: str):
        return {"id": raw_id}

    test_app.add_middleware(QuizMetricsMiddleware, metrics=metrics)
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        assert (await client.get("/api/quiz/exams/123456")).status_code == 200
        assert (await client.get("/api/quiz/custom/private-id")).status_code == 200

    output = metrics.render().decode()
    assert 'route="/api/quiz/exams/{exam_id}"' in output
    assert 'route="/api/quiz/_unmatched"' in output
    assert 'status_code="200"' in output
    assert "quiz_api_request_duration_seconds_bucket" in output
    assert "123456" not in output
    assert "private-id" not in output


def test_worker_metrics_export_only_aggregate_processor_signals() -> None:
    metrics = QuizMetrics(CollectorRegistry())
    now = datetime.now(timezone.utc).isoformat()
    processors = {
        name: {
            "queue_depth": 2 if name == "quiz-import" else 0,
            "failures": 1,
            "retries": 1,
            "last_runtime_seconds": 0.125,
        }
        for name in quiz_task_registry.names
    }
    metrics.update_worker(
        {
            "source": "redis",
            "processors": processors,
            "heartbeat_at": now,
            "signals": {
                "ready": True,
                "heartbeat_age_seconds": 0.5,
                "stuck_processors": [],
                "stats_lag_seconds": 12,
                "stats_lagging": False,
                "exam_timeout_queue_depth": 3,
                "oss_cleanup_queue_depth": 4,
            },
        }
    )

    output = metrics.render().decode()
    assert 'quiz_worker_info{source="redis"} 1.0' in output
    assert "quiz_worker_ready 1.0" in output
    assert 'quiz_worker_processor_queue_depth{processor="quiz-import"} 2.0' in output
    assert "quiz_worker_exam_timeout_queue_depth 3.0" in output
    assert "quiz_worker_oss_cleanup_queue_depth 4.0" in output
    assert "question_text" not in output


@pytest.mark.asyncio
async def test_internal_metrics_requires_dedicated_token_and_refreshes_worker(
    monkeypatch,
) -> None:
    token = "test-metrics-token-that-is-at-least-32-characters"
    monkeypatch.setattr(settings, "QUIZ_METRICS_ENABLED", True)
    monkeypatch.setattr(settings, "QUIZ_METRICS_BEARER_TOKEN", token)
    processors = {
        name: {
            "queue_depth": 0,
            "failures": 0,
            "retries": 0,
            "last_runtime_seconds": None,
            "last_heartbeat_at": datetime.now(timezone.utc).isoformat(),
            "last_finished_at": datetime.now(timezone.utc).isoformat(),
        }
        for name in quiz_task_registry.names
    }
    monkeypatch.setattr(
        "app.main.read_quiz_task_snapshot",
        AsyncMock(
            return_value={
                "source": "process",
                "heartbeat_at": datetime.now(timezone.utc).isoformat(),
                "processors": processors,
            }
        ),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        missing = await client.get("/internal/metrics")
        wrong = await client.get(
            "/internal/metrics", headers={"Authorization": "Bearer wrong"}
        )
        allowed = await client.get(
            "/internal/metrics", headers={"Authorization": f"Bearer {token}"}
        )

    assert missing.status_code == wrong.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert allowed.status_code == 200
    assert allowed.headers["content-type"].startswith("text/plain")
    assert "quiz_api_requests_total" in allowed.text
    assert "quiz_worker_ready 1.0" in allowed.text
    assert token not in allowed.text
