"""Baseline snapshot of the verified Cloud SQL public schema.

The existing production database was provisioned outside Alembic. After a schema
comparison, stamp this revision on that database once; do not run this upgrade there.
"""

import re
from pathlib import Path

from alembic import op

revision = "0001_live_schema_baseline"
down_revision = None
branch_labels = None
depends_on = None

_DOLLAR_QUOTE = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")


def _split_sql(sql: str) -> list[str]:
    """Split the schema-only pg_dump while respecting quoted strings and comments."""
    statements: list[str] = []
    current: list[str] = []
    index = 0
    single_quoted = False
    double_quoted = False
    dollar_quote: str | None = None
    line_comment = False
    block_comment = False

    while index < len(sql):
        char = sql[index]
        next_two = sql[index : index + 2]

        if line_comment:
            if char == "\n":
                line_comment = False
                current.append(char)
            index += 1
            continue
        if block_comment:
            if next_two == "*/":
                block_comment = False
                index += 2
            else:
                index += 1
            continue
        if dollar_quote is not None:
            if sql.startswith(dollar_quote, index):
                current.append(dollar_quote)
                index += len(dollar_quote)
                dollar_quote = None
            else:
                current.append(char)
                index += 1
            continue
        if single_quoted:
            current.append(char)
            if char == "'":
                if index + 1 < len(sql) and sql[index + 1] == "'":
                    current.append(sql[index + 1])
                    index += 2
                    continue
                single_quoted = False
            index += 1
            continue
        if double_quoted:
            current.append(char)
            if char == '"':
                if index + 1 < len(sql) and sql[index + 1] == '"':
                    current.append(sql[index + 1])
                    index += 2
                    continue
                double_quoted = False
            index += 1
            continue

        if next_two == "--":
            line_comment = True
            index += 2
            continue
        if next_two == "/*":
            block_comment = True
            index += 2
            continue
        if char == "'":
            single_quoted = True
            current.append(char)
            index += 1
            continue
        if char == '"':
            double_quoted = True
            current.append(char)
            index += 1
            continue
        if char == "$":
            match = _DOLLAR_QUOTE.match(sql, index)
            if match:
                dollar_quote = match.group(0)
                current.append(dollar_quote)
                index = match.end()
                continue
        if char == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current.clear()
            index += 1
            continue

        current.append(char)
        index += 1

    trailing = "".join(current).strip()
    if trailing:
        statements.append(trailing)
    return statements


def upgrade() -> None:
    schema_path = Path(__file__).with_name("0001_live_schema_baseline.sql")
    schema_sql = schema_path.read_text(encoding="utf-8").lstrip("\ufeff")
    for statement in _split_sql(schema_sql):
        normalized = statement.lstrip().upper()
        # pg_dump session configuration is version-specific and is not part of
        # the schema. In particular, pg_dump 17 emits a SET unsupported by PG16.
        if normalized.startswith("SET ") or normalized.startswith("SELECT PG_CATALOG.SET_CONFIG("):
            continue
        # psql's \restrict markers are removed from the checked-in snapshot.
        if statement.lstrip().startswith("\\"):
            continue
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError(
        "The live-schema baseline is intentionally non-reversible; "
        "dropping it would delete application data."
    )
