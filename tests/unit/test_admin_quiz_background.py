from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

import app.services.quiz_tasks as quiz_tasks
from app.domain.community.src.model.quiz import QuizImportJob
from app.port.config import settings
from app.port.exceptions import ThirdPartyException
from app.services.admin_quiz import AdminQuizService
from app.services.quiz_tasks import (
    QUIZ_TASK_METRICS_KEY,
    QUIZ_TASK_RUNTIME,
    QuizTaskRegistry,
    publish_quiz_task_snapshot,
    quiz_task_snapshot_signals,
    quiz_task_snapshot_ready,
    quiz_task_registry,
    read_quiz_task_snapshot,
)


class _ObjectResult:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


class _FakeBucket:
    def __init__(self, *, upload_status: int = 200, sign_error: bool = False) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.signed: list[tuple[str, str, int, dict | None]] = []
        self.upload_status = upload_status
        self.sign_error = sign_error

    def put_object(self, key: str, data: bytes, headers=None):
        self.objects[key] = data
        return SimpleNamespace(status=self.upload_status)

    def get_object(self, key: str) -> _ObjectResult:
        return _ObjectResult(self.objects[key])

    def delete_object(self, key: str) -> None:
        self.deleted.append(key)
        self.objects.pop(key, None)

    def sign_url(self, method: str, key: str, expires: int, params=None) -> str:
        if self.sign_error:
            raise RuntimeError("simulated signing failure")
        self.signed.append((method, key, expires, params))
        return f"https://private.example/{key}?expires={expires}"


@pytest.mark.asyncio
async def test_quiz_oss_adapter_uploads_reads_signs_and_deletes(monkeypatch) -> None:
    async def run_inline(func, *args, **kwargs):
        return func(*args, **kwargs)

    bucket = _FakeBucket()
    monkeypatch.setattr(settings, "QUIZ_IMPORT_STORAGE_TYPE", "aliyun_oss")
    monkeypatch.setattr("app.services.admin_quiz.asyncio.to_thread", run_inline)
    monkeypatch.setattr(
        AdminQuizService,
        "_quiz_oss_bucket",
        staticmethod(lambda: bucket),
    )
    service = AdminQuizService()

    await service._put_import_object("quiz-imports/batch.csv", b"payload", "text/csv")
    assert await service._get_import_object("quiz-imports/batch.csv") == b"payload"

    job = SimpleNamespace(
        id=7,
        admin_id=3,
        report_object_key="quiz-imports/report.json",
    )
    url = await service._signed_import_url(
        job,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=3),
    )
    assert url.startswith("https://private.example/quiz-imports/report.json")
    assert bucket.signed[0][0:2] == ("GET", "quiz-imports/report.json")
    assert 1 <= bucket.signed[0][2] <= 180
    assert bucket.signed[0][3] == {
        "response-content-disposition": (
            'attachment; filename="quiz-import-7-errors.json"'
        )
    }

    await service._delete_import_object("quiz-imports/batch.csv")
    assert bucket.deleted == ["quiz-imports/batch.csv"]


