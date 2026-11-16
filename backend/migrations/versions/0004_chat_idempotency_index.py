"""Add idempotency protection to completed chat rows without a new table."""

from alembic import op

revision = "0004_chat_idempotency_index"
down_revision = "0003_admin_totp_mfa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE UNIQUE INDEX query_runs_user_idempotency_key_uq
           ON query_runs (user_uuid, (metadata->>'idempotency_key_hash'))
           WHERE request_type = 'CHAT'
             AND response_text IS NOT NULL
             AND metadata ? 'idempotency_key_hash'"""
    )


def downgrade() -> None:
    op.execute("DROP INDEX query_runs_user_idempotency_key_uq")
