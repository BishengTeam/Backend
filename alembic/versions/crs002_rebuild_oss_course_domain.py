"""rebuild the online-course domain and entitlement integration

Revision ID: crs002
Revises: quiz008
Create Date: 2026-08-21 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "crs002"
down_revision: str | None = "quiz008"
branch_labels: str | None = None
depends_on: str | None = None


def _drop_course_domain() -> None:
    # The source installation contains fake course data.  Tables may or may not
    # exist depending on upgrade/downgrade direction, so use explicit ordered
    # IF EXISTS drops rather than inventing compatible state conversions.
    op.execute("DROP TABLE IF EXISTS quiz_course_library_binding")
    op.execute("DROP TABLE IF EXISTS course_entitlement_job_item")
    op.execute("DROP TABLE IF EXISTS course_entitlement_job")
    op.execute("DROP TABLE IF EXISTS course_audit_log")
    op.execute("DROP TABLE IF EXISTS quiz_library_entitlement")
    op.execute("DROP TABLE IF EXISTS user_chapter_progress")
    op.execute("DROP TABLE IF EXISTS course_enrollment")
    op.execute("DROP TABLE IF EXISTS course_upload")
    op.execute("DROP TABLE IF EXISTS course_asset")
    op.execute("DROP TABLE IF EXISTS course_chapter")
    op.execute("DROP TABLE IF EXISTS course_category")
    op.execute("DROP TABLE IF EXISTS course")


def upgrade() -> None:
    # The existing course data is explicitly test/fake data.  Rebuild instead
    # of trying to infer lifecycle, enrollment, asset, and entitlement states.
    _drop_course_domain()
    op.drop_constraint("ck_admin_user_role", "admin_user", type_="check")
    op.create_check_constraint(
        "ck_admin_user_role",
        "admin_user",
        "role IN ('super_admin', 'quiz_admin', 'h3c_admin', 'course_admin')",
    )

    op.create_table(
        "course",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("cover_storage_key", sa.String(length=512), nullable=False),
        sa.Column("price", sa.Integer(), nullable=False),
        sa.Column("teacher_name", sa.String(length=64), nullable=True),
        sa.Column("teacher_contact", sa.String(length=128), nullable=True),
        sa.Column("preview_chapter_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="draft", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("price >= 0", name="ck_course_price_nonnegative"),
        sa.CheckConstraint("status IN ('draft', 'published', 'offline', 'archived')", name="ck_course_status"),
        sa.CheckConstraint("preview_chapter_count >= 0", name="ck_course_preview_chapter_count_nonnegative"),
        sa.CheckConstraint(
            "((status = 'published' AND is_active = true) OR (status <> 'published' AND is_active = false))",
            name="ck_course_status_active_consistency",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cover_storage_key", name="uq_course_cover_storage_key"),
    )
    op.create_index("ix_course_category", "course", ["category"])
    op.create_index("ix_course_status", "course", ["status", "id"])

    op.create_table(
        "quiz_course_library_binding",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("library_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('active', 'inactive')", name="ck_quiz_course_library_binding_status"),
        sa.CheckConstraint("lock_version >= 1", name="ck_quiz_course_library_binding_lock_version"),
        sa.ForeignKeyConstraint(["course_id"], ["course.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["library_id"], ["quiz_library.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["admin_user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["admin_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("course_id", "library_id", name="uq_quiz_course_library_binding"),
    )
    op.create_index(
        "ix_quiz_course_library_binding_active",
        "quiz_course_library_binding",
        ["course_id", "status", "library_id"],
    )

    op.create_table(
        "course_category",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("sort_order >= 0", name="ck_course_category_sort_order"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_course_category_name", "course_category", ["name"], unique=True)

    op.create_table(
        "course_chapter",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("course.id"), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("video_storage_key", sa.String(length=512), nullable=False),
        sa.Column("original_filename", sa.String(length=256), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("duration", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("duration > 0", name="ck_course_chapter_duration_positive"),
        sa.CheckConstraint("size_bytes > 0", name="ck_course_chapter_size_positive"),
        sa.ForeignKeyConstraint(["course_id"], ["course.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("course_id", "sort_order", name="uq_course_chapter_sort"),
    )
    op.create_index(
        "ix_course_chapter_course_sort",
        "course_chapter",
        ["course_id", "sort_order", "id"],
    )

    op.create_table(
        "course_upload",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("oss_upload_id", sa.String(length=256), nullable=False),
        sa.Column("original_filename", sa.String(length=256), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("part_size", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("title", sa.String(length=256), nullable=True),
        sa.Column("duration", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("kind IN ('cover', 'chapter_video')", name="ck_course_upload_kind"),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'bound', 'aborted', 'expired')",
            name="ck_course_upload_status",
        ),
        sa.CheckConstraint("size_bytes > 0", name="ck_course_upload_size_positive"),
        sa.CheckConstraint("part_size > 0", name="ck_course_upload_part_size_positive"),
        sa.CheckConstraint(
            "(kind = 'cover' AND title IS NULL AND duration IS NULL AND sort_order IS NULL) "
            "OR (kind = 'chapter_video' AND title IS NOT NULL AND duration IS NOT NULL AND sort_order IS NOT NULL)",
            name="ck_course_upload_kind_fields",
        ),
        sa.CheckConstraint(
            "kind = 'cover' OR course_id IS NOT NULL",
            name="ck_course_upload_course_presence",
        ),
        sa.ForeignKeyConstraint(["course_id"], ["course.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key", name="uq_course_upload_object_key"),
    )
    op.create_index("ix_course_upload_course_status", "course_upload", ["course_id", "status", "id"])
    op.create_index("ix_course_upload_cleanup", "course_upload", ["status", "expires_at"])
    op.create_index("ix_course_upload_expires_at", "course_upload", ["expires_at"])

    op.create_table(
        "course_enrollment",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("course.id"), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="pending_payment", nullable=False),
        sa.Column("learning_access", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("access_granted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("access_revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('pending_payment', 'enrolled', 'completed', 'refunded', 'cancelled', 'expired')", name="ck_course_enrollment_status"),
        sa.CheckConstraint("learning_access IS FALSE OR status IN ('enrolled', 'completed')", name="ck_course_enrollment_learning_access"),
        sa.CheckConstraint("(order_id IS NULL AND status <> 'pending_payment') OR order_id IS NOT NULL", name="ck_course_enrollment_order_presence"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["course_id"], ["course.id"]),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["order.id"],
            name="fk_course_enrollment_order_id_order",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_course_enrollment_user_id", "course_enrollment", ["user_id"])
    op.create_index("ix_course_enrollment_course_id", "course_enrollment", ["course_id"])
    op.create_index("ix_course_enrollment_status", "course_enrollment", ["status"])
    op.create_index("uq_course_enrollment_order_id", "course_enrollment", ["order_id"], unique=True)
    op.create_index(
        "uq_course_enrollment_active_user_course",
        "course_enrollment",
        ["user_id", "course_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending_payment', 'enrolled', 'completed')"),
        sqlite_where=sa.text("status IN ('pending_payment', 'enrolled', 'completed')"),
    )

    op.create_table(
        "user_chapter_progress",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("course.id"), nullable=False),
        sa.Column("chapter_id", sa.Integer(), sa.ForeignKey("course_chapter.id"), nullable=False),
        sa.Column("last_position_seconds", sa.Integer(), nullable=False),
        sa.Column("is_completed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["course_id"], ["course.id"]),
        sa.ForeignKeyConstraint(["chapter_id"], ["course_chapter.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "course_id", "chapter_id", name="uq_user_chapter_progress"),
    )
    op.create_index("ix_user_chapter_progress_user_id", "user_chapter_progress", ["user_id"])
    op.create_index("ix_user_chapter_progress_course_id", "user_chapter_progress", ["course_id"])

    op.create_table(
        "quiz_library_entitlement",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("library_id", sa.BigInteger(), sa.ForeignKey("quiz_library.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("course.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("order.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("enrollment_id", sa.Integer(), sa.ForeignKey("course_enrollment.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("source_type", sa.String(length=24), server_default="course_order", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('active', 'revoked', 'expired')", name="ck_quiz_library_entitlement_status"),
        sa.CheckConstraint("source_type IN ('course_order', 'course_enrollment')", name="ck_quiz_library_entitlement_source_type"),
        sa.CheckConstraint("ends_at IS NULL OR ends_at > starts_at", name="ck_quiz_library_entitlement_period"),
        sa.CheckConstraint(
            "((source_type = 'course_order' AND order_id IS NOT NULL) OR (source_type = 'course_enrollment' AND enrollment_id IS NOT NULL))",
            name="ck_quiz_library_entitlement_source_ref",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["library_id"], ["quiz_library.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["course_id"], ["course.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["order_id"], ["order.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["enrollment_id"], ["course_enrollment.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "library_id", name="uq_quiz_library_entitlement_order_library"),
    )
    op.create_index("uq_quiz_library_entitlement_enrollment_library", "quiz_library_entitlement", ["enrollment_id", "library_id"], unique=True, postgresql_where=sa.text("enrollment_id IS NOT NULL"), sqlite_where=sa.text("enrollment_id IS NOT NULL"))
    op.create_index("ix_quiz_library_entitlement_access", "quiz_library_entitlement", ["user_id", "library_id", "status", "starts_at", "ends_at"])

    op.create_table(
        "course_audit_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("actor_type", sa.String(length=16), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("object_type", sa.String(length=64), nullable=False),
        sa.Column("object_id", sa.Integer(), nullable=True),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("changed_fields", sa.JSON(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("result IN ('succeeded', 'failed')", name="ck_course_audit_result"),
        sa.CheckConstraint("actor_type IN ('admin', 'system', 'user')", name="ck_course_audit_actor_type"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_course_audit_object", "course_audit_log", ["object_type", "object_id", "created_at"])
    op.create_index("ix_course_audit_actor", "course_audit_log", ["actor_type", "actor_id", "created_at"])
    op.create_index("ix_course_audit_action", "course_audit_log", ["action", "created_at"])

    op.create_table(
        "course_entitlement_job",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("course.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("library_id", sa.Integer(), sa.ForeignKey("quiz_library.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("binding_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column("batch_size", sa.Integer(), server_default="500", nullable=False),
        sa.Column("total_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("processed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("success_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failure_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.String(length=1024), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("admin_user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("action IN ('backfill', 'revoke')", name="ck_course_entitlement_job_action"),
        sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'partial', 'failed')", name="ck_course_entitlement_job_status"),
        sa.CheckConstraint("batch_size > 0", name="ck_course_entitlement_job_batch_size"),
        sa.CheckConstraint("success_count >= 0 AND failure_count >= 0 AND processed_count >= 0 AND total_count >= 0 AND success_count + failure_count = processed_count AND processed_count <= total_count", name="ck_course_entitlement_job_counts"),
        sa.CheckConstraint("retry_count >= 0", name="ck_course_entitlement_job_retry_count"),
        sa.ForeignKeyConstraint(["course_id"], ["course.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["library_id"], ["quiz_library.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["admin_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_course_entitlement_job_queue", "course_entitlement_job", ["status", "created_at", "id"])
    op.create_index("ix_course_entitlement_job_course", "course_entitlement_job", ["course_id", "id"])

    op.create_table(
        "course_entitlement_job_item",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("course_entitlement_job.id", ondelete="CASCADE"), nullable=False),
        sa.Column("enrollment_id", sa.Integer(), sa.ForeignKey("course_enrollment.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("error_message", sa.String(length=1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'succeeded', 'failed')", name="ck_course_entitlement_job_item_status"),
        sa.ForeignKeyConstraint(["job_id"], ["course_entitlement_job.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["enrollment_id"], ["course_enrollment.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_course_entitlement_job_item_job", "course_entitlement_job_item", ["job_id", "status", "id"])
    op.create_index("ix_course_entitlement_job_item_enrollment", "course_entitlement_job_item", ["enrollment_id", "id"])


def downgrade() -> None:
    # Downgrade is a structural rollback for a clean course-domain rebuild.
    # It intentionally does not resurrect fake business data.
    _drop_course_domain()
    op.execute("UPDATE admin_user SET role = 'quiz_admin' WHERE role = 'course_admin'")
    op.drop_constraint("ck_admin_user_role", "admin_user", type_="check")
    op.create_check_constraint(
        "ck_admin_user_role",
        "admin_user",
        "role IN ('super_admin', 'quiz_admin', 'h3c_admin')",
    )

    op.create_table(
        "course",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("cover_url", sa.String(length=512), nullable=True),
        sa.Column("video_url", sa.String(length=512), nullable=True),
        sa.Column("price", sa.Integer(), nullable=False),
        sa.Column("batches", sa.JSON(), nullable=True),
        sa.Column("teacher_name", sa.String(length=64), nullable=True),
        sa.Column("teacher_contact", sa.String(length=128), nullable=True),
        sa.Column("free_preview_seconds", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("price >= 0", name="ck_course_price_nonnegative"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_course_category", "course", ["category"])

    op.create_table(
        "quiz_course_library_binding",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("library_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('active', 'inactive')", name="ck_quiz_course_library_binding_status"),
        sa.CheckConstraint("lock_version >= 1", name="ck_quiz_course_library_binding_lock_version"),
        sa.ForeignKeyConstraint(["course_id"], ["course.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["library_id"], ["quiz_library.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["admin_user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["admin_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("course_id", "library_id", name="uq_quiz_course_library_binding"),
    )
    op.create_index(
        "ix_quiz_course_library_binding_active",
        "quiz_course_library_binding",
        ["course_id", "status", "library_id"],
    )

    op.create_table(
        "course_chapter",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("course.id"), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("video_url", sa.String(length=512), nullable=True),
        sa.Column("duration", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["course_id"], ["course.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("course_id", "sort_order", name="uq_course_chapter_sort"),
    )

    op.create_table(
        "user_chapter_progress",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("course.id"), nullable=False),
        sa.Column("chapter_id", sa.Integer(), sa.ForeignKey("course_chapter.id"), nullable=False),
        sa.Column("last_position_seconds", sa.Integer(), nullable=False),
        sa.Column("is_completed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["course_id"], ["course.id"]),
        sa.ForeignKeyConstraint(["chapter_id"], ["course_chapter.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "course_id", "chapter_id", name="uq_user_chapter_progress"),
    )
    op.create_index("ix_user_chapter_progress_user_id", "user_chapter_progress", ["user_id"])
    op.create_index("ix_user_chapter_progress_course_id", "user_chapter_progress", ["course_id"])

    op.create_table(
        "course_asset",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("course.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("asset_type", sa.String(length=32), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_preview", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("sort_order >= 0", name="ck_course_asset_sort_order_nonnegative"),
        sa.ForeignKeyConstraint(["course_id"], ["course.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key", name="uq_course_asset_storage_key"),
    )
    op.create_index("ix_course_asset_course_sort", "course_asset", ["course_id", "sort_order", "id"])

    op.create_table(
        "course_enrollment",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("course.id"), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("batch_selected", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="pending_payment", nullable=False),
        sa.Column("learning_access", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("access_granted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("access_revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('pending_payment', 'enrolled', 'completed', 'refunded', 'cancelled', 'expired')", name="ck_course_enrollment_status"),
        sa.CheckConstraint("learning_access IS FALSE OR status IN ('enrolled', 'completed')", name="ck_course_enrollment_learning_access"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["course_id"], ["course.id"]),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["order.id"],
            name="fk_course_enrollment_order_id_order",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_course_enrollment_user_id", "course_enrollment", ["user_id"])
    op.create_index("ix_course_enrollment_course_id", "course_enrollment", ["course_id"])
    op.create_index("ix_course_enrollment_status", "course_enrollment", ["status"])
    op.create_index("uq_course_enrollment_order_id", "course_enrollment", ["order_id"], unique=True)
    op.create_index("uq_course_enrollment_active_user_course", "course_enrollment", ["user_id", "course_id"], unique=True, postgresql_where=sa.text("status IN ('pending_payment', 'enrolled', 'completed')"), sqlite_where=sa.text("status IN ('pending_payment', 'enrolled', 'completed')"))

    op.create_table(
        "quiz_library_entitlement",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("library_id", sa.BigInteger(), sa.ForeignKey("quiz_library.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("course.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("order.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_type", sa.String(length=24), server_default="course_order", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('active', 'revoked', 'expired')", name="ck_quiz_library_entitlement_status"),
        sa.CheckConstraint("source_type = 'course_order'", name="ck_quiz_library_entitlement_source_type"),
        sa.CheckConstraint("ends_at IS NULL OR ends_at > starts_at", name="ck_quiz_library_entitlement_period"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["library_id"], ["quiz_library.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["course_id"], ["course.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["order_id"], ["order.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "library_id", name="uq_quiz_library_entitlement_order_library"),
    )
    op.create_index("ix_quiz_library_entitlement_access", "quiz_library_entitlement", ["user_id", "library_id", "status", "starts_at", "ends_at"])