@pytest.mark.asyncio
async def test_quiz_oss_adapter_maps_upload_and_sign_failures(monkeypatch) -> None:
    async def run_inline(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(settings, "QUIZ_IMPORT_STORAGE_TYPE", "aliyun_oss")
    monkeypatch.setattr("app.services.admin_quiz.asyncio.to_thread", run_inline)
    service = AdminQuizService()

    monkeypatch.setattr(
        AdminQuizService,
        "_quiz_oss_bucket",
        staticmethod(lambda: _FakeBucket(upload_status=500)),
    )
    with pytest.raises(ThirdPartyException, match="上传"):
        await service._put_import_object(
            "quiz-imports/batch.csv",
            b"payload",
            "text/csv",
        )

    monkeypatch.setattr(
        AdminQuizService,
        "_quiz_oss_bucket",
        staticmethod(lambda: _FakeBucket(sign_error=True)),
    )
    with pytest.raises(ThirdPartyException, match="生成错误报告地址"):
        await service._signed_import_url(
            SimpleNamespace(id=8, report_object_key="quiz-imports/report.json"),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        )


@pytest.mark.asyncio
async def test_disabled_quiz_oss_never_falls_back_to_local_storage(monkeypatch) -> None:
    monkeypatch.setattr(settings, "QUIZ_IMPORT_STORAGE_TYPE", "disabled")
    service = AdminQuizService()

    with pytest.raises(ThirdPartyException, match="题库 OSS 未配置"):
        await service._put_import_object(
            "quiz-imports/batch.csv",
            b"payload",
            "text/csv",
        )
    with pytest.raises(ThirdPartyException, match="题库 OSS 未配置"):
        await service._get_import_object("quiz-imports/batch.csv")


@pytest.mark.asyncio
async def test_local_quiz_import_storage_maps_filesystem_failure(monkeypatch) -> None:
    async def run_inline(func, *args, **kwargs):
        return func(*args, **kwargs)

    def deny_write(_data):
        raise PermissionError("sensitive local path")

    monkeypatch.setattr(settings, "QUIZ_IMPORT_STORAGE_TYPE", "local")
    monkeypatch.setattr("app.services.admin_quiz.asyncio.to_thread", run_inline)
    monkeypatch.setattr(
        AdminQuizService,
        "_local_import_path",
        staticmethod(lambda _key: SimpleNamespace(
            parent=SimpleNamespace(mkdir=lambda **_kwargs: None),
            write_bytes=deny_write,
        )),
    )

    with pytest.raises(ThirdPartyException, match="题库导入存储不可用") as error:
        await AdminQuizService()._put_import_object(
            "quiz-imports/batch.json",
            b"{}",
            "application/json",
        )

    assert error.value.http_status_code == 502
    assert "sensitive local path" not in error.value.message


@pytest.mark.asyncio
async def test_local_quiz_import_storage_maps_read_and_delete_failures(
    monkeypatch,
) -> None:
    async def run_inline(func, *args, **kwargs):
        return func(*args, **kwargs)

    def deny_read():
        raise PermissionError("sensitive read path")

    def deny_delete():
        raise PermissionError("sensitive delete path")

    path = SimpleNamespace(
        is_file=lambda: True,
        read_bytes=deny_read,
        unlink=deny_delete,
    )
    monkeypatch.setattr(settings, "QUIZ_IMPORT_STORAGE_TYPE", "local")
    monkeypatch.setattr("app.services.admin_quiz.asyncio.to_thread", run_inline)
    monkeypatch.setattr(
        AdminQuizService,
        "_local_import_path",
        staticmethod(lambda _key: path),
    )
    service = AdminQuizService()

    with pytest.raises(ThirdPartyException, match="题库导入存储不可用"):
        await service._get_import_object("quiz-imports/batch.json")
    with pytest.raises(ThirdPartyException, match="题库导入存储不可用"):
        await service._delete_import_object("quiz-imports/batch.json")


def test_expired_import_job_never_advertises_report() -> None:
    expired = QuizImportJob(
        report_object_key="quiz-imports/report.json",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    active = QuizImportJob(
        report_object_key="quiz-imports/report.json",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )

    assert expired.report_available is False
    assert active.report_available is True


def test_quiz_signed_url_ttl_has_hard_300_second_ceiling() -> None:
    from pydantic import ValidationError
    from app.port.config import Settings

    with pytest.raises(ValidationError, match="between 1 and 300"):
        Settings(QUIZ_OSS_SIGNED_URL_TTL_SECONDS=301)


def test_csv_parser_rejects_more_than_five_thousand_rows() -> None:
    header = "category_path,question_type,question_text,options,correct_answer,explanation"
    row = '"[""分类""]",single_choice,题目,"{""A"":""一"",""B"":""二"",""C"":""三""}",A,解析'
    content = (header + "\n" + "\n".join([row] * 5001) + "\n").encode()

    rows, errors = AdminQuizService()._parse_import_rows("csv", content)

    assert len(rows) == 5000
    assert any("单批最多 5000 道" in str(error["message"]) for error in errors)


def test_import_parser_preserves_domain_error_field_without_sensitive_value() -> None:
    content = json.dumps(
        {
            "questions": [
                {
                    "category_path": ["判断题"],
                    "question_type": "judge",
                    "question_text": "TCP 是无连接协议。",
                    "options": {"A": "错误", "B": "错误"},
                    "correct_answer": "A",
                    "explanation": None,
                }
            ]
        },
        ensure_ascii=False,
    ).encode()

    rows, errors = AdminQuizService()._parse_import_rows("json", content)

    assert rows == []
    assert len(errors) == 1
    assert errors[0]["field"] == "options"
    assert errors[0]["message"] == "判断题固定为 A=正确、B=错误"
    assert "TCP 是无连接协议" not in json.dumps(errors, ensure_ascii=False)

    _rows, path_errors = AdminQuizService()._parse_import_rows(
        "json",
        json.dumps(
            {
                "questions": [
                    {
                        "category_path": [" "],
                        "question_type": "judge",
                        "question_text": "分类路径错误。",
                        "options": {"A": "正确", "B": "错误"},
                        "correct_answer": "A",
                        "explanation": None,
                    }
                ]
            },
            ensure_ascii=False,
        ).encode(),
    )
    assert path_errors[0]["field"] == "category_path"


def test_quiz_worker_registers_cleanup_and_stats_with_subminute_polling() -> None:
    assert quiz_task_registry.names == (
        "quiz-import",
        "quiz-import-cleanup",
        "quiz-exam-timeout",
        "quiz-question-stats",
        "course-entitlement-jobs",
    )
    assert QUIZ_TASK_RUNTIME.poll_seconds <= 60


@pytest.mark.asyncio
async def test_question_stats_processor_is_periodic_not_a_busy_loop(monkeypatch) -> None:
    calls = 0

    async def aggregate(_self) -> bool:
        nonlocal calls
        calls += 1
        return True

    monkeypatch.setattr(AdminQuizService, "aggregate_question_stats", aggregate)
    monkeypatch.setattr(quiz_tasks, "_last_question_stats_run", None)
    processor = quiz_task_registry._processors["quiz-question-stats"]

    assert await processor() is False
    assert await processor() is False
    assert calls == 1


@pytest.mark.asyncio
async def test_quiz_worker_loop_survives_metrics_publish_failure(monkeypatch) -> None:
    registry = QuizTaskRegistry()
    calls = 0

    async def processor() -> bool:
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise asyncio.CancelledError
        return False

    async def publish(_registry) -> None:
        raise RuntimeError("redis unavailable")

    async def no_wait(_seconds: float) -> None:
        return None

    registry.register("processor", processor)
    monkeypatch.setattr(quiz_tasks, "publish_quiz_task_snapshot", publish)
    monkeypatch.setattr(quiz_tasks.asyncio, "sleep", no_wait)

    with pytest.raises(asyncio.CancelledError):
        await quiz_tasks.quiz_worker_loop(registry)
    assert calls == 2


@pytest.mark.asyncio
async def test_exam_timeout_processor_reports_work(monkeypatch) -> None:
    calls: list[int] = []

    async def settle(self):
        del self
        calls.append(1)
        return 2

    monkeypatch.setattr(
        "app.services.quiz_exam.QuizExamService.settle_expired_exams",
        settle,
    )
    processor = quiz_task_registry._processors["quiz-exam-timeout"]

    assert await processor() is True
    assert calls == [1]


@pytest.mark.asyncio
async def test_worker_metrics_are_published_and_read_from_shared_redis(
    monkeypatch,
) -> None:
    class _FakeRedis:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}
            self.ttls: dict[str, int] = {}

        async def setex(self, key: str, ttl: int, value: str) -> None:
            self.values[key] = value
            self.ttls[key] = ttl

        async def get(self, key: str) -> str | None:
            return self.values.get(key)

    redis = _FakeRedis()
    monkeypatch.setattr("app.adapter.redis.redis_client", redis)
    monkeypatch.setattr(settings, "QUIZ_TASKS_ENABLED", True)
    monkeypatch.setattr(settings, "QUIZ_EMBEDDED_WORKER_ENABLED", False)
    registry = QuizTaskRegistry()

    async def processor() -> bool:
        return True

    registry.register("shared", processor)
    await registry.run_once()
    await publish_quiz_task_snapshot(registry)

    stored = json.loads(redis.values[QUIZ_TASK_METRICS_KEY])
    assert stored["source"] == "redis"
    assert redis.ttls[QUIZ_TASK_METRICS_KEY] >= QUIZ_TASK_RUNTIME.stale_seconds
    snapshot = await read_quiz_task_snapshot()
    assert snapshot["source"] == "redis"
    assert snapshot["processors"]["shared"]["successes"] == 1


@pytest.mark.asyncio
async def test_web_probe_marks_missing_shared_worker_metrics_unavailable(
    monkeypatch,
) -> None:
    class _EmptyRedis:
        async def get(self, key: str) -> None:
            del key
            return None

    monkeypatch.setattr("app.adapter.redis.redis_client", _EmptyRedis())
    monkeypatch.setattr(settings, "QUIZ_TASKS_ENABLED", True)
    monkeypatch.setattr(settings, "QUIZ_EMBEDDED_WORKER_ENABLED", False)

    snapshot = await read_quiz_task_snapshot()
    assert snapshot == {
        "source": "unavailable",
        "heartbeat_at": None,
        "processors": {},
    }
    assert quiz_task_snapshot_ready(snapshot) is False


def test_worker_readiness_requires_all_processors_and_fresh_heartbeat(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "QUIZ_TASKS_ENABLED", True)
    monkeypatch.setattr(settings, "QUIZ_EMBEDDED_WORKER_ENABLED", False)
    fresh = {
        "source": "redis",
        "heartbeat_at": datetime.now(timezone.utc).isoformat(),
        "processors": {
            name: {"name": name}
            for name in quiz_task_registry.names
        },
    }
    assert quiz_task_snapshot_ready(fresh) is True

    stale = dict(fresh)
    stale["heartbeat_at"] = (
        datetime.now(timezone.utc)
        - timedelta(seconds=QUIZ_TASK_RUNTIME.stale_seconds + 1)
    ).isoformat()
    assert quiz_task_snapshot_ready(stale) is False

    incomplete = dict(fresh)
    incomplete["processors"] = {"quiz-import": {"name": "quiz-import"}}
    assert quiz_task_snapshot_ready(incomplete) is False


def test_worker_signals_expose_queue_failure_stuck_and_lag_without_content(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "QUIZ_TASKS_ENABLED", True)
    monkeypatch.setattr(settings, "QUIZ_EMBEDDED_WORKER_ENABLED", False)
    now = datetime.now(timezone.utc)
    stale = (now - timedelta(seconds=QUIZ_TASK_RUNTIME.stale_seconds + 1)).isoformat()
    old_stats = (now - timedelta(seconds=61)).isoformat()
    processors = {
        name: {
            "name": name,
            "queue_depth": 0,
            "failures": 0,
            "last_heartbeat_at": now.isoformat(),
            "last_finished_at": now.isoformat(),
        }
        for name in quiz_task_registry.names
    }
    processors["quiz-exam-timeout"].update(
        queue_depth=2,
        failures=1,
        last_heartbeat_at=stale,
    )
    processors["quiz-import-cleanup"]["queue_depth"] = 3
    processors["quiz-question-stats"].update(
        queue_depth=4,
        last_finished_at=old_stats,
    )
    snapshot = {
        "source": "redis",
        "heartbeat_at": now.isoformat(),
        "processors": processors,
    }

    signals = quiz_task_snapshot_signals(snapshot)
    assert signals["ready"] is True
    assert signals["total_queue_depth"] == 9
    assert signals["total_failures"] == 1
    assert signals["stuck_processors"] == ["quiz-exam-timeout"]
    assert signals["stats_lagging"] is True
    assert signals["stats_lag_seconds"] >= 60
    assert signals["exam_timeout_queue_depth"] == 2
    assert signals["oss_cleanup_queue_depth"] == 3
    assert "question" not in str(signals).lower()
