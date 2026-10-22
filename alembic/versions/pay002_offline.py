"""Private bank transfer workflow; ambiguous historical channels remain unknown."""
from alembic import op
import sqlalchemy as sa
revision = "pay002_offline"
down_revision = "cp019_comp_display_flags"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("order", sa.Column("payment_channel", sa.String(24), nullable=False, server_default="unknown"))
    op.add_column("order", sa.Column("offline_status", sa.String(24)))
    op.add_column("order", sa.Column("offline_submitted_at", sa.DateTime(timezone=True)))
    op.create_index("ix_order_payment_channel", "order", ["payment_channel"])
    op.create_index("ix_order_offline_status", "order", ["offline_status"])
    op.execute("UPDATE \"order\" SET payment_channel='wechat' WHERE transaction_id IS NOT NULL AND price > 0")
    op.execute("UPDATE \"order\" SET payment_channel='free' WHERE price=0 AND status IN ('paid','completed','refunded')")
    op.create_table('offline_payment_settings',
        sa.Column('data', sa.JSON(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('id', sa.Integer(), nullable=False, primary_key=True, autoincrement=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.create_table('offline_payment',
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('account_snapshot', sa.JSON(), nullable=False),
        sa.Column('account_fingerprint', sa.String(length=64), nullable=False),
        sa.Column('current_version', sa.Integer(), nullable=False),
        sa.Column('deadline', sa.DateTime(timezone=True), nullable=True),
        sa.Column('confirmed_by', sa.Integer(), nullable=True),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('message', sa.String(length=1000), nullable=True),
        sa.Column('id', sa.Integer(), nullable=False, primary_key=True, autoincrement=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['order_id'], ['order.id']),
        sa.UniqueConstraint('order_id'),
    )
    op.create_table('offline_payment_material',
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('admin_id', sa.Integer(), nullable=True),
        sa.Column('storage_key', sa.String(length=512), nullable=False),
        sa.Column('filename', sa.String(length=256), nullable=False),
        sa.Column('content_type', sa.String(length=64), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('id', sa.Integer(), nullable=False, primary_key=True, autoincrement=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['order_id'], ['order.id']),
    )
    op.create_index('ix_offline_payment_material_order_id', 'offline_payment_material', ['order_id'], unique=False)
    op.create_table('offline_payment_submission',
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('reason', sa.String(length=1000), nullable=True),
        sa.Column('id', sa.Integer(), nullable=False, primary_key=True, autoincrement=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['order_id'], ['order.id']),
        sa.UniqueConstraint('order_id', 'version', name='uq_offline_submission_version'),
    )
    op.create_index('ix_offline_payment_submission_order_id', 'offline_payment_submission', ['order_id'], unique=False)
    op.create_table('offline_bank_entry',
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('account_fingerprint', sa.String(length=64), nullable=False),
        sa.Column('bank_reference', sa.String(length=128), nullable=False),
        sa.Column('amount_cents', sa.Integer(), nullable=False),
        sa.Column('bank_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('admin_id', sa.Integer(), nullable=False),
        sa.Column('submission_version', sa.Integer(), nullable=False),
        sa.Column('material_ids', sa.JSON(), nullable=False),
        sa.Column('id', sa.Integer(), nullable=False, primary_key=True, autoincrement=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.CheckConstraint('amount_cents > 0', name='ck_offline_entry_amount'),
        sa.ForeignKeyConstraint(['order_id'], ['order.id']),
        sa.UniqueConstraint('account_fingerprint', 'bank_reference', 'kind', name='uq_offline_bank_entry'),
        sa.UniqueConstraint('order_id', 'kind', name='uq_offline_order_entry'),
    )
    op.create_table('offline_payment_audit',
        sa.Column('order_id', sa.Integer(), nullable=True),
        sa.Column('actor_id', sa.Integer(), nullable=False),
        sa.Column('action', sa.String(length=40), nullable=False),
        sa.Column('request_key', sa.String(length=128), nullable=False),
        sa.Column('payload_hash', sa.String(length=64), nullable=False),
        sa.Column('summary', sa.JSON(), nullable=False),
        sa.Column('id', sa.Integer(), nullable=False, primary_key=True, autoincrement=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['order_id'], ['order.id']),
        sa.UniqueConstraint('order_id', 'action', 'request_key', name='uq_offline_command'),
    )
    op.create_index('ix_offline_payment_audit_order_id', 'offline_payment_audit', ['order_id'], unique=False)


def downgrade():
    raise RuntimeError("Offline orders require their reconciliation and refund data; restore application compatibility instead of deleting financial history")
