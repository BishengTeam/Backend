"""管理员权限策略：大类授权（勾选）+ 细粒度权限（内部校验）。

- ``PERMISSION_GRANTS`` 是大类权限 → 内部细粒度权限的展开表：后台勾选只
  暴露大类（如「深信服认证审核」），鉴权仍使用原有的细粒度字面量；
- ``ROLE_PERMISSIONS`` 是角色预设模板（代码即真源，后台不可编辑），同样
  只写大类；
- ``admin_user.permissions`` 列（JSONB）存按人覆盖：NULL = 跟随角色预设，
  非 NULL 列表 = 该管理员的授权大类（见 ``resolve_granted_permissions``）；
- ``PERMISSION_CATALOG`` 是后台勾选 UI 的唯一清单（大类 + 中文名）；
- ``ALL_PERMISSIONS`` 是服务端白名单：大类 ∪ 历史细粒度，用于兼容存量覆盖；
- ``super_admin`` 恒为 ``["*"]``，不走覆盖。
"""

from __future__ import annotations


VENDOR_PERMISSIONS: tuple[str, ...] = (
    "cert:h3c",
    "cert:nisp",
    "cert:renshe",
    "cert:sangfor",
)

# 题库两组授权共用同一批细粒度权限；区别只在数据范围（见 policy/quiz_scope.py）。
QUIZ_FINE_PERMISSIONS: tuple[str, ...] = (
    "quiz:list",
    "quiz:write",
    "quiz:import",
    "quiz_content_edit",
    "quiz_content_publish",
    "quiz_library_manage",
    "quiz_review",
    "course_quiz_bind",
)

# 大类权限 → 内部细粒度权限。新增细粒度鉴权点时，必须同时挂到这里。
PERMISSION_GRANTS: dict[str, tuple[str, ...]] = {
    "dashboard:view": ("dashboard:view",),
    "user:manage": (
        "user:list",
        "user:write",
        "user:delete",
    ),
    "order:manage": (
        "order:list",
        "order:write",
    ),
    "content:manage": (
        "content:read",
        "content:list",
        "content:write",
        "document:read",
        "document:write",
    ),
    "homepage:manage": (
        "homepage:list",
        "homepage:write",
    ),
    "quiz:manage": QUIZ_FINE_PERMISSIONS,
    "quiz:course": QUIZ_FINE_PERMISSIONS,
    "course:manage": (
        "course:read",
        "course:write",
        "course:publish",
    ),
    "classroom:manage": ("classroom:manage",),
    "cert:h3c": (
        "h3c:batch_manage",
        "h3c:review",
        "h3c:export",
        "h3c:refund",
        "h3c:order_close",
        "h3c:final_review",
    ),
    "cert:nisp": (
        "nisp:batch_manage",
        "nisp:review",
        "nisp:export",
        "nisp:refund",
        "nisp:final_review",
    ),
    "renshe:review": ("renshe:review",),
    "renshe:export": ("renshe:export",),
    "cert:renshe": (
        "renshe:review",
        "renshe:export",
    ),
    "cert:sangfor": (
        "sangfor:review",
        "sangfor:export",
    ),
    "competition:manage": (
        "competition:list",
        "competition:write",
        "activity:list",
        "activity:write",
    ),
    "training:manage": (
        "training:list",
        "training:write",
        "job:list",
        "job:write",
    ),
    "offline:manage": (
        "offline:view",
        "offline:confirm",
        "offline:refund",
        "offline:return",
        "offline:settings",
    ),
    "points:manage": ("points:manage",),
    # 内部只读 grant：不进入后台勾选目录，仅供「运营管理员」等预设使用。
    "course:view": ("course:read",),
    "quiz:view": ("quiz:list",),
}

# 仅供角色预设使用的内部授权，不在勾选目录中展示。
INTERNAL_GRANTS: frozenset[str] = frozenset({"course:view", "quiz:view"})

# 历史细粒度权限 → 大类。用于存量按人覆盖归一化（过渡期兼容）。
# 多个大类共用同一批细粒度 key 时，保留最先声明的（quiz:manage 先于 quiz:course），
# 避免存量 quiz_admin 被归到课程题库。
LEGACY_PERMISSION_GRANTS: dict[str, str] = {}
for _grant, _permissions in PERMISSION_GRANTS.items():
    for _permission in _permissions:
        # 细粒度权限一旦同时是独立一级 grant，就不能再归一到其他大类；
        # 否则“只审核不导出”的按人授权会被折叠回完整 cert:renshe。
        if _permission != _grant and _permission not in PERMISSION_GRANTS:
            LEGACY_PERMISSION_GRANTS.setdefault(_permission, _grant)


ROLE_PERMISSIONS: dict[str, list[str]] = {
    "super_admin": ["*"],
    "quiz_admin": ["quiz:manage"],
    "cert_admin": [
        "content:manage",
        "user:manage",
        "order:manage",
        *VENDOR_PERMISSIONS,
    ],
    "course_admin": ["course:manage", "quiz:course"],
    "teacher": ["classroom:manage"],
    "operations_admin": [
        "points:manage",
        "homepage:manage",
        "competition:manage",
        "training:manage",
        "content:manage",
        "course:view",
        "quiz:view",
    ],
}

