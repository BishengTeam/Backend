ROLE_PERMISSIONS: dict[str, list[str]] = {
    "super_admin": ["*"],
    "admin": [
        "dashboard:view",
        "user:list", "user:write", "user:delete",
        "order:list", "order:write",
        "quiz:list", "quiz:write", "quiz:import",
        "content:list", "content:write", "content:banner",
        "course:list", "course:write",
    ],
}
