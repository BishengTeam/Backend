from pathlib import Path

import pytest

from app.port.exceptions import ForbiddenException, NotFoundException
from app.services.course_asset import CourseAssetService, CourseAssetStorage
from app.services.upload import UploadService


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


def test_course_enrollment_model_declares_lifecycle_constraints_and_indexes():
    source = (
        REPO_ROOT / "app/domain/certification/src/model/course.py"
    ).read_text(encoding="utf-8")

    assert "pending_payment" in source
    assert "uq_course_enrollment_active_user_course" in source
    assert "uq_course_enrollment_order_id" in source
    assert "access_granted_at" in source
    assert "access_revoked_at" in source
    assert "CourseAsset" in source


def test_private_course_asset_path_rejects_traversal():
    with pytest.raises(NotFoundException):
        CourseAssetStorage.resolve("../public-file.mp4")


def test_course_asset_playback_signature_binds_user_asset_and_expiry():
    expires_at = 1_800_000_000
    signature = CourseAssetService.create_playback_signature(10, 20, expires_at)

    CourseAssetService.verify_playback_signature(
        10,
        20,
        expires_at,
        signature,
        now=expires_at - 1,
    )

    with pytest.raises(ForbiddenException, match="签名无效"):
        CourseAssetService.verify_playback_signature(
            11,
            20,
            expires_at,
            signature,
            now=expires_at - 1,
        )


def test_course_asset_playback_signature_expires():
    expires_at = 1_800_000_000
    signature = CourseAssetService.create_playback_signature(10, 20, expires_at)

    with pytest.raises(ForbiddenException, match="已过期"):
        CourseAssetService.verify_playback_signature(
            10,
            20,
            expires_at,
            signature,
            now=expires_at,
        )


def test_public_media_lookup_rejects_private_directory_keys():
    assert UploadService.file_exists("private/course-assets/1/secret.mp4") is False


def test_admin_course_writes_no_longer_accept_public_video_url():
    source = (REPO_ROOT / "app/schemas/admin_course.py").read_text(encoding="utf-8")
    create_schema = source[source.index("class AdminCourseCreate") : source.index("class AdminCourseUpdate")]
    update_schema = source[source.index("class AdminCourseUpdate") : source.index("class AdminCourseListItem")]

    assert "video_url" not in create_schema
    assert "video_url" not in update_schema


def test_payment_closed_callback_invokes_course_fulfillment():
    source = (REPO_ROOT / "app/services/payment.py").read_text(encoding="utf-8")

    assert 'data.trade_state in {"CLOSED", "REVOKED"}' in source
    assert "await self.fulfillment.on_closed(db, order)" in source
