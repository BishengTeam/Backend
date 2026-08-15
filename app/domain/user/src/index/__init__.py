"""domain/user 公开入口。"""

from app.domain.user.src.model.user import User
from app.domain.user.src.model.user_profile import UserProfile
from app.domain.user.src.model.user_realname import UserRealname
from app.domain.user.src.model.user_student import UserStudent
from app.domain.user.src.model.user_enterprise import UserEnterprise
from app.domain.user.src.model.admin_user import AdminUser
from app.domain.user.src.model.admin_password_history import AdminPasswordHistory
from app.domain.user.src.model.admin_security_audit import AdminSecurityAudit
from app.domain.user.src.model.deleted_openid import DeletedOpenid
from app.domain.user.src.model.deleted_identity_hash import DeletedIdentityHash
from app.domain.user.src.model.points import PointsHistory, UserPoints
from app.domain.user.src.rule.admin_roles import ADMIN_ROLES

__all__ = [
    "User",
    "UserProfile",
    "UserRealname",
    "UserStudent",
    "UserEnterprise",
    "AdminUser",
    "AdminPasswordHistory",
    "AdminSecurityAudit",
    "DeletedOpenid",
    "DeletedIdentityHash",
    "UserPoints",
    "PointsHistory",
    "ADMIN_ROLES",
]
