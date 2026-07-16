"""auth_tables

Revision ID: 3a99fe629a01
Revises: f9112119455c
Create Date: 2026-07-16 00:00:00.000000

Raw SQL (not SQLAlchemy ORM models), matching f9112119455c's style. Adds the
`operators` and `auth_tokens` tables backing real auth (see auth/hashing.py).
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '3a99fe629a01'
down_revision: Union[str, Sequence[str], None] = 'f9112119455c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE operators (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    op.execute("""
        CREATE TABLE auth_tokens (
            token TEXT PRIMARY KEY,
            operator_id TEXT NOT NULL REFERENCES operators (id),
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
    """)
    op.execute("CREATE INDEX ix_auth_tokens_operator_id ON auth_tokens (operator_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_auth_tokens_operator_id")
    op.execute("DROP TABLE IF EXISTS auth_tokens")
    op.execute("DROP TABLE IF EXISTS operators")
