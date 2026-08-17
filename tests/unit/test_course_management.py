from pathlib import Path

from fastapi.routing import APIRoute
from pydantic import ValidationError

from app.main import app
from app.schemas.admin_course import AdminCourseCreate
from app.schemas.course import ChapterCreate, CourseDetailResponse
from app.services.course import CourseService


def _route(method: str, path: str) -> APIRoute:
    return next(
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path == path
        and method in route.methods
    )


def test_online_course_contract_rejects_batches_and_allows_zero_price() -> None:
    course = AdminCourseCreate(title="在线课程", category="网络", price=0)
    assert course.price == 0
    try:
        AdminCourseCreate(
            title="在线课程", category="网络", price=0, batches={}
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("online courses must not accept batches")


def test_chapter_requires_source_specific_video_reference() -> None:
    ChapterCreate(
        title="第一节",
        video_source_type="external_url",
        video_url="https://video.example/chapter.m3u8",
    )
    ChapterCreate(
        title="第二节",
        video_source_type="private_object",
        video_storage_key="courses/1/chapters/2.m3u8",
    )
    try:
        ChapterCreate(title="第三节", video_source_type="private_object")
    except ValidationError:
        pass
    else:
        raise AssertionError("private video chapters need a storage key")


def test_course_detail_contract_contains_lifecycle_and_quiz_directory() -> None:
    response = CourseDetailResponse(
        id=1,
        title="课程",
        category="网络",
        price=0,
        status="published",
    )
    assert response.status == "published"
    assert response.included_quiz_libraries == []
    assert not hasattr(response, "batches")


def test_public_chapters_do_not_leak_private_object_keys() -> None:
    from types import SimpleNamespace

    chapter = SimpleNamespace(
        id=8,
        title="私有视频章节",
        video_url=None,
        video_source_type="private_object",
        video_storage_key="course-assets/8/private.m3u8",
        duration=120,
        sort_order=1,
        is_preview=False,
    )
    response = CourseService._public_chapter(chapter)
    assert response.video_storage_key is None
    assert response.video_source_type == "private_object"


def test_course_admin_routes_require_unique_super_admin() -> None:
    for method, path in (
        ("GET", "/admin/courses"),
        ("POST", "/admin/courses/{course_id}/lifecycle"),
        ("GET", "/admin/courses/{course_id}/quiz-bindings/impact"),
        ("POST", "/admin/courses/{course_id}/quiz-bindings"),
        ("POST", "/admin/courses/course-entitlement-jobs/{job_id}/retry"),
    ):
        dependencies = {
            dependency.call.__name__
            for dependency in _route(method, path).dependant.dependencies
        }
        assert "require_super_admin" in dependencies


def test_rebuild_migration_removes_fake_course_data_and_adds_free_source() -> None:
    source = Path(
        "alembic/versions/crs001_rebuild_course_domain.py"
    ).read_text(encoding="utf-8")
    assert 'revision: str = "crs001"' in source
    assert "course_enrollment" in source
    assert "source_type IN ('course_order', 'course_enrollment')" in source
    assert "DROP TABLE IF EXISTS quiz_course_library_binding" in source
