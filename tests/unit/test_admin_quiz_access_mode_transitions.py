"""Access-mode transition table guards: every mode must have an exit map.

Regression guard for the 2026-10-24 production KeyError: a paid library hit
``_ACCESS_MODE_TRANSITIONS[current_mode]`` with no ``PAID`` key, turning the
convert-access-mode endpoint into a 500 instead of a clean validation error.
"""

from app.domain.community.src.rule.quiz import QuizLibraryAccessMode
from app.services.admin_quiz_v2 import _ACCESS_MODE_TRANSITIONS


def test_every_access_mode_has_a_transition_entry():
    assert set(_ACCESS_MODE_TRANSITIONS) == set(QuizLibraryAccessMode)


def test_paid_can_convert_to_free_and_course_entitlement():
    assert _ACCESS_MODE_TRANSITIONS[QuizLibraryAccessMode.PAID] == {
        QuizLibraryAccessMode.FREE,
        QuizLibraryAccessMode.COURSE_ENTITLEMENT,
    }


def test_pending_stays_restricted_to_non_paid_targets():
    assert QuizLibraryAccessMode.PAID not in _ACCESS_MODE_TRANSITIONS[
        QuizLibraryAccessMode.PENDING
    ]
