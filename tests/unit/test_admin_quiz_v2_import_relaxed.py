"""Relaxed V2 import validation: duplicate stems skip instead of failing.

2026-10-22 product decision: repeated stems (within one batch or already in
the library) keep the first occurrence and silently skip later rows, so a
messy vendor workbook no longer fails validation outright.
"""

from __future__ import annotations

import asyncio
from collections import deque
from types import SimpleNamespace

from app.schemas.admin_quiz_contract import AdminQuizImportQuestion
from app.services.admin_quiz import AdminQuizService

PATH = ["模块", "知识点"]


class _Scalars:
    def __init__(self, items: list) -> None:
        self._items = items

    def __iter__(self):
        return iter(self._items)


class _Result:
    def __init__(self, scalar=None, items=None) -> None:
        self._scalar = scalar
        self._items = items or []

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return _Scalars(self._items)


class _FakeDB:
    """Serves queued results for the four selects in _validate_v2_import_rows."""

    def __init__(self, results: list) -> None:
        self._results = deque(results)

    async def execute(self, stmt):
        return self._results.popleft()


def _item(text: str) -> AdminQuizImportQuestion:
    return AdminQuizImportQuestion.model_validate(
        {
            "category_path": PATH,
            "question_type": "single_choice",
            "question_text": text,
            "options": {"A": "选项一", "B": "选项二"},
            "correct_answer": "A",
        }
    )


def _library_module_point() -> tuple:
    library = SimpleNamespace(id=6, status="active")
    module = SimpleNamespace(
        id=11, name="模块", normalized_name="模块", status="active"
    )
    point = SimpleNamespace(
        id=22, module_id=11, name="知识点", normalized_name="知识点", status="active"
    )
    return library, module, point


def _validate(db, rows):
    return asyncio.run(
        AdminQuizService()._validate_v2_import_rows(
            db,
            rows,
            [],
            source_type="json",
            job_id=1,
            lock_version=1,
            library_id=6,
            lock_content=False,
        )
    )


def test_v2_import_keeps_duplicate_stems_in_batch() -> None:
    library, module, point = _library_module_point()
    db = _FakeDB(
        [
            _Result(scalar=library),
            _Result(items=[module]),
            _Result(items=[point]),
        ]
    )
    rows = [
        (2, _item("题干一")),
        (3, _item("题干一")),
        (4, _item("题干二")),
    ]

    result = _validate(db, rows)

    # 2026-10-22（修订）：源文件多少行就导入多少，重复题干不跳过。
    assert result.errors == []
    assert result.missing_errors == []
    assert [locator for locator, _, _ in result.valid] == [2, 3, 4]


def test_v2_import_keeps_stems_already_in_library() -> None:
    library, module, point = _library_module_point()
    db = _FakeDB(
        [
            _Result(scalar=library),
            _Result(items=[module]),
            _Result(items=[point]),
        ]
    )
    rows = [
        (2, _item("库内已有题")),
        (3, _item("全新题")),
    ]

    result = _validate(db, rows)

    assert result.errors == []
    assert [locator for locator, _, _ in result.valid] == [2, 3]


def test_v2_import_accepts_disabled_modules_and_points() -> None:
    library, module, point = _library_module_point()
    module.status = "disabled"
    point.status = "disabled"
    db = _FakeDB(
        [
            _Result(scalar=library),
            _Result(items=[module]),
            _Result(items=[point]),
            _Result(items=[]),
        ]
    )
    rows = [(2, _item("停用分类下的题"))]

    result = _validate(db, rows)

    assert result.errors == []
    assert result.missing_errors == []
    assert [locator for locator, _, _ in result.valid] == [2]
    assert result.valid[0][2] == 22


def test_v2_import_flags_missing_structure_without_hard_error() -> None:
    library, _, _ = _library_module_point()
    db = _FakeDB(
        [
            _Result(scalar=library),
            _Result(items=[]),
            _Result(items=[]),
            _Result(items=[]),
        ]
    )
    rows = [(2, _item("新结构下的题"))]

    result = _validate(db, rows)

    # Missing structure is informational only: auto-creation happens at import
    # time, so the row stays in `valid` with a path target.
    assert result.errors == []
    assert [item["error_code"] for item in result.missing_errors] == [
        "category_missing"
    ]
    assert len(result.valid) == 1
    _, _, target = result.valid[0]
    assert isinstance(target, tuple) and target == ("模块", "知识点")
