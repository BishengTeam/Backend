"""考场/附件等厂商资产的授权范围。"""
from app.port.exceptions import ForbiddenException
from app.policy.permissions import admin_has_permission

VENDOR_MANAGE_PERMISSIONS: dict[str, str] = {
    "h3c": "cert:h3c",
    "nisp": "cert:nisp",
    "renshe": "cert:renshe",
    "sangfor": "cert:sangfor",
}


def admin_can_manage_vendor(admin, vendor: str) -> bool:
    if getattr(admin, "role", None) == "super_admin":
        return True
    permission = VENDOR_MANAGE_PERMISSIONS.get(vendor)
    if permission is None:
        return False
    return admin_has_permission(admin, permission)


def require_vendor_permission(admin, vendor: str) -> None:
    if not admin_can_manage_vendor(admin, vendor):
        permission = VENDOR_MANAGE_PERMISSIONS.get(vendor, "unknown")
        raise ForbiddenException(f"缺少权限: {permission}")


def manageable_vendors(admin) -> list[str]:
    return [
        vendor for vendor in VENDOR_MANAGE_PERMISSIONS
        if admin_can_manage_vendor(admin, vendor)
    ]
