from datetime import datetime, timezone

from app.schemas.admin_quiz_contract import AdminQuizAuditLogResponse


def test_audit_response_adapts_legacy_scalar_changed_fields():
    payload = AdminQuizAuditLogResponse.model_validate(
        {
            "id": 1,
            "actor_type": "admin",
            "admin_id": 2,
            "permission": "quiz:write",
            "request_id": None,
            "ip_address": None,
            "action": "quiz_library.update",
            "object_type": "quiz_library",
            "object_id": 3,
            "result": "succeeded",
            "changed_fields": {"cover_url": "[REDACTED]"},
            "target_ids": None,
            "error_summary": None,
            "created_at": datetime.now(timezone.utc),
        }
    )

    assert payload.changed_fields is not None
    assert payload.changed_fields["cover_url"].before is None
    assert payload.changed_fields["cover_url"].after == "[REDACTED]"
