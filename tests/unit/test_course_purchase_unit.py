from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_course_purchase_reads_price_from_locked_course_model():
    source = (REPO_ROOT / "app/services/course_purchase.py").read_text(encoding="utf-8")

    assert "select(Course).where(Course.id == course_id).with_for_update()" in source
    assert "price=course.price" in source
    assert "CoursePurchaseRequest" not in source


def test_duplicate_purchase_uses_order_then_enrollment_lock_order():
    source = (REPO_ROOT / "app/services/course_purchase.py").read_text(encoding="utf-8")
    existing_block = source[
        source.index("if enrollment is not None:") : source.index("if course.price == 0:")
    ]

    assert existing_block.index("select(Order)") < existing_block.index(
        "select(CourseEnrollment)"
    )


def test_public_media_lookup_rejects_private_directory_keys():
    from app.services.upload import UploadService

    assert UploadService.file_exists("course/secret.mp4") is False


def test_admin_course_writes_no_longer_accept_public_video_url():
    source = (REPO_ROOT / "app/schemas/admin_course.py").read_text(encoding="utf-8")
    create_schema = source[
        source.index("class AdminCourseCreate") : source.index(
            "class AdminCourseUpdate"
        )
    ]
    update_schema = source[
        source.index("class AdminCourseUpdate") : source.index(
            "class AdminCourseListItem"
        )
    ]

    assert "video_url" not in create_schema
    assert "video_url" not in update_schema


def test_payment_closed_callback_invokes_course_fulfillment():
    source = (REPO_ROOT / "app/services/payment.py").read_text(encoding="utf-8")

    assert 'transaction.trade_state in {"CLOSED", "REVOKED"}' in source
    assert "fulfillment_closed = await self.fulfillment.on_closed(db, order)" in source
