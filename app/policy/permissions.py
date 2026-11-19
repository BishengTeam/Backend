"""管理员权限策略：角色预设（模板）+ 按人覆盖（覆盖式勾选）。

- ``ROLE_PERMISSIONS`` 是角色预设模板（代码即真源，后台不可编辑）；
- ``admin_user.permissions`` 列（JSONB）存按人覆盖：NULL = 跟随角色预设，
  非 NULL 列表 = 该管理员的最终权限集（见 ``resolve_admin_permissions``）；
- ``PERMISSION_CATALOG`` 是全部合法权限串的唯一清单（含分组与中文名），
  供后台建号/编辑勾选 UI 与服务端白名单校验共用；
- ``super_admin`` 恒为 ``["*"]``，不走覆盖。
"""

from __future__ import annotations


ROLE_PERMISSIONS: dict[str, list[str]] = {
    "super_admin": ["*"],
    "quiz_admin": [
        "quiz:list", "quiz:write", "quiz:import",
        "quiz_content_edit", "quiz_content_publish", "quiz_library_manage",
        "course_quiz_bind", "quiz_review",
    ],
    "cert_admin": [
        "content:read",
        "content:list",
        "content:write",
        "document:read",
        "document:write",
        "user:list",
        "user:write",
        "order:list",
        "h3c:batch_manage",
        "h3c:review",
        "h3c:export",
        "h3c:refund",
        "h3c:order_close",
        "h3c:final_review",
        "nisp:batch_manage",
        "nisp:review",
        "nisp:export",
        "nisp:refund",
        "nisp:final_review",
        "renshe:review",
        "renshe:export",
        "sangfor:review",
        "sangfor:export",
    ],
    "course_admin": [
        "course:read",
        "course:write",
        "course:publish",
    ],
    "teacher": [
        "classroom:manage",
    ],
}

# 分组勾选目录：(权限串, 中文名)。组顺序即 UI 呈现顺序。
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
            ("user:list", "用户列表"),
            ("user:write", "用户管理"),
            ("user:delete", "用户删除"),
            ("order:list", "订单列表"),
            ("order:write", "订单管理"),
        ],
    },
    {
        "key": "content",
        "label": "内容与文档",
        "permissions": [
            ("content:read", "内容查看"),
            ("content:list", "内容列表"),
            ("content:write", "内容管理"),
            ("document:read", "文档查看"),
            ("document:write", "文档管理"),
        ],
    },
    {
        "key": "homepage",
        "label": "首页运营",
        "permissions": [
            ("homepage:list", "首页配置列表"),
            ("homepage:write", "首页配置管理"),
        ],
    },
    {
        "key": "quiz",
        "label": "题库与考试",
        "permissions": [
            ("quiz:list", "题库列表"),
            ("quiz:write", "题库写入"),
            ("quiz:import", "题库导入"),
            ("quiz_content_edit", "题目内容编辑"),
            ("quiz_content_publish", "题目内容发布"),
            ("quiz_library_manage", "题库管理"),
            ("quiz_review", "考试人工评阅"),
            ("course_quiz_bind", "课程-题库绑定"),
            ("classroom:manage", "课堂管理"),
        ],
    },
    {
        "key": "course",
        "label": "课程",
        "permissions": [
            ("course:read", "课程查看"),
            ("course:write", "课程管理"),
            ("course:publish", "课程发布"),
        ],
    },
    {
        "key": "competition",
        "label": "赛事与活动",
        "permissions": [
            ("competition:list", "赛事列表"),
            ("competition:write", "赛事管理"),
            ("activity:list", "活动列表"),
            ("activity:write", "活动管理"),
        ],
    },
    {
        "key": "training_job",
        "label": "培训与招聘",
        "permissions": [
            ("training:list", "培训列表"),
            ("training:write", "培训管理"),
            ("job:list", "招聘列表"),
            ("job:write", "招聘管理"),
        ],
    },
    {
        "key": "cert_review",
        "label": "认证审核",
        "permissions": [
            ("h3c:batch_manage", "H3C 批次管理"),
            ("h3c:review", "H3C 审核"),
            ("h3c:export", "H3C 导出"),
            ("h3c:refund", "H3C 退款"),
            ("h3c:order_close", "H3C 订单关闭"),
            ("h3c:final_review", "H3C 终审"),
            ("nisp:batch_manage", "NISP 批次管理"),
            ("nisp:review", "NISP 审核"),
            ("nisp:export", "NISP 导出"),
            ("nisp:refund", "NISP 退款"),
            ("nisp:final_review", "NISP 终审"),
            ("renshe:review", "职业技能等级认证审核"),
            ("renshe:export", "职业技能等级认证导出"),
            ("sangfor:review", "深信服审核"),
            ("sangfor:export", "深信服导出"),
        ],
    },
    {
        "key": "offline_payment",
        "label": "线下支付",
        "permissions": [
            ("offline:view", "线下支付查看"),
            ("offline:confirm", "确认到账"),
            ("offline:refund", "线下退款"),
            ("offline:return", "线下退回"),
            ("offline:settings", "线下支付设置"),
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

ALL_PERMISSIONS: frozenset[str] = frozenset(
    permission
    for group in PERMISSION_CATALOG
    for permission, _label in group["permissions"]  # type: ignore[misc]
)


def resolve_admin_permissions(admin) -> list[str]:
    """解析管理员的最终权限集。

    super_admin 恒为通配；否则优先取按人覆盖（JSONB 列），未覆盖时回退
    角色预设。所有权限判定点（require_permission、/me、回收站页签等）
    必须统一走本函数，禁止直接读 ``ROLE_PERMISSIONS``。
    """

    if admin.role == "super_admin":
        return ["*"]
    overrides = getattr(admin, "permissions", None)
    # 防御：只有 list/tuple 视为有效覆盖；NULL、异常值、未加载属性一律回退预设。
    if isinstance(overrides, (list, tuple)):
        return list(overrides)
    return list(ROLE_PERMISSIONS.get(admin.role, []))
