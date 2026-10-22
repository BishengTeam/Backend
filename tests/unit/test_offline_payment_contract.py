from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.schemas.offline_payment import ConfirmInput, SettingsInput, SubmissionInput


def test_settings_require_real_contact_even_if_optional_fields_omitted():
    with pytest.raises(ValidationError, match="联系方式"):
        SettingsInput(account_name="测试账户", bank_name="测试银行", account_number="6222000011112222", contact_name="测试管理员")


@pytest.mark.parametrize("amount", [1.2, "100", True, 0, -1, 2147483648])
def test_money_must_be_positive_integer_cents(amount):
    with pytest.raises(ValidationError):
        ConfirmInput(amount_cents=amount, bank_time=datetime.now(timezone.utc), bank_reference="flow",
            expected_version=1, request_key="test", note="实际到账")


def test_future_and_duplicate_materials_are_rejected():
    body = dict(payer="测试付款人", transfer_time=datetime.now(timezone.utc), amount_cents=100,
        material_ids=[1], expected_version=0, request_key="test")
    for change in [{"transfer_time": datetime.now(timezone.utc) + timedelta(hours=1)}, {"material_ids": [1, 1]},
                   {"material_ids": []}, {"material_ids": [1, 2, 3, 4]}, {"transfer_time": datetime.now()}]:
        with pytest.raises(ValidationError):
            SubmissionInput(**{**body, **change})


def test_finance_routes_require_separate_permissions_and_reauthentication():
    from app.api.offline_payment import admin_router
    from app.middleware.auth import require_reauthenticated_admin, require_reauthenticated_super_admin
    routes = {route.path: route for route in admin_router.routes}
    for suffix in ["confirm", "refund", "resolve"]:
        route = routes[f"/orders/{{order_id}}/offline-payment/{suffix}"]
        assert require_reauthenticated_admin in [d.call for d in route.dependant.dependencies]
        assert any("offline:" in repr(c.cell_contents) for d in route.dependant.dependencies
                   for c in (getattr(d.call, "__closure__", None) or []))
    assert require_reauthenticated_super_admin in [d.call for d in routes["/orders/offline-payment-settings"].dependant.dependencies] or any(
        route.methods == {"PUT"} and require_reauthenticated_super_admin in [d.call for d in route.dependant.dependencies]
        for route in admin_router.routes if route.path.endswith("offline-payment-settings"))
