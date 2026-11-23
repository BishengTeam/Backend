from types import SimpleNamespace

import pytest

from app.policy.vendor_scope import (
    admin_can_manage_vendor,
    manageable_vendors,
    require_vendor_permission,
)
from app.port.exceptions import ForbiddenException


def _admin(role="cert_admin", permissions=None):
    return SimpleNamespace(role=role, permissions=permissions)


def test_super_admin_can_manage_every_vendor():
    admin = _admin(role="super_admin", permissions=None)
    for vendor in ("h3c", "nisp", "renshe", "sangfor"):
        assert admin_can_manage_vendor(admin, vendor)
    assert set(manageable_vendors(admin)) == {"h3c", "nisp", "renshe", "sangfor"}


@pytest.mark.parametrize("permission,vendor", [
    ("cert:h3c", "h3c"),
    ("cert:nisp", "nisp"),
    ("cert:renshe", "renshe"),
    ("cert:sangfor", "sangfor"),
])
def test_each_vendor_maps_to_its_own_write_permission(permission, vendor):
    admin = _admin(permissions=[permission])
    assert admin_can_manage_vendor(admin, vendor)
    for other in {"h3c", "nisp", "renshe", "sangfor"} - {vendor}:
        assert not admin_can_manage_vendor(admin, other)


def test_unknown_permission_is_rejected_without_bypassing_super_admin():
    admin = _admin(permissions=["content:read"])
    assert manageable_vendors(admin) == []
    with pytest.raises(ForbiddenException):
        require_vendor_permission(admin, "nisp")
