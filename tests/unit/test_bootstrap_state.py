from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from bootstrap_app.state import (
    BootstrapCompletedError,
    BootstrapPhase,
    BootstrapStateError,
    BootstrapStateStore,
)


def _store(tmp_path):
    control = tmp_path / "control"
    control.mkdir(mode=0o700)
    return BootstrapStateStore(control, b"t" * 64)


def test_state_is_signed_private_and_advances_one_phase(tmp_path):
    store = _store(tmp_path)
    state = store.initialize()
    assert state.phase == BootstrapPhase.NEW
    assert len(state.installation_id) == 32
    assert (os.stat(store.state_path).st_mode & 0o777) == 0o600
    assert (os.stat(store.control_dir).st_mode & 0o777) == 0o700

    configured = store.transition(
        BootstrapPhase.NEW,
        BootstrapPhase.CONFIGURED,
        config_fingerprint="a" * 64,
    )
    assert configured.phase == BootstrapPhase.CONFIGURED
    assert configured.config_fingerprint == "a" * 64

    with pytest.raises(BootstrapStateError, match="advance one phase"):
        store.transition(
            BootstrapPhase.CONFIGURED,
            BootstrapPhase.INFRA_READY,
        )


def test_state_tamper_is_rejected(tmp_path):
    store = _store(tmp_path)
    store.initialize()
    envelope = json.loads(store.state_path.read_text(encoding="utf-8"))
    envelope["payload"]["phase"] = BootstrapPhase.PRODUCTION_ACCEPTED.value
    store.state_path.write_text(json.dumps(envelope), encoding="utf-8")

    with pytest.raises(BootstrapStateError, match="signature"):
        store.load()


def test_state_failure_is_redacted_and_retry_clears_only_failure(tmp_path):
    store = _store(tmp_path)
    store.initialize()
    failed = store.record_failure("quality_gate_failed", "backend_quality")
    assert failed.retry_count == 1
    assert failed.last_failure is not None
    assert failed.last_failure.code == "quality_gate_failed"
    assert "password" not in json.dumps(failed.public_dict())

    retried = store.clear_failure()
    assert retried.phase == BootstrapPhase.NEW
    assert retried.retry_count == 1
    assert retried.last_failure is None


def test_concurrent_transition_has_exactly_one_winner(tmp_path):
    store = _store(tmp_path)
    store.initialize()

    def transition():
        try:
            store.transition(BootstrapPhase.NEW, BootstrapPhase.CONFIGURED)
            return "ok"
        except BootstrapStateError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: transition(), range(2)))
    assert sorted(outcomes) == ["conflict", "ok"]


def test_completed_installation_rejects_mutations(tmp_path):
    store = _store(tmp_path)
    state = store.initialize()
    phases = list(BootstrapPhase)
    for target in phases[1 : phases.index(BootstrapPhase.INSTALLED_PENDING_UAT) + 1]:
        state = store.transition(state.phase, target)
    assert state.phase == BootstrapPhase.INSTALLED_PENDING_UAT

    with pytest.raises(BootstrapCompletedError):
        store.load(allow_completed=False)
    with pytest.raises(BootstrapCompletedError):
        store.clear_failure()
