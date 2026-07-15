"""baseline

Revision ID: f9112119455c
Revises:
Create Date: 2026-07-15 19:14:54.110412

Raw SQL (not SQLAlchemy ORM models) — see kb/repository.py COLUMNS/JSON_COLUMNS,
which this schema must stay in sync with. `tenant` is a real contract field only
on `businesses`; elsewhere it's a KB-internal column the repository resolves and
stripped again on read.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f9112119455c'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("businesses", "sessions", "tasks", "workflow_graphs", "opportunities", "recommendations", "automations")


def upgrade() -> None:
    op.execute("""
        CREATE TABLE businesses (
            id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL DEFAULT 1,
            tenant TEXT NOT NULL,
            name TEXT NOT NULL,
            meta TEXT NOT NULL DEFAULT '{}'
        )
    """)
    op.execute("CREATE INDEX ix_businesses_tenant ON businesses (tenant)")

    op.execute("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL DEFAULT 1,
            business_id TEXT NOT NULL REFERENCES businesses (id),
            status TEXT NOT NULL DEFAULT 'active',
            transcript_ref TEXT,
            tenant TEXT NOT NULL
        )
    """)
    op.execute("CREATE INDEX ix_sessions_business_id ON sessions (business_id)")
    op.execute("CREATE INDEX ix_sessions_tenant ON sessions (tenant)")

    op.execute("""
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL DEFAULT 1,
            session_id TEXT NOT NULL REFERENCES sessions (id),
            task TEXT NOT NULL,
            frequency TEXT NOT NULL,
            frequency_per_week REAL NOT NULL,
            time_spent_min INTEGER NOT NULL,
            pain_level INTEGER NOT NULL,
            tools_used TEXT NOT NULL DEFAULT '[]',
            dependencies TEXT NOT NULL DEFAULT '[]',
            desired_outcome TEXT NOT NULL,
            tenant TEXT NOT NULL
        )
    """)
    op.execute("CREATE INDEX ix_tasks_session_id ON tasks (session_id)")
    op.execute("CREATE INDEX ix_tasks_tenant ON tasks (tenant)")

    op.execute("""
        CREATE TABLE workflow_graphs (
            id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL DEFAULT 1,
            session_id TEXT NOT NULL REFERENCES sessions (id),
            nodes TEXT NOT NULL,
            edges TEXT NOT NULL,
            bottlenecks TEXT NOT NULL DEFAULT '[]',
            tenant TEXT NOT NULL
        )
    """)
    op.execute("CREATE INDEX ix_workflow_graphs_session_id ON workflow_graphs (session_id)")
    op.execute("CREATE INDEX ix_workflow_graphs_tenant ON workflow_graphs (tenant)")

    op.execute("""
        CREATE TABLE opportunities (
            id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL DEFAULT 1,
            task_ids TEXT NOT NULL,
            roi_low_hrs REAL NOT NULL,
            roi_high_hrs REAL NOT NULL,
            assumptions TEXT NOT NULL,
            complexity INTEGER NOT NULL,
            confidence REAL NOT NULL,
            crosscheck_flags TEXT NOT NULL DEFAULT '[]',
            tenant TEXT NOT NULL
        )
    """)
    op.execute("CREATE INDEX ix_opportunities_tenant ON opportunities (tenant)")

    op.execute("""
        CREATE TABLE recommendations (
            id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL DEFAULT 1,
            opportunity_id TEXT NOT NULL REFERENCES opportunities (id),
            summary TEXT NOT NULL,
            approval_state TEXT NOT NULL DEFAULT 'draft',
            tenant TEXT NOT NULL
        )
    """)
    op.execute("CREATE INDEX ix_recommendations_opportunity_id ON recommendations (opportunity_id)")
    op.execute("CREATE INDEX ix_recommendations_tenant ON recommendations (tenant)")

    op.execute("""
        CREATE TABLE automations (
            id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL DEFAULT 1,
            recommendation_id TEXT NOT NULL REFERENCES recommendations (id),
            spec TEXT NOT NULL,
            blast_radius TEXT NOT NULL,
            rollback TEXT NOT NULL,
            approval_state TEXT NOT NULL DEFAULT 'draft',
            tenant TEXT NOT NULL
        )
    """)
    op.execute("CREATE INDEX ix_automations_recommendation_id ON automations (recommendation_id)")
    op.execute("CREATE INDEX ix_automations_tenant ON automations (tenant)")


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
