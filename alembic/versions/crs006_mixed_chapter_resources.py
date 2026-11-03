"""Mixed video/PDF chapters. Shared course tables are owned by Backend only."""
from alembic import op
import sqlalchemy as sa

revision = "crs006"
down_revision = "r60002"
branch_labels = None
depends_on = None


def upgrade():
    # Preserve the existing 5GB video limit without PostgreSQL int32 overflow.
    op.alter_column("course_chapter", "size_bytes", type_=sa.BigInteger(), existing_type=sa.Integer())
    op.alter_column("course_upload", "size_bytes", type_=sa.BigInteger(), existing_type=sa.Integer())
    op.add_column("course_chapter", sa.Column("resource_type", sa.String(16), nullable=False, server_default="video"))
    op.add_column("course_chapter", sa.Column("resource_storage_key", sa.String(512)))
    op.add_column("course_chapter", sa.Column("resource_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("course_chapter", sa.Column("page_count", sa.Integer()))
    op.execute("UPDATE course_chapter SET resource_storage_key = video_storage_key")
    op.alter_column("course_chapter", "resource_storage_key", nullable=False)
    op.create_unique_constraint("uq_course_chapter_resource_key", "course_chapter", ["resource_storage_key"])
    op.drop_constraint("ck_course_chapter_duration_positive", "course_chapter", type_="check")
    op.alter_column("course_chapter", "duration", nullable=True)
    op.alter_column("course_chapter", "video_storage_key", nullable=True)
    op.create_check_constraint("ck_course_chapter_resource", "course_chapter",
        "(resource_type = 'video' AND duration > 0 AND duration IS NOT NULL AND page_count IS NULL AND video_storage_key IS NOT NULL) OR "
        "(resource_type = 'document' AND duration IS NULL AND page_count > 0 AND page_count IS NOT NULL AND video_storage_key IS NULL)")
    op.create_check_constraint("ck_course_chapter_version", "course_chapter", "resource_version > 0")
    op.drop_constraint("ck_course_upload_kind", "course_upload", type_="check")
    op.drop_constraint("ck_course_upload_status", "course_upload", type_="check")
    op.drop_constraint("ck_course_upload_kind_fields", "course_upload", type_="check")
    op.create_check_constraint("ck_course_upload_kind", "course_upload", "kind IN ('cover', 'chapter_video', 'chapter_file')")
    op.create_check_constraint("ck_course_upload_status", "course_upload", "status IN ('pending','validating','failed','completed','bound','retired','aborted','expired')")
    op.create_check_constraint("ck_course_upload_kind_fields", "course_upload",
        "(kind = 'cover' AND title IS NULL AND duration IS NULL AND sort_order IS NULL) OR kind = 'chapter_file' OR "
        "(kind = 'chapter_video' AND title IS NOT NULL AND duration IS NOT NULL AND sort_order IS NOT NULL)")
    for name, kind in [("resource_type", sa.String(16)), ("page_count", sa.Integer()), ("chapter_id", sa.Integer()),
                       ("expected_resource_version", sa.Integer()), ("validation_error", sa.String(1024))]:
        op.add_column("course_upload", sa.Column(name, kind))
    op.add_column("user_chapter_progress", sa.Column("last_page", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("user_chapter_progress", sa.Column("resource_version", sa.Integer(), nullable=False, server_default="1"))


def downgrade():
    raise RuntimeError("已有文档资源时不能删除字段；请关闭文档入口并保留数据")
