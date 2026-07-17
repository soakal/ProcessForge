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
import uuid
from datetime import datetime, timezone

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

    def list_automations_by_recommendation(self, recommendation_id: str, tenant: str) -> list[dict]:
        """Tenant-scoped: every Automation ever built/refined from one
        Recommendation. Used by POST /recommendations/{id}/refine (api/main.py)
        to find the prior latest revision to increment from — callers should
        compare each row's spec['revision'] (see stages/qa.py's existing
        revision pattern), not row order, since insertion order isn't
        guaranteed to match revision order."""
        cols = COLUMNS["automations"]
        rows = self._conn.execute(
            f"SELECT {', '.join(cols)} FROM automations WHERE recommendation_id = ? AND tenant = ?",
            (recommendation_id, tenant),
        ).fetchall()
        return [self._deserialize("automations", r) for r in rows]

    # -- audit_log: append-only, no parent-chain tenant resolution needed (caller
    # already knows the tenant), so these bypass _resolve_tenant entirely. --
    def log_approval_change(
        self,
        *,
        operator_id: str,
        tenant: str,
        record_kind: str,
        record_id: str,
        field: str,
        old_value: str,
        new_value: str,
    ) -> None:
        self._conn.execute(
            "INSERT INTO audit_log "
            "(id, ts, operator_id, tenant, record_kind, record_id, field, old_value, new_value) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                datetime.now(timezone.utc).isoformat(),
                operator_id,
                tenant,
                record_kind,
                record_id,
                field,
                old_value,
                new_value,
            ),
        )
        self._conn.commit()

    def list_audit_log(self, tenant: str, record_id: str | None = None) -> list[dict]:
        if record_id is None:
            rows = self._conn.execute(
                "SELECT id, ts, operator_id, tenant, record_kind, record_id, field, old_value, new_value "
                "FROM audit_log WHERE tenant = ? ORDER BY ts",
                (tenant,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, ts, operator_id, tenant, record_kind, record_id, field, old_value, new_value "
                "FROM audit_log WHERE tenant = ? AND record_id = ? ORDER BY ts",
                (tenant, record_id),
            ).fetchall()
        return [dict(row) for row in rows]

    # -- session_turns: caller already knows the session, so these bypass
    # _resolve_tenant entirely, mirroring log_approval_change/list_audit_log. --
    def add_turn(self, session_id: str, role: str, content: str) -> None:
        turn_index = self._conn.execute(
            "SELECT COUNT(*) FROM session_turns WHERE session_id = ?", (session_id,)
        ).fetchone()[0]
        self._conn.execute(
            "INSERT INTO session_turns (id, session_id, turn_index, role, content, ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                session_id,
                turn_index,
                role,
                content,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()

    def list_turns(self, session_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, session_id, turn_index, role, content, ts "
            "FROM session_turns WHERE session_id = ? ORDER BY turn_index",
            (session_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    # -- delete_business: atomic cascade delete, bypasses _resolve_tenant like the
    # audit_log methods above (caller already knows the tenant). --
    def delete_business(self, business_id: str, tenant: str) -> dict | None:
        biz = self._conn.execute(
            "SELECT id FROM businesses WHERE id = ? AND tenant = ?", (business_id, tenant)
        ).fetchone()
        if not biz:
            return None

        def _ids(query: str, params: tuple) -> list[str]:
            return [row[0] for row in self._conn.execute(query, params).fetchall()]

        def _placeholders(ids: list[str]) -> str:
            return ", ".join("?" for _ in ids)

        # Gather the full child set first (read-only), before any deletes.
        session_ids = _ids(
            "SELECT id FROM sessions WHERE business_id = ? AND tenant = ?", (business_id, tenant)
        )

        task_ids: list[str] = []
        workflow_graph_ids: list[str] = []
        if session_ids:
            task_ids = _ids(
                f"SELECT id FROM tasks WHERE session_id IN ({_placeholders(session_ids)}) AND tenant = ?",
                (*session_ids, tenant),
            )
            workflow_graph_ids = _ids(
                f"SELECT id FROM workflow_graphs WHERE session_id IN ({_placeholders(session_ids)}) "
                f"AND tenant = ?",
                (*session_ids, tenant),
            )

        opportunity_ids: list[str] = []
        if task_ids:
            # opportunities reference tasks via a JSON array column (task_ids), not
            # a real FK, so json_each() is required to find the matching rows.
            # tenant is scoped too as defense in depth even though task ids are
            # already tenant-scoped via the session chain.
            opportunity_ids = _ids(
                f"SELECT DISTINCT o.id FROM opportunities o, json_each(o.task_ids) je "
                f"WHERE je.value IN ({_placeholders(task_ids)}) AND o.tenant = ?",
                (*task_ids, tenant),
            )

        recommendation_ids: list[str] = []
        if opportunity_ids:
            recommendation_ids = _ids(
                f"SELECT id FROM recommendations WHERE opportunity_id IN "
                f"({_placeholders(opportunity_ids)}) AND tenant = ?",
                (*opportunity_ids, tenant),
            )

        automation_ids: list[str] = []
        if recommendation_ids:
            automation_ids = _ids(
                f"SELECT id FROM automations WHERE recommendation_id IN "
                f"({_placeholders(recommendation_ids)}) AND tenant = ?",
                (*recommendation_ids, tenant),
            )

        try:
            # FK-safe order: children before parents. All within one transaction —
            # no commit() until every delete below has succeeded.
            if automation_ids:
                self._conn.execute(
                    f"DELETE FROM automations WHERE id IN ({_placeholders(automation_ids)})",
                    automation_ids,
                )
            if recommendation_ids:
                self._conn.execute(
                    f"DELETE FROM recommendations WHERE id IN ({_placeholders(recommendation_ids)})",
                    recommendation_ids,
                )
            if opportunity_ids:
                self._conn.execute(
                    f"DELETE FROM opportunities WHERE id IN ({_placeholders(opportunity_ids)})",
                    opportunity_ids,
                )
            if workflow_graph_ids:
                self._conn.execute(
                    f"DELETE FROM workflow_graphs WHERE id IN ({_placeholders(workflow_graph_ids)})",
                    workflow_graph_ids,
                )
            if task_ids:
                self._conn.execute(
                    f"DELETE FROM tasks WHERE id IN ({_placeholders(task_ids)})", task_ids
                )
            if session_ids:
                self._conn.execute(
                    f"DELETE FROM sessions WHERE id IN ({_placeholders(session_ids)})", session_ids
                )
            self._conn.execute(
                "DELETE FROM businesses WHERE id = ? AND tenant = ?", (business_id, tenant)
            )
            # audit_log is intentionally NOT purged here: it is append-only by
            # design (69ccb440bc94_audit_log.py installs BEFORE UPDATE/DELETE
            # triggers on audit_log that unconditionally RAISE(ABORT) — there is
            # no WHEN clause carving out an exception, and migrations are out of
            # scope for this change). Leaving those rows in place is a deliberate
            # choice, not an oversight: they reference a tenant/record_id that no
            # longer resolves to anything, which is harmless once the business is
            # gone, and an audit trail arguably should survive deletion of the
            # thing it audited (the same way closing a bank account doesn't erase
            # its transaction history).
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

        return {
            "businesses": 1,
            "sessions": len(session_ids),
            "tasks": len(task_ids),
            "workflow_graphs": len(workflow_graph_ids),
            "opportunities": len(opportunity_ids),
            "recommendations": len(recommendation_ids),
            "automations": len(automation_ids),
        }

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
