"""classroom module (offline teaching)

Revision ID: cls001_classroom_module
Revises: assignment001
"""
from alembic import op
from collections.abc import Sequence

import sqlalchemy as sa


revision: str = 'cls001_classroom_module'
down_revision: str | Sequence[str] = 'quiz012'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'classroom',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('teacher_admin_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=16), server_default='active', nullable=False),
        sa.Column('join_code', sa.String(length=8), nullable=True),
        sa.Column('join_code_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('stopped_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['teacher_admin_id'], ['admin_user.id']),
        sa.CheckConstraint("status IN ('active', 'stopped')", name='ck_classroom_status'),
    )
    op.create_index('ix_classroom_teacher_admin_id', 'classroom', ['teacher_admin_id'])

    op.create_table(
        'classroom_member',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('classroom_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('real_name_snapshot', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['classroom_id'], ['classroom.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.UniqueConstraint('classroom_id', 'user_id', name='uq_classroom_member'),
    )
    op.create_index('ix_classroom_member_classroom_id', 'classroom_member', ['classroom_id'])
    op.create_index('ix_classroom_member_user_id', 'classroom_member', ['user_id'])

    op.create_table(
        'classroom_video',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('classroom_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=128), nullable=False),
        sa.Column('storage_key', sa.String(length=512), nullable=False),
        sa.Column('duration_seconds', sa.Integer(), server_default='0', nullable=False),
        sa.Column('size_bytes', sa.Integer(), server_default='0', nullable=False),
        sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['classroom_id'], ['classroom.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_classroom_video_classroom_id', 'classroom_video', ['classroom_id'])

    op.create_table(
        'classroom_question',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('classroom_id', sa.Integer(), nullable=False),
        sa.Column('type', sa.String(length=16), nullable=False),
        sa.Column('stem', sa.Text(), nullable=False),
        sa.Column('options', sa.JSON(), nullable=True),
        sa.Column('answer', sa.Text(), nullable=True),
        sa.Column('analysis', sa.Text(), nullable=True),
        sa.Column('score', sa.Integer(), server_default='1', nullable=False),
        sa.Column('status', sa.String(length=16), server_default='draft', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['classroom_id'], ['classroom.id'], ondelete='CASCADE'),
        sa.CheckConstraint("type IN ('single', 'multiple', 'judge', 'blank', 'short')", name='ck_classroom_question_type'),
        sa.CheckConstraint("status IN ('draft', 'published')", name='ck_classroom_question_status'),
    )
    op.create_index('ix_classroom_question_classroom_id', 'classroom_question', ['classroom_id'])

    op.create_table(
        'classroom_quiz',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('classroom_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=128), nullable=False),
        sa.Column('duration_minutes', sa.Integer(), nullable=False),
        sa.Column('question_ids', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=16), server_default='ongoing', nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['classroom_id'], ['classroom.id'], ondelete='CASCADE'),
        sa.CheckConstraint("status IN ('ongoing', 'ended')", name='ck_classroom_quiz_status'),
    )
    op.create_index('ix_classroom_quiz_classroom_id', 'classroom_quiz', ['classroom_id'])

    op.create_table(
        'classroom_quiz_submission',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('quiz_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('answers', sa.JSON(), nullable=False),
        sa.Column('auto_score', sa.Integer(), server_default='0', nullable=False),
        sa.Column('manual_score', sa.Integer(), server_default='0', nullable=False),
        sa.Column('manual_scores', sa.JSON(), nullable=True),
        sa.Column('total_score', sa.Integer(), server_default='0', nullable=False),
        sa.Column('status', sa.String(length=16), server_default='pending_review', nullable=False),
        sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['quiz_id'], ['classroom_quiz.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.CheckConstraint("status IN ('pending_review', 'approved')", name='ck_classroom_submission_status'),
        sa.UniqueConstraint('quiz_id', 'user_id', name='uq_classroom_submission'),
    )
    op.create_index('ix_classroom_quiz_submission_quiz_id', 'classroom_quiz_submission', ['quiz_id'])
    op.create_index('ix_classroom_quiz_submission_user_id', 'classroom_quiz_submission', ['user_id'])


def downgrade() -> None:
    op.drop_table('classroom_quiz_submission')
    op.drop_table('classroom_quiz')
    op.drop_table('classroom_question')
    op.drop_table('classroom_video')
    op.drop_table('classroom_member')
    op.drop_index('ix_classroom_teacher_admin_id', table_name='classroom')
    op.drop_table('classroom')
