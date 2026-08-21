"""Production bootstrap invariants for the rebuilt course domain."""

from scripts.seed_production import COURSE_REQUIRED_TABLES, PRODUCTION_SEED_VERSION


def test_production_seed_validates_course_domain_without_inventing_content() -> None:
    assert PRODUCTION_SEED_VERSION == "2026.08.21.1"
    assert set(COURSE_REQUIRED_TABLES) == {
        "course",
        "quiz_course_library_binding",
        "course_category",
        "course_chapter",
        "course_upload",
        "course_enrollment",
        "user_chapter_progress",
        "quiz_library_entitlement",
        "course_audit_log",
        "course_entitlement_job",
        "course_entitlement_job_item",
    }


def test_initial_administrator_uses_explicit_security_columns() -> None:
    source = (
        __import__("pathlib")
        .Path(__file__)
        .resolve()
        .parents[2]
        .joinpath("bootstrap_app", "runtime.py")
        .read_text(encoding="utf-8")
    )
    for column in (
        "display_name",
        "must_change_password",
        "auth_version",
        "failed_login_attempts",
    ):
        assert column in source
