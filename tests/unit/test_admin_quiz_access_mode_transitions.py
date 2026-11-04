"""Access-mode transition table guards: every mode must have an exit map.

Regression guard for the 2026-10-24 production KeyError: a paid library hit
``_ACCESS_MODE_TRANSITIONS[current_mode]`` with no ``PAID`` key, turning the
convert-access-mode endpoint into a 500 instead of a clean validation error.
"""

from app.domain.community.src.rule.quiz import QuizLibraryAccessMode
from app.services.admin_quiz_v2 import _ACCESS_MODE_TRANSITIONS


def test_every_access_mode_has_a_transition_entry():
    assert set(_ACCESS_MODE_TRANSITIONS) == set(QuizLibraryAccessMode)


def test_paid_can_convert_out_to_every_open_mode():
    assert _ACCESS_MODE_TRANSITIONS[QuizLibraryAccessMode.PAID] == {
        QuizLibraryAccessMode.FREE,
        QuizLibraryAccessMode.COURSE_ENTITLEMENT,
        QuizLibraryAccessMode.ACTIVATION_CODE,
    }


def test_points_redemption_paid_mode_is_reachable_from_other_modes():
    for mode in QuizLibraryAccessMode:
        if mode != QuizLibraryAccessMode.PAID:
            assert QuizLibraryAccessMode.PAID in _ACCESS_MODE_TRANSITIONS[mode]


def test_activation_code_mode_is_reachable_and_exits_cleanly():
    # quiz018：激活码模式可从任一既有模式转入（此前没有任何转 INTO 的路径，
    # free→paid 只能走编辑表单的历史坑不得在 activation_code 上重演）。
    for mode in (
        QuizLibraryAccessMode.PENDING,
        QuizLibraryAccessMode.FREE,
        QuizLibraryAccessMode.COURSE_ENTITLEMENT,
        QuizLibraryAccessMode.PAID,
    ):
        assert QuizLibraryAccessMode.ACTIVATION_CODE in _ACCESS_MODE_TRANSITIONS[mode]
    assert _ACCESS_MODE_TRANSITIONS[QuizLibraryAccessMode.ACTIVATION_CODE] == {
        QuizLibraryAccessMode.FREE,
        QuizLibraryAccessMode.COURSE_ENTITLEMENT,
        QuizLibraryAccessMode.PAID,
    }
