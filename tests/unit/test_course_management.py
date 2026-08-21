from decimal import Decimal
from pathlib import Path

from fastapi.routing import APIRoute
from pydantic import ValidationError

from app.main import app
from app.schemas.admin_course import AdminCourseCreate
from app.schemas.course import CourseDetailResponse
from app.services.course_storage import validate_upload


def _route(method: str, path: str) -> APIRoute:
    return next(
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path == path
        and method in route.methods
    )


def test_online_course_contract_requires_upload_and_uses_yuan() -> None:
    course = AdminCourseCreate(
        title="在线课程",
        category="网络",
        cover_upload_id=3,
        price_yuan=Decimal("199.00"),
        preview_chapter_count=2,
    )
    assert course.price_yuan == Decimal("199.00")
    assert course.preview_chapter_count == 2
    try:
        AdminCourseCreate(
            title="在线课程",
            category="网络",
            cover_upload_id=3,
            price_yuan=Decimal("199.001"),
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("course prices allow at most two decimal places")


def test_course_detail_contract_contains_preview_count_and_quiz_directory() -> None:
    response = CourseDetailResponse(
        id=1,
        title="课程",
        category="网络",
        cover_url="https://signed.example/cover.jpg",
        description=None,
        price=0,
        price_yuan=Decimal("0.00"),
        teacher_name=None,
        status="published",
        enrollment_id=None,
        preview_chapter_count=0,
        chapter_count=0,
    )
    assert response.included_quiz_libraries == []
    assert not hasattr(response, "free_preview_seconds")


def test_public_course_contract_does_not_expose_storage_keys() -> None:
    import inspect

    from app.schemas.course import ChapterResponse

    assert "video_storage_key" not in ChapterResponse.model_fields
    assert "video_url" not in ChapterResponse.model_fields
    source = inspect.getsource(ChapterResponse)
    assert "object_key" not in source


def test_upload_limits_are_frozen() -> None:
    validate_upload(
        kind="cover",
        filename="cover.webp",
        content_type="image/webp",
        size_bytes=10 * 1024 * 1024,
    )
    validate_upload(
        kind="chapter_video",
        filename="chapter.mkv",
        content_type="video/x-matroska",
        size_bytes=5 * 1024 * 1024 * 1024,
    )
    for filename, content_type in (
        ("cover.gif", "image/gif"),
        ("chapter.avi", "video/x-msvideo"),
    ):
        try:
            validate_upload(
                kind="cover" if filename.startswith("cover") else "chapter_video",
                filename=filename,
                content_type=content_type,
                size_bytes=1024,
            )
        except ValidationError:
            raise AssertionError("validation errors should be domain exceptions")
        except Exception as exc:
            assert "支持" in str(exc)
        else:
            raise AssertionError("unsupported course upload extension must be rejected")


def test_course_admin_permissions_split_daily_and_high_risk_operations() -> None:
    source = Path("app/api/admin/courses.py").read_text(encoding="utf-8")
    assert 'Depends(require_permission("course:read"))' in source
    assert 'Depends(require_permission("course:write"))' in source
    assert 'Depends(require_permission("course:publish"))' in source
    assert 'Depends(require_super_admin)' in source


def test_rebuild_migration_removes_fake_course_data_and_adds_oss_domain() -> None:
    source = Path(
        "alembic/versions/crs002_rebuild_oss_course_domain.py"
    ).read_text(encoding="utf-8")
    assert 'revision: str = "crs002"' in source
    assert "DROP TABLE IF EXISTS course_upload" in source
    assert "preview_chapter_count" in source
    assert "cover_storage_key" in source
    assert "course_asset" not in source[source.index("def upgrade()") : source.index("def downgrade()")]


def test_updater_configures_restricted_course_oss_cors() -> None:
    updater = Path("scripts/upgrade_release.sh").read_text(encoding="utf-8")
    script = Path("scripts/configure_course_oss.py").read_text(encoding="utf-8")

    assert "configuring course OSS browser-upload CORS" in updater
    assert "scripts/configure_course_oss.py" in updater
    assert "allowed_methods=[\"GET\", \"PUT\", \"POST\", \"DELETE\", \"HEAD\"]" in script
    assert "allowed_origins=sorted(origins)" in script
