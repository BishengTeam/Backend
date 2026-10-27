"""Vendor-friendly normalizations in the xlsx template import path.

2026-10-27: vendor workbooks (e.g. H3C NE) ship a single-level 分类 column and
2-3 underscore fill-blank placeholders; both are normalized at parse time so
the strict two-level V2 path and the ``____`` placeholder rule no longer fail
the whole batch.
"""

from __future__ import annotations

from app.services.admin_quiz import AdminQuizService


def _build(
    question_type: str,
    question_text: str,
    answer: str,
    category: str,
    options: list[str],
) -> dict[str, object]:
    values = {
        "question_type": question_type,
        "question_text": question_text,
        "correct_answer": answer,
        "category": category,
    }
    return AdminQuizService()._build_import_item_from_template(values, options)


def test_single_level_category_is_padded_to_two_levels() -> None:
    item = _build("单选题", "题干", "A", "SR", ["选项一", "选项二"])

    assert item["category_path"] == ["SR", "SR"]


def test_two_level_category_unchanged() -> None:
    item = _build("单选题", "题干", "A", "PPP/PPPoE", ["选项一", "选项二"])

    assert item["category_path"] == ["PPP", "PPPoE"]


def test_category_parts_are_stripped() -> None:
    item = _build("单选题", "题干", "A", "PPP、 PPPoE ", ["选项一", "选项二"])

    assert item["category_path"] == ["PPP", "PPPoE"]


def test_fill_blank_placeholder_normalized_to_four_underscores() -> None:
    item = _build(
        "填空题",
        "在PPPoE的__阶段确定。",
        "discovery",
        "PPP/PPPoE",
        [" discovery"],
    )

    assert item["question_text"] == "在PPPoE的____阶段确定。"
    assert item["correct_answer"] == [["discovery"]]


def test_fill_blank_single_underscore_and_long_runs_untouched() -> None:
    item = _build(
        "填空题",
        "a_b __c ___d ____e _____f",
        "x",
        "PPP/PPPoE",
        [" x"],
    )

    assert item["question_text"] == "a_b ____c ____d ____e _____f"


def test_choice_question_text_not_placeholder_normalized() -> None:
    item = _build(
        "单选题",
        "如下配置中nat_pool表示__",
        "A",
        "NAT",
        [" 正确", " 错误"],
    )

    assert item["question_text"] == "如下配置中nat_pool表示__"
