ROLE_PERMISSIONS: dict[str, list[str]] = {
    "super_admin": ["*"],
    "content_editor": [
        "dashboard:view",
        "quiz:list", "quiz:write", "quiz:import",
        "content:list", "content:write", "content:banner",
        "course:list", "course:write",
    ],
    "customer_service": [
        "dashboard:view",
        "user:list",
        "user:write",
        "user:delete",
        "order:list",
    ],
    "finance": [
        "dashboard:view",
        "order:list", "order:write",
    ],
    "auditor": [
        "dashboard:view",
        "user:list", "order:list",
        "quiz:list", "content:list", "course:list",
    ],
}
