from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import app.services.quiz_tasks as quiz_tasks
from app.domain.community.src.model.quiz import QuizImportJob
from app.port.config import settings
from app.port.exceptions import ThirdPartyException
from app.services.admin_quiz import AdminQuizService
from app.services.quiz_tasks import QUIZ_TASK_RUNTIME, quiz_task_registry


class _ObjectResult:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


class _FakeBucket:
    def __init__(self, *, upload_status: int = 200, sign_error: bool = False) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.signed: list[tuple[str, str, int]] = []
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

    def sign_url(self, method: str, key: str, expires: int) -> str:
        if self.sign_error:
            raise RuntimeError("simulated signing failure")
        self.signed.append((method, key, expires))
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
            SimpleNamespace(report_object_key="quiz-imports/report.json"),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        )


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


def test_csv_parser_rejects_more_than_five_thousand_rows() -> None:
    header = "category_path,question_type,question_text,options,correct_answer,explanation"
    row = '"[""分类""]",single_choice,题目,"{""A"":""一"",""B"":""二"",""C"":""三""}",A,解析'
    content = (header + "\n" + "\n".join([row] * 5001) + "\n").encode()

    rows, errors = AdminQuizService()._parse_import_rows("csv", content)

    assert len(rows) == 5000
    assert any("单批最多 5000 道" in str(error["message"]) for error in errors)


def test_quiz_worker_registers_cleanup_and_stats_with_subminute_polling() -> None:
    assert quiz_task_registry.names == (
        "quiz-import",
        "quiz-import-cleanup",
        "quiz-exam-timeout",
        "quiz-question-stats",
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
