"""Stable normal import defaults and question ownership provenance."""
from alembic import op
import sqlalchemy as sa

revision = "quiz019"
down_revision = "cjr001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("quiz_import_job", sa.Column("source_filename", sa.String(256)))
    for table, parent, index in (("quiz_module", "library_id", "uq_quiz_module_import_default"),
                                 ("quiz_knowledge_point", "module_id", "uq_quiz_point_import_default")):
        op.add_column(table, sa.Column("is_import_default", sa.Boolean(), nullable=False, server_default=sa.false()))
        op.create_index(index, table, [parent], unique=True,
                        postgresql_where=sa.text("is_import_default = true AND status <> 'deleted'"),
                        sqlite_where=sa.text("is_import_default = true AND status <> 'deleted'"))
    op.add_column("quiz_question", sa.Column("import_default_ownership", sa.Boolean(), nullable=False, server_default=sa.false()))
    from sqlalchemy.dialects.postgresql import JSONB
    op.create_table("quiz_publication_operation",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("library_id", sa.BigInteger(), sa.ForeignKey("quiz_library.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("admin_id", sa.Integer(), sa.ForeignKey("admin_user.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("request_key", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("payload", JSONB(), nullable=False), sa.Column("progress", JSONB(), nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_quiz_publication_queue", "quiz_publication_operation", ["status", "id"])


def downgrade():
    op.drop_column("quiz_import_job", "source_filename")
    op.drop_table("quiz_publication_operation")
    op.drop_column("quiz_question", "import_default_ownership")
    op.drop_index("uq_quiz_point_import_default", table_name="quiz_knowledge_point")
    op.drop_index("uq_quiz_module_import_default", table_name="quiz_module")
    op.drop_column("quiz_knowledge_point", "is_import_default")
    op.drop_column("quiz_module", "is_import_default")
