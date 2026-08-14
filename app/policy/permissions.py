ROLE_PERMISSIONS: dict[str, list[str]] = {
    "super_admin": ["*"],
    "admin": [
        "dashboard:view",
        "user:list", "user:write", "user:delete",
        "order:list", "order:write",
        "quiz:list", "quiz:write", "quiz:import",
        "quiz_content_edit", "quiz_content_publish", "quiz_library_manage",
        "course_quiz_bind",
        "content:list", "content:write", "content:banner",
        "course:list", "course:write",
    ],
}