# 分组勾选目录：(大类权限, 中文名)。组顺序即 UI 呈现顺序。
PERMISSION_CATALOG: list[dict[str, object]] = [
    {
        "key": "dashboard",
        "label": "数据看板",
        "permissions": [
            ("dashboard:view", "查看数据看板"),
        ],
    },
    {
        "key": "user_order",
        "label": "用户与订单",
        "permissions": [
            ("user:manage", "用户管理"),
            ("order:manage", "订单管理"),
        ],
    },
    {
        "key": "content",
        "label": "内容与文档",
        "permissions": [
            ("content:manage", "内容与文档管理"),
        ],
    },
    {
        "key": "homepage",
        "label": "首页运营",
        "permissions": [
            ("homepage:manage", "首页运营管理"),
        ],
    },
    {
        "key": "quiz",
        "label": "题库与考试",
        "permissions": [
            ("quiz:manage", "独立题库"),
            ("quiz:course", "课程题库"),
        ],
    },
    {
        "key": "course",
        "label": "课程",
        "permissions": [
            ("course:manage", "课程商品"),
            ("classroom:manage", "教学课堂"),
        ],
    },
    {
        "key": "cert_review",
        "label": "认证审核",
        "permissions": [
            ("cert:h3c", "H3C 认证审核"),
            ("cert:nisp", "NISP 认证审核"),
            ("cert:renshe", "职业技能等级认证审核"),
            ("renshe:review", "职业技能等级认证-仅审核"),
            ("renshe:export", "职业技能等级认证-仅导出"),
            ("cert:sangfor", "深信服认证审核"),
        ],
    },
    {
        "key": "competition",
        "label": "赛事与活动",
        "permissions": [
            ("competition:manage", "赛事与活动管理"),
        ],
    },
    {
        "key": "training_job",
        "label": "培训与招聘",
        "permissions": [
            ("training:manage", "培训与招聘管理"),
        ],
    },
    {
        "key": "offline_payment",
        "label": "线下支付",
        "permissions": [
            ("offline:manage", "线下支付管理"),
        ],
    },
    {
        "key": "points_mall",
        "label": "积分商城",
        "permissions": [
            ("points:manage", "积分商城管理"),
        ],
    },
]

GRANT_PERMISSIONS: frozenset[str] = frozenset(PERMISSION_GRANTS)
LEGACY_PERMISSIONS: frozenset[str] = frozenset(LEGACY_PERMISSION_GRANTS)
ALL_PERMISSIONS: frozenset[str] = GRANT_PERMISSIONS | LEGACY_PERMISSIONS


def normalize_grants(values: list[str] | tuple[str, ...]) -> list[str]:
    """把混合了历史细粒度 key 的授权列表归一化为大类，去重保序。"""

    normalized: list[str] = []
    for value in values:
        if value == "*":
            if "*" not in normalized:
                normalized.append("*")
            continue
        grant = value if value in PERMISSION_GRANTS else LEGACY_PERMISSION_GRANTS.get(value)
        if grant is None or grant in normalized:
            continue
        normalized.append(grant)
    return normalized


def resolve_granted_permissions(admin) -> list[str]:
    """解析管理员的大类授权（后台勾选/展示口径）。

    super_admin 恒为通配；覆盖优先（含存量细粒度归一化），未覆盖回退角色预设。
    """

    if admin.role == "super_admin":
        return ["*"]
    overrides = getattr(admin, "permissions", None)
    if isinstance(overrides, (list, tuple)):
        return normalize_grants(overrides)
    return list(ROLE_PERMISSIONS.get(admin.role, []))


def resolve_admin_permissions(admin) -> list[str]:
    """解析管理员的最终权限集（鉴权口径，细粒度）。

    super_admin 恒为通配；否则把大类授权展开成内部细粒度权限。所有权限
    判定点（require_permission、/me、回收站页签等）必须统一走本函数，
    禁止直接读 ``ROLE_PERMISSIONS``。
    """

    if admin.role == "super_admin":
        return ["*"]
    resolved: list[str] = []
    grants = resolve_granted_permissions(admin)
    if "*" in grants:
        return ["*"]
    for grant in grants:
        for permission in PERMISSION_GRANTS.get(grant, ()):
            if permission not in resolved:
                resolved.append(permission)
    return resolved


def admin_has_permission(admin, permission: str) -> bool:
    """判断管理员是否持有某个权限（大类或细粒度均可）。"""

    if admin.role == "super_admin":
        return True
    for grant in resolve_granted_permissions(admin):
        if grant == "*" or grant == permission:
            return True
        if permission in PERMISSION_GRANTS.get(grant, ()):
            return True
    return False


def admin_has_any_permission(admin, permissions) -> bool:
    return any(admin_has_permission(admin, permission) for permission in permissions)
