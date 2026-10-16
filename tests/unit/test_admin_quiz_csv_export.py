"""题库题目 CSV 导出：表头/路径/答案格式与导入兼容。"""

from contextlib import asynccontextmanager
from csv import DictReader
from io import StringIO
from types import SimpleNamespace

import pytest

from app.schemas.admin_quiz_contract import AdminQuizQuestionQuery
from app.services import admin_quiz_v2 as service_module
from app.services.admin_quiz_v2 import AdminQuizV2Service
from app.port.exceptions import ValidationException


def _question(
    question_id: int,
    question_type: str,
    correct_answer: object,
    *,
    option_images: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=question_id,
        knowledge_point_id=11,
        question_type=question_type,
        question_text=f"题干{question_id}",
        options={"A": "选项A", "B": "选项B"},
        correct_answer=correct_answer,
        explanation="解析",
        option_image_urls=option_images or {},
    )


class _SequenceDB:
    """execute 按入队顺序返回结果：题目 → 知识点 → 模块。"""

    def __init__(self, results: list[SimpleNamespace]):
        self._results = results

    async def execute(self, statement):
        return self._results.pop(0)


def _point() -> SimpleNamespace:
    return SimpleNamespace(id=11, module_id=21, name="攻击原理")


def _module() -> SimpleNamespace:
    return SimpleNamespace(id=21, name="网络安全")


@pytest.mark.asyncio
async def test_export_requires_library(monkeypatch) -> None:
    @asynccontextmanager
    async def _context():
        raise AssertionError("不应触达数据库")

        yield None

    monkeypatch.setattr(service_module, "get_db_ctx", _context)
    with pytest.raises(ValidationException):
        await AdminQuizV2Service().export_questions_csv(AdminQuizQuestionQuery())


@pytest.mark.asyncio
async def test_export_six_column_csv_round_trips(monkeypatch) -> None:
    questions = [
        _question(1, "single_choice", "A"),
        _question(2, "multiple_choice", ["A", "B"]),
        _question(3, "fill_blank", [["SYN", "同步"], ["ACK"]]),
        _question(4, "judge", "A"),
    ]
    db = _SequenceDB([SimpleNamespace(scalars=lambda: iter(questions)),
                      SimpleNamespace(scalars=lambda: iter([_point()])),
                      SimpleNamespace(scalars=lambda: iter([_module()]))])

    @asynccontextmanager
    async def _context():
        yield db

    monkeypatch.setattr(service_module, "get_db_ctx", _context)
    csv_text = await AdminQuizV2Service().export_questions_csv(
        AdminQuizQuestionQuery(library_id=1)
    )

    # 导入解析器用 utf-8-sig 解码，这里以同一方式去掉 BOM 后校验表头
    rows = list(DictReader(StringIO(csv_text.encode("utf-8").decode("utf-8-sig"), newline="")))
    assert list(rows[0].keys()) == [
        "category_path",
        "question_type",
        "question_text",
        "options",
        "correct_answer",
        "explanation",
    ]
    assert rows[0]["category_path"] == '["网络安全", "攻击原理"]'
    assert rows[0]["correct_answer"] == "A"
    assert rows[1]["correct_answer"] == '["A", "B"]'
    # 填空答案用导入解析器的分隔符文法（同空 ;; 分隔、空间 | 分隔）
    assert rows[2]["correct_answer"] == "SYN;;同步|ACK"
    assert rows[3]["question_type"] == "judge"


@pytest.mark.asyncio
async def test_export_fill_blank_with_delimiters_falls_back_to_json(monkeypatch) -> None:
    questions = [_question(1, "fill_blank", [["a|b"]])]
    db = _SequenceDB([SimpleNamespace(scalars=lambda: iter(questions)),
                      SimpleNamespace(scalars=lambda: iter([_point()])),
                      SimpleNamespace(scalars=lambda: iter([_module()]))])

    @asynccontextmanager
    async def _context():
        yield db

    monkeypatch.setattr(service_module, "get_db_ctx", _context)
    csv_text = await AdminQuizV2Service().export_questions_csv(
        AdminQuizQuestionQuery(library_id=1)
    )
    rows = list(DictReader(StringIO(csv_text, newline="")))
    assert rows[0]["correct_answer"] == '[["a|b"]]'


@pytest.mark.asyncio
async def test_export_extends_header_for_option_images(monkeypatch) -> None:
    questions = [
        _question(1, "single_choice", "A", option_images={"B": "https://media/b.png"}),
        _question(2, "single_choice", "B"),
    ]
    db = _SequenceDB([SimpleNamespace(scalars=lambda: iter(questions)),
                      SimpleNamespace(scalars=lambda: iter([_point()])),
                      SimpleNamespace(scalars=lambda: iter([_module()]))])

    @asynccontextmanager
    async def _context():
        yield db

    monkeypatch.setattr(service_module, "get_db_ctx", _context)
    csv_text = await AdminQuizV2Service().export_questions_csv(
        AdminQuizQuestionQuery(library_id=1)
    )
    reader = DictReader(StringIO(csv_text, newline=""))
    rows = list(reader)
    assert reader.fieldnames[-4:] == [
        "option_image_a",
        "option_image_b",
        "option_image_c",
        "option_image_d",
    ]
    assert rows[0]["option_image_b"] == "https://media/b.png"
    assert rows[1]["option_image_b"] == ""
