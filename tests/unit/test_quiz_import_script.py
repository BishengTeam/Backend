from __future__ import annotations

import importlib
from pathlib import Path

import pytest


def _module():
    return importlib.import_module("scripts.import_quiz")


def test_import_script_normalizers_are_available_without_database_loading() -> None:
    module = _module()

    assert module.normalize_category_name("  网络   基础 ") == "网络 基础"
    normalized = module.normalize_question_payload(
        question_type="single_choice",
        question_text="题干",
        options={"A": "一", "B": "二", "C": "三"},
        correct_answer="A",
        explanation=None,
        require_publishable=False,
    )
    assert normalized.question_text_hash


def test_import_script_requires_models_before_database_helpers(monkeypatch) -> None:
    module = _module()
    monkeypatch.setattr(module, "QuizCategory", None)
    monkeypatch.setattr(module, "QuizQuestion", None)
    monkeypatch.setattr(module, "AdminUser", None)

    with pytest.raises(RuntimeError, match="模型尚未加载"):
        module.find_category_path(None, ("网络基础",))


def test_category_paths_are_limited_to_three_levels() -> None:
    module = _module()
    errors: list[str] = []

    assert module.split_path(
        "一级/二级/三级/四级",
        field="category_path",
        label="questions.csv",
        line_no=2,
        errors=errors,
    ) is None
    assert any("at most three" in error for error in errors)


def test_business_cleanup_does_not_reference_removed_quiz_record_table() -> None:
    path = Path(__file__).resolve().parents[2] / "scripts" / "clean_business_data.py"
    source = path.read_text(encoding="utf-8")

    assert '"quiz_record"' not in source
    for table in (
        "quiz_practice_attempt",
        "quiz_practice_session_question",
        "quiz_exam_answer",
        "quiz_exam_question",
        "quiz_question_stats",
    ):
        assert f'"{table}"' in source
