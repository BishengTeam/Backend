from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_shared_consultation_group_uses_plan_and_user_visibility_rules():
    service = (ROOT / "app/services/consultation_group.py").read_text(encoding="utf-8")

    assert "USER_VISIBLE_PLAN_STATUSES" in service
    assert '"published"' in service
    assert '"registration_closed"' in service
    assert '"finalized"' in service
    assert '"pending_payment"' not in service.split("USER_VISIBLE_REGISTRATION_STATUSES", 1)[1].split("}", 1)[0]
    assert '"refunded_closed"' not in service.split("USER_VISIBLE_REGISTRATION_STATUSES", 1)[1].split("}", 1)[0]
    assert "_is_expired(row)" in service
    assert "version" in service


def test_all_first_stage_certification_registrations_expose_consultation_group():
    schemas = {
        name: (ROOT / f"app/schemas/{name}.py").read_text(encoding="utf-8")
        for name in ("h3c_registration", "nisp", "renshe")
    }
    services = {
        name: (ROOT / f"app/services/{name}.py").read_text(encoding="utf-8")
        for name in ("h3c_registration", "nisp_registration", "renshe_application")
    }

    for source in schemas.values():
        assert "consultation_group: ConsultationGroupUserResponse | None" in source
    for source in services.values():
        assert "ConsultationGroupService.get_user_group" in source


def test_consultation_admin_api_has_concurrent_update_guard():
    api = (ROOT / "app/api/admin/consultation_groups.py").read_text(encoding="utf-8")
    schema = (ROOT / "app/schemas/consultation_group.py").read_text(encoding="utf-8")
    migration = (
        ROOT / "alembic/versions/cp013_cert_consultation_groups.py"
    ).read_text(encoding="utf-8")

    assert '"/{plan_id}"' in api
    assert "require_batch_permission" in api
    assert "version: int = Field(..., ge=0)" in schema
    assert "uq_cert_consultation_group_plan" in migration
