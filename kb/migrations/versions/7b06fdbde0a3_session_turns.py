"""session_turns

Revision ID: 7b06fdbde0a3
Revises: 69ccb440bc94
Create Date: 2026-07-16 00:00:00.000000

Raw SQL (not SQLAlchemy ORM models), matching 69ccb440bc94's style. Adds the
`session_turns` table backing KBRepository.add_turn()/list_turns(). Unlike
audit_log this table has no permanent-compliance-record requirement, so no
append-only triggers are installed here.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '7b06fdbde0a3'
down_revision: Union[str, Sequence[str], None] = '69ccb440bc94'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE session_turns (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions (id),
            turn_index INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            ts TEXT NOT NULL
        )
    """)
    op.execute("CREATE INDEX ix_session_turns_session ON session_turns (session_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_session_turns_session")
    op.execute("DROP TABLE IF EXISTS session_turns")
