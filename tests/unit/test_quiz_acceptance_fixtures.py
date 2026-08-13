from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.admin_quiz import AdminQuizService
from scripts.quiz_acceptance_fixtures import (
    FIXTURE_PREFIX,
    FIXTURE_VERSION,
    FixtureError,
    build_fixture_set,
    check,
    check_manifest,
    generate,
)


def test_fixture_definition_is_deterministic_bounded_and_complete() -> None:
    first_artifacts, first_manifest = build_fixture_set()
    second_artifacts, second_manifest = build_fixture_set()

    assert first_artifacts == second_artifacts
    assert first_manifest == second_manifest
    assert first_manifest["fixture_version"] == FIXTURE_VERSION
    assert first_manifest["fixture_prefix"] == FIXTURE_PREFIX
    assert first_manifest["generation"] == {
        "deterministic": True,
        "contains_credentials": False,
        "connects_to_external_services": False,
    }

    metadata = first_manifest["artifacts"]
    assert set(metadata) == {
        "acceptance-plan.json",
        "categories.json",
        "workflow-questions.json",
        "import-success-5000.json",
        "import-success-5000.csv",
        "import-validation-errors.json",
        "import-validation-errors.csv",
    }
    for name in ("import-success-5000.json", "import-success-5000.csv"):
        assert metadata[name]["row_or_case_count"] == 5000
        assert metadata[name]["size_bytes"] <= 10 * 1024 * 1024


def test_success_import_fixtures_match_the_backend_parser_contract() -> None:
    artifacts, _manifest = build_fixture_set()
    service = AdminQuizService()

    json_rows, json_errors = service._parse_import_rows(
        "json", artifacts["import-success-5000.json"]
    )
    csv_rows, csv_errors = service._parse_import_rows(
        "csv", artifacts["import-success-5000.csv"]
    )

    assert json_errors == []
    assert csv_errors == []
    assert len(json_rows) == len(csv_rows) == 5000
    assert {item.question_type.value for _, item in json_rows} == {
        "single_choice",
        "multiple_choice",
        "judge",
    }
    assert {item.question_type.value for _, item in csv_rows} == {
        "single_choice",
        "multiple_choice",
        "judge",
    }
    assert all(len(item.category_path) == 3 for _, item in json_rows + csv_rows)
    # CSV and JSON success files can be imported into the same disposable
    # database without triggering the same-category normalized-text rule.
    assert {
        item.question_text for _, item in json_rows
    }.isdisjoint({item.question_text for _, item in csv_rows})


def test_validation_error_fixtures_exercise_parse_and_database_errors() -> None:
    artifacts, _manifest = build_fixture_set()
    service = AdminQuizService()

    json_rows, json_parse_errors = service._parse_import_rows(
        "json", artifacts["import-validation-errors.json"]
    )
    csv_rows, csv_parse_errors = service._parse_import_rows(
        "csv", artifacts["import-validation-errors.csv"]
    )

    # JSON rows are structurally valid and fail later on missing/disabled
    # categories plus a duplicate normalized question text.
    assert len(json_rows) == 4
    assert json_parse_errors == []
    # CSV also freezes two parser/schema failures, then leaves three rows for
    # missing-category and duplicate checks in the database validation phase.
    assert len(csv_rows) == 3
    assert len(csv_parse_errors) == 2
    assert {error["row"] for error in csv_parse_errors} == {2, 3}


def test_acceptance_plan_freezes_scenarios_personas_and_cleanup_boundary() -> None:
    artifacts, _manifest = build_fixture_set()
    plan = json.loads(artifacts["acceptance-plan.json"])

    assert {item["id"] for item in plan["scenarios"]} == {
        "QF55-CATEGORY-THREE-LEVELS",
        "QF55-IMPORT-JSON-5000",
        "QF55-IMPORT-CSV-5000",
        "QF55-IMPORT-ATOMIC-ERRORS",
        "QF55-PERMISSIONS",
        "QF55-PRACTICE-WRONG-CLEAR",
        "QF55-EXAM-FOUR-STATES-AND-DISCONNECT",
        "QF55-OSS-SEVEN-DAY-LIFECYCLE",
        "QF55-WORKER-RESTART-AND-CONTENTION",
    }
    assert {item["ref"] for item in plan["required_personas"]} >= {
        "admin",
        "super_admin",
        "disabled_admin",
        "user_primary",
        "user_secondary",
        "anonymous",
    }
    assert plan["cleanup"]["allowed_database_suffixes"] == [
        "_test",
        "_uat",
        "_acceptance",
    ]
    assert "production" in " ".join(plan["cleanup"]["never"])


def test_generate_check_and_owned_file_overwrite_protection(tmp_path: Path) -> None:
    output = tmp_path / "qf55"
    manifest = generate(output)
    assert check(output) == manifest

    with pytest.raises(FixtureError, match="already exist"):
        generate(output)

    unrelated = output / "operator-notes.txt"
    unrelated.write_text("keep me", encoding="utf-8")
    (output / "categories.json").write_text("tampered", encoding="utf-8")
    with pytest.raises(FixtureError, match="drifted"):
        check(output)

    assert generate(output, force=True) == manifest
    assert unrelated.read_text(encoding="utf-8") == "keep me"
    assert check(output) == manifest


def test_canonical_manifest_detects_definition_drift(tmp_path: Path) -> None:
    _artifacts, manifest = build_fixture_set()
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    assert check_manifest(path) == manifest

    manifest["fixture_version"] = "tampered"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(FixtureError, match="drifted"):
        check_manifest(path)


@pytest.mark.parametrize("broad_path", [Path("/"), Path.home()])
def test_generator_refuses_broad_output_directories(broad_path: Path) -> None:
    with pytest.raises(FixtureError, match="broad"):
        generate(broad_path, force=True)
