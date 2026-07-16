"""audit_log

Revision ID: 69ccb440bc94
Revises: 3a99fe629a01
Create Date: 2026-07-16 00:00:00.000000

Raw SQL (not SQLAlchemy ORM models), matching 3a99fe629a01's style. Adds the
`audit_log` table backing KBRepository.log_approval_change(). Append-only is
enforced at the DB level via BEFORE UPDATE/DELETE triggers that abort the
statement, not just by convention in application code.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '69ccb440bc94'
down_revision: Union[str, Sequence[str], None] = '3a99fe629a01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE audit_log (
            id TEXT PRIMARY KEY,
            ts TEXT NOT NULL,
            operator_id TEXT NOT NULL,
            tenant TEXT NOT NULL,
            record_kind TEXT NOT NULL,
            record_id TEXT NOT NULL,
            field TEXT NOT NULL,
            old_value TEXT NOT NULL,
            new_value TEXT NOT NULL
        )
    """)
    op.execute("CREATE INDEX ix_audit_log_tenant_record ON audit_log (tenant, record_id)")

    op.execute("""
        CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log
        BEGIN
            SELECT RAISE(ABORT, 'audit_log is append-only');
        END
    """)
    op.execute("""
        CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log
        BEGIN
            SELECT RAISE(ABORT, 'audit_log is append-only');
        END
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_delete")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_update")
    op.execute("DROP INDEX IF EXISTS ix_audit_log_tenant_record")
    op.execute("DROP TABLE IF EXISTS audit_log")
