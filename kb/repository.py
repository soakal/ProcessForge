"""KB repository: get/put/list_by_session/list_by_business.

Tenant isolation is enforced here, not by callers (§9) — every query is scoped by
tenant. Business/Session carry tenant directly; Task/WorkflowGraph/Opportunity/
Recommendation/Automation don't carry it in the frozen contract (§3), so on put()
the repository resolves it transitively through the parent chain and stores it as
a KB-only `tenant` column (not part of the Pydantic contract, stripped on read).
"""
from __future__ import annotations
import json
import sqlite3

COLUMNS: dict[str, list[str]] = {
    "businesses": ["id", "schema_version", "tenant", "name", "meta"],
    "sessions": ["id", "schema_version", "business_id", "status", "transcript_ref", "tenant"],
    "tasks": [
        "id", "schema_version", "session_id", "task", "frequency", "frequency_per_week",
        "time_spent_min", "pain_level", "tools_used", "dependencies", "desired_outcome", "tenant",
    ],
    "workflow_graphs": ["id", "schema_version", "session_id", "nodes", "edges", "bottlenecks", "tenant"],
    "opportunities": [
        "id", "schema_version", "task_ids", "roi_low_hrs", "roi_high_hrs", "assumptions",
        "complexity", "confidence", "crosscheck_flags", "tenant",
    ],
    "recommendations": ["id", "schema_version", "opportunity_id", "summary", "approval_state", "tenant"],
    "automations": [
        "id", "schema_version", "recommendation_id", "spec", "blast_radius", "rollback",
        "approval_state", "tenant",
    ],
}

JSON_COLUMNS: dict[str, list[str]] = {
    "businesses": ["meta"],
    "tasks": ["tools_used", "dependencies"],
    "workflow_graphs": ["nodes", "edges", "bottlenecks"],
    "opportunities": ["task_ids", "assumptions", "crosscheck_flags"],
    "automations": ["spec"],
}

# tenant is a real contract field only on Business; elsewhere it's a KB-internal column.
_CONTRACT_HAS_TENANT = {"businesses"}


class TenantMismatchError(ValueError):
    """Raised when a record's tenant cannot be unambiguously resolved from its parent chain."""


class KBRepository:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self._conn.close()

    def get(self, kind: str, id: str, tenant: str) -> dict | None:
        cols = COLUMNS[kind]
        row = self._conn.execute(
            f"SELECT {', '.join(cols)} FROM {kind} WHERE id = ? AND tenant = ?", (id, tenant)
        ).fetchone()
        return self._deserialize(kind, row) if row else None

    def put(self, kind: str, record: dict) -> None:
        if kind not in COLUMNS:
            raise ValueError(f"unknown kind {kind!r}")
        tenant = self._resolve_tenant(kind, record)
        cols = COLUMNS[kind]
        row = self._serialize(kind, record, tenant)
        placeholders = ", ".join("?" for _ in cols)
        assignments = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "id")
        self._conn.execute(
            f"INSERT INTO {kind} ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {assignments}",
            [row[c] for c in cols],
        )
        self._conn.commit()

    def list_by_session(self, kind: str, session_id: str, tenant: str) -> list[dict]:
        if "session_id" not in COLUMNS[kind]:
            raise ValueError(f"{kind} has no session_id column")
        cols = COLUMNS[kind]
        rows = self._conn.execute(
            f"SELECT {', '.join(cols)} FROM {kind} WHERE session_id = ? AND tenant = ?",
            (session_id, tenant),
        ).fetchall()
        return [self._deserialize(kind, r) for r in rows]

    def list_by_business(self, kind: str, business_id: str, tenant: str) -> list[dict]:
        cols = COLUMNS[kind]
        if kind == "sessions":
            rows = self._conn.execute(
                f"SELECT {', '.join(cols)} FROM sessions WHERE business_id = ? AND tenant = ?",
                (business_id, tenant),
            ).fetchall()
        elif "session_id" in cols:
            select_cols = ", ".join(f"t.{c}" for c in cols)
            rows = self._conn.execute(
                f"SELECT {select_cols} FROM {kind} t "
                f"JOIN sessions s ON s.id = t.session_id "
                f"WHERE s.business_id = ? AND t.tenant = ?",
                (business_id, tenant),
            ).fetchall()
        else:
            raise ValueError(f"{kind} is not scoped to a business")
        return [self._deserialize(kind, r) for r in rows]

    # -- tenant resolution: the one piece of business logic the repo must own (§9) --
    def _resolve_tenant(self, kind: str, record: dict) -> str:
        if kind == "businesses":
            return record["tenant"]
        if kind == "sessions":
            biz = self._conn.execute(
                "SELECT tenant FROM businesses WHERE id = ?", (record["business_id"],)
            ).fetchone()
            if not biz:
                raise TenantMismatchError(f"unknown business_id {record['business_id']}")
            return biz["tenant"]
        if kind in ("tasks", "workflow_graphs"):
            sess = self._conn.execute(
                "SELECT tenant FROM sessions WHERE id = ?", (record["session_id"],)
            ).fetchone()
            if not sess:
                raise TenantMismatchError(f"unknown session_id {record['session_id']}")
            return sess["tenant"]
        if kind == "opportunities":
            task_ids = record["task_ids"]
            if not task_ids:
                raise TenantMismatchError("Opportunity.task_ids is empty; cannot resolve tenant")
            tenants = set()
            for tid in task_ids:
                row = self._conn.execute("SELECT tenant FROM tasks WHERE id = ?", (tid,)).fetchone()
                if not row:
                    raise TenantMismatchError(f"unknown task_id {tid}")
                tenants.add(row["tenant"])
            if len(tenants) != 1:
                raise TenantMismatchError(f"Opportunity.task_ids span multiple tenants: {tenants}")
            return tenants.pop()
        if kind == "recommendations":
            opp = self._conn.execute(
                "SELECT tenant FROM opportunities WHERE id = ?", (record["opportunity_id"],)
            ).fetchone()
            if not opp:
                raise TenantMismatchError(f"unknown opportunity_id {record['opportunity_id']}")
            return opp["tenant"]
        if kind == "automations":
            rec = self._conn.execute(
                "SELECT tenant FROM recommendations WHERE id = ?", (record["recommendation_id"],)
            ).fetchone()
            if not rec:
                raise TenantMismatchError(f"unknown recommendation_id {record['recommendation_id']}")
            return rec["tenant"]
        raise ValueError(f"unknown kind {kind!r}")

    def _serialize(self, kind: str, record: dict, tenant: str) -> dict:
        row = dict(record)
        row["tenant"] = tenant
        for col in JSON_COLUMNS.get(kind, []):
            row[col] = json.dumps(row[col])
        return row

    def _deserialize(self, kind: str, row: sqlite3.Row) -> dict:
        d = dict(row)
        if kind not in _CONTRACT_HAS_TENANT:
            d.pop("tenant", None)
        for col in JSON_COLUMNS.get(kind, []):
            d[col] = json.loads(d[col])
        return d
