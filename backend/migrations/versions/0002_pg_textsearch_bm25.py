"""Install the BM25 extension and index required by live retrieval."""

from alembic import op

revision = "0002_pg_textsearch_bm25"
down_revision = "0001_live_schema_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_textsearch")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunks_bm25 "
        "ON chunks USING bm25 (content) WITH (text_config = english)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_chunks_bm25")
    # Keep the extension; another schema may depend on it.
