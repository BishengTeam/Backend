from decimal import Decimal
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys

from fastapi.routing import APIRoute
import pytest
from pydantic import ValidationError

import app.api.admin.courses as courses_api
from app.main import app
from app.schemas.admin_course import AdminChapterUpdate, AdminChapterResponse, AdminCourseCreate
from app.schemas.course import CourseDetailResponse
from app.services.course_storage import CourseStorage, validate_upload


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


def test_admin_chapter_response_contains_workbench_metadata() -> None:
    assert set(AdminChapterResponse.model_fields) >= {
        "id",
        "course_id",
        "title",
        "video_storage_key",
        "original_filename",
        "content_type",
        "size_bytes",
        "duration",
        "sort_order",
        "created_at",
        "updated_at",
    }


@pytest.mark.asyncio
async def test_update_chapter_endpoint_does_not_read_course_id_from_response(
    monkeypatch,
) -> None:
    class Service:
        async def update_chapter_metadata(self, course_id, chapter_id, data, *, admin_id):
            assert (course_id, chapter_id, admin_id) == (1, 2, 9)
            assert data.title == "新章节标题"
            return AdminChapterUpdate(title=data.title)

    monkeypatch.setattr(courses_api, "AdminCourseService", Service)
    response = await courses_api.update_chapter(
        course_id=1,
        chapter_id=2,
        body=AdminChapterUpdate(title="新章节标题"),
        admin=SimpleNamespace(id=9),
    )

    assert response.data.title == "新章节标题"


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


@pytest.mark.asyncio
async def test_course_video_part_signing_uses_string_query_params(monkeypatch) -> None:
    signed: dict[str, object] = {}

    class Bucket:
        def sign_url(self, method, key, expires, params=None):
            signed.update(
                method=method,
                key=key,
                expires=expires,
                params=params,
            )
            return "https://oss.example/signed-part"

    monkeypatch.setattr(CourseStorage, "_bucket", staticmethod(lambda: Bucket()))

    url = await CourseStorage.part_url(
        "course/default/chapters/video.mp4", "oss-upload-id", 2
    )

    assert url == "https://oss.example/signed-part"
    assert signed["method"] == "PUT"
    assert signed["key"] == "course/default/chapters/video.mp4"
    assert signed["expires"] == 3600
    assert signed["params"] == {"partNumber": "2", "uploadId": "oss-upload-id"}
    assert all(
        isinstance(value, str)
        for value in signed["params"].values()  # type: ignore[union-attr]
    )


@pytest.mark.asyncio
async def test_course_video_parts_read_oss_result_and_follow_pagination(
    monkeypatch,
) -> None:
    class PartResult:
        def __init__(self, parts, is_truncated, next_marker):
            self.parts = parts
            self.is_truncated = is_truncated
            self.next_marker = next_marker

        def __iter__(self):
            raise TypeError("OSS ListPartsResult is not iterable")

    class Bucket:
        calls = []

        def list_parts(self, key, upload_id, marker="", max_parts=1000):
            self.calls.append((key, upload_id, marker, max_parts))
            if marker == "":
                return PartResult(
                    [SimpleNamespace(part_number=1, size=16, etag='"etag-1"')],
                    True,
                    "2",
                )
            return PartResult(
                [SimpleNamespace(part_number=2, size=4, etag="etag-2")],
                False,
                "",
            )

    bucket = Bucket()
    monkeypatch.setattr(CourseStorage, "_bucket", staticmethod(lambda: bucket))

    result = await CourseStorage.list_parts("course/video.mp4", "upload-id")

    assert result == [
        {"part_number": 1, "size_bytes": 16, "etag": "etag-1"},
        {"part_number": 2, "size_bytes": 4, "etag": "etag-2"},
    ]
    assert bucket.calls == [
        ("course/video.mp4", "upload-id", "", 1000),
        ("course/video.mp4", "upload-id", "2", 1000),
    ]


@pytest.mark.asyncio
async def test_course_video_complete_uses_all_listed_parts(monkeypatch) -> None:
    class PartResult:
        def __init__(self, parts):
            self.parts = parts
            self.is_truncated = False
            self.next_marker = ""

    class FakePartInfo:
        def __init__(self, part_number, etag):
            self.part_number = part_number
            self.etag = etag

    class Bucket:
        completed = None

        def list_parts(self, key, upload_id, marker="", max_parts=1000):
            assert (key, upload_id, marker, max_parts) == (
                "course/video.mp4",
                "upload-id",
                "",
                1000,
            )
            return PartResult(
                [
                    SimpleNamespace(part_number=1, size=16, etag="etag-1"),
                    SimpleNamespace(part_number=2, size=4, etag="etag-2"),
                ]
            )

        def complete_multipart_upload(self, key, upload_id, parts):
            self.completed = (key, upload_id, parts)

        def head_object(self, key):
            return SimpleNamespace(
                content_length=20,
                headers={"Content-Type": "video/mp4"},
            )

    bucket = Bucket()
    monkeypatch.setattr(CourseStorage, "_bucket", staticmethod(lambda: bucket))
    oss_module = ModuleType("oss2")
    models_module = ModuleType("oss2.models")
    models_module.PartInfo = FakePartInfo
    oss_module.models = models_module
    monkeypatch.setitem(sys.modules, "oss2", oss_module)
    monkeypatch.setitem(sys.modules, "oss2.models", models_module)

    uploaded = await CourseStorage.complete(
        "course/video.mp4", "upload-id", 20
    )

    assert uploaded.size_bytes == 20
    assert uploaded.content_type == "video/mp4"
    assert bucket.completed[0] == "course/video.mp4"
    assert [(part.part_number, part.etag) for part in bucket.completed[2]] == [
        (1, "etag-1"),
        (2, "etag-2"),
    ]


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
