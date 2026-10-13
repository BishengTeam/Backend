from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_all_certification_refund_paths_use_shared_recovery_semantics():
    paths = [
        "app/services/h3c_refund.py",
        "app/services/nisp_refund.py",
        "app/services/nisp_worker.py",
        "app/services/renshe_refund.py",
        "app/services/renshe_refund_reconciliation.py",
        "app/services/payment_refund.py",
    ]

    for path in paths:
        source = ROOT.joinpath(path).read_text(encoding="utf-8")
        assert "app.services.refund_recovery" in source, path


def test_h3c_and_nisp_registration_responses_expose_active_refund_state():
    for path in (
        "app/schemas/h3c_registration.py",
        "app/schemas/nisp.py",
        "app/services/h3c_registration.py",
        "app/services/nisp_registration.py",
    ):
        source = ROOT.joinpath(path).read_text(encoding="utf-8")
        assert "active_refund" in source, path


def test_failed_renshe_refunds_are_retried_with_backoff():
    service = ROOT.joinpath("app/services/renshe_refund.py").read_text(encoding="utf-8")
    worker = ROOT.joinpath(
        "app/services/renshe_refund_reconciliation.py"
    ).read_text(encoding="utf-8")

    assert 'if status == "failed"' in service
    assert "refund_retry_is_due" in worker
    assert 'RensheRefundRequest.status == "failed"' in worker
