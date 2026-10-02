from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_h3c_user_batch_cards_remain_visible_until_archived():
    source = (
        REPO_ROOT / "app/services/h3c_registration.py"
    ).read_text(encoding="utf-8")

    assert 'H3C_VISIBLE_PLAN_STATUSES = (\n    "published",\n    "registration_closed",\n    "finalized",\n)' in source
    assert "Plan.status.in_(H3C_VISIBLE_PLAN_STATUSES)" in source
    assert 'Plan.status == "published"' not in source


def test_h3c_admin_archive_transition_is_exposed():
    api_source = (REPO_ROOT / "app/api/admin/h3c.py").read_text(encoding="utf-8")

    assert '"/batches/{batch_id}/archive"' in api_source
    assert "H3cAdminBatchService().archive_batch" in api_source
