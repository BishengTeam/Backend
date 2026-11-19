"""考场/附件等厂商资产的授权范围。"""
from app.port.exceptions import ForbiddenException
from app.policy.permissions import resolve_admin_permissions

VENDOR_MANAGE_PERMISSIONS: dict[str, str] = {
    "h3c": "h3c:batch_manage",
    "sangfor": "content:write",
}


def admin_can_manage_vendor(admin, vendor: str) -> bool:
    permission = VENDOR_MANAGE_PERMISSIONS.get(vendor)
    if permission is None:
        return False
    allowed = resolve_admin_permissions(admin)
    return "*" in allowed or permission in allowed


def require_vendor_permission(admin, vendor: str) -> None:
    if not admin_can_manage_vendor(admin, vendor):
        permission = VENDOR_MANAGE_PERMISSIONS.get(vendor, "unknown")
        raise ForbiddenException(f"缺少权限: {permission}")


def manageable_vendors(admin) -> list[str]:
    return [
        vendor for vendor in VENDOR_MANAGE_PERMISSIONS
        if admin_can_manage_vendor(admin, vendor)
    ]
