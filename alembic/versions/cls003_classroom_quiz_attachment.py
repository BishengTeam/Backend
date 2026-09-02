"""classroom quiz attachments for essay questions

Revision ID: cls003_classroom_quiz_attachment
Revises: cls002_teacher_role
"""
from alembic import op
from collections.abc import Sequence

import sqlalchemy as sa


revision: str = 'cls003_classroom_quiz_attachment'
down_revision: str | Sequence[str] = 'cls002_teacher_role'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'classroom_quiz_attachment',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('quiz_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('question_id', sa.Integer(), nullable=False),
        sa.Column('submission_id', sa.Integer(), nullable=True),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('status', sa.String(length=16), server_default='uploaded', nullable=False),
        sa.Column('object_key', sa.String(length=512), nullable=False),
        sa.Column('filename', sa.String(length=256), nullable=False),
        sa.Column('content_type', sa.String(length=128), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('bound_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['quiz_id'], ['classroom_quiz.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.ForeignKeyConstraint(['question_id'], ['classroom_question.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['submission_id'], ['classroom_quiz_submission.id'], ondelete='SET NULL'),
        sa.CheckConstraint("kind IN ('image', 'document', 'archive')", name='ck_classroom_attachment_kind'),
        sa.CheckConstraint("status IN ('uploaded', 'bound')", name='ck_classroom_attachment_status'),
        sa.CheckConstraint('size_bytes > 0', name='ck_classroom_attachment_size'),
        sa.UniqueConstraint('object_key', name='uq_classroom_attachment_key'),
    )
    op.create_index(
        'ix_classroom_attachment_draft', 'classroom_quiz_attachment',
        ['quiz_id', 'user_id', 'question_id'],
    )
    op.create_index(
        'ix_classroom_attachment_user', 'classroom_quiz_attachment', ['user_id'],
    )
    op.create_index(
        'ix_classroom_attachment_submission', 'classroom_quiz_attachment', ['submission_id'],
    )


def downgrade() -> None:
    op.drop_index('ix_classroom_attachment_submission', table_name='classroom_quiz_attachment')
    op.drop_index('ix_classroom_attachment_user', table_name='classroom_quiz_attachment')
    op.drop_index('ix_classroom_attachment_draft', table_name='classroom_quiz_attachment')
    op.drop_table('classroom_quiz_attachment')
