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
        "user:list",
        "user:write",
        "order:list",
        "h3c:batch_manage",
        "h3c:review",
        "h3c:export",
        "h3c:refund",
        "h3c:order_close",
    ],
    "course_admin": [
        "course:read",
        "course:write",
        "course:publish",
    ],
}
