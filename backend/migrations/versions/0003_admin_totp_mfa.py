"""Add encrypted TOTP MFA for administrator accounts."""

from alembic import op

revision = "0003_admin_totp_mfa"
down_revision = "0002_pg_textsearch_bm25"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN admin_mfa_secret_ciphertext text")
    op.execute("ALTER TABLE users ADD COLUMN admin_mfa_pending_secret_ciphertext text")
    op.execute("ALTER TABLE users ADD COLUMN admin_mfa_pending_expires_at timestamptz")
    op.execute("ALTER TABLE users ADD COLUMN admin_mfa_enabled_at timestamptz")
    op.execute("ALTER TABLE users ADD COLUMN admin_mfa_last_counter bigint NOT NULL DEFAULT -1")
    op.execute(
        "ALTER TABLE user_sessions ADD COLUMN admin_mfa_verified boolean NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE user_sessions DROP COLUMN admin_mfa_verified")
    op.execute("ALTER TABLE users DROP COLUMN admin_mfa_last_counter")
    op.execute("ALTER TABLE users DROP COLUMN admin_mfa_enabled_at")
    op.execute("ALTER TABLE users DROP COLUMN admin_mfa_pending_expires_at")
    op.execute("ALTER TABLE users DROP COLUMN admin_mfa_pending_secret_ciphertext")
    op.execute("ALTER TABLE users DROP COLUMN admin_mfa_secret_ciphertext")
