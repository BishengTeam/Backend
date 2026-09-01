"""Machine-readable joint-release contract gate tests."""

from __future__ import annotations

import json

import pytest

from scripts.quiz_contract_manifest import (
    _assert_equal,
    _scan_removed_operations,
    canonical_manifest,
)


def test_canonical_manifest_has_frozen_counts_and_stable_fingerprint() -> None:
    manifest = canonical_manifest()
    assert manifest["quiz_contract_version"] == "2026-09-01"
    assert manifest["operation_count"] == 95
    assert manifest["user_operation_count"] == 32
    assert manifest["admin_operation_count"] == 63
    assert manifest["removed_operation_count"] == 13
    assert len(manifest["fingerprint_sha256"]) == 64


def test_manifest_equality_rejects_version_or_operation_drift(tmp_path) -> None:
    expected = canonical_manifest()
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(expected), encoding="utf-8")
    _assert_equal(expected, json.loads(path.read_text(encoding="utf-8")), path)

    drifted = dict(expected)
    drifted["quiz_contract_version"] = "stale"
    with pytest.raises(RuntimeError, match="manifest mismatch"):
        _assert_equal(expected, drifted, path)


def test_removed_route_scanner_checks_string_literals_without_prefix_false_positive(
    tmp_path,
) -> None:
    manifest = canonical_manifest()
    source = tmp_path / "src"
    source.mkdir()
    client = source / "client.ts"
    client.write_text(
        "const current = '/admin/quiz/imports/json'\n"
        "const current2 = `/api/quiz/practice-sessions/${id}/attempts`\n",
        encoding="utf-8",
    )
    _scan_removed_operations(manifest, [str(source)])

    client.write_text(
        "const removed = '/api/quiz/submit'\n"
        "const dynamic = `/api/quiz/wrong-book/${id}`\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="removed quiz API references remain"):
        _scan_removed_operations(manifest, [str(source)])
