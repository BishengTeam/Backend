"""domain/user 公开入口。"""

from app.domain.user.src.model.user import User
from app.domain.user.src.model.user_identity import UserIdentity
from app.domain.user.src.model.admin_user import AdminUser
from app.domain.user.src.model.deleted_openid import DeletedOpenid
from app.domain.user.src.model.points import PointsHistory, UserPoints
from app.domain.user.src.rule.admin_roles import ADMIN_ROLES

__all__ = [
    "User",
    "UserIdentity",
    "AdminUser",
    "DeletedOpenid",
    "UserPoints",
    "PointsHistory",
    "ADMIN_ROLES",
]
