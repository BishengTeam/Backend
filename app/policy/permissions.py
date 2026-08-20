ROLE_PERMISSIONS: dict[str, list[str]] = {
    "super_admin": ["*"],
    "quiz_admin": [
        "quiz:list", "quiz:write", "quiz:import",
        "quiz_content_edit", "quiz_content_publish", "quiz_library_manage",
        "course_quiz_bind",
    ],
    "h3c_admin": [
        "h3c:batch_manage",
        "h3c:review",
        "h3c:export",
        "h3c:refund",
        "h3c:order_close",
    ],
}
