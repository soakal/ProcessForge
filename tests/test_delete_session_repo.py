"""Tests for KBRepository.delete_session: atomic cascade delete of a single
session and its full child chain, exercised via direct repository calls only
(no API this cycle — that's item 9), mirroring
tests/test_delete_business_repo.py's structure/fixture conventions, built
against the real production migration path (pipeline._migrate)."""
from __future__ import annotations

import uuid

import pytest

import pipeline
from kb.repository import KBRepository


@pytest.fixture
def repo(tmp_path):
    db_path = str(tmp_path / "pf.db")
    pipeline._migrate(db_path)
    r = KBRepository(db_path)
    yield r
    r.close()


def _new_id() -> str:
    return str(uuid.uuid4())


def _build_business(repo: KBRepository, tenant: str, name: str = "Acme Co") -> str:
    business_id = _new_id()
    repo.put("businesses", {
        "id": business_id, "schema_version": 1, "tenant": tenant,
        "name": name, "meta": {},
    })
    return business_id


def _build_session_chain(repo: KBRepository, business_id: str, tenant: str) -> dict:
    """Creates a session with one of every child record under an existing
    business, wired together exactly like pipeline.run_session does."""
    session_id = _new_id()
    repo.put("sessions", {
        "id": session_id, "schema_version": 1, "business_id": business_id,
        "status": "active", "transcript_ref": None,
    })

    task_id = _new_id()
    repo.put("tasks", {
        "id": task_id, "schema_version": 1, "session_id": session_id,
        "task": "Reconcile invoices", "frequency": "weekly", "frequency_per_week": 1.0,
        "time_spent_min": 120, "pain_level": 3, "tools_used": [], "dependencies": [],
        "desired_outcome": "automated",
    })

    workflow_graph_id = _new_id()
    repo.put("workflow_graphs", {
        "id": workflow_graph_id, "schema_version": 1, "session_id": session_id,
        "nodes": [{"id": "n1", "task_id": task_id, "label": "step"}],
        "edges": [], "bottlenecks": [],
    })

    opportunity_id = _new_id()
    repo.put("opportunities", {
        "id": opportunity_id, "schema_version": 1, "task_ids": [task_id],
        "roi_low_hrs": 1.0, "roi_high_hrs": 2.0, "assumptions": ["a1"],
        "complexity": 2, "confidence": 0.5, "crosscheck_flags": [],
    })

    recommendation_id = _new_id()
    repo.put("recommendations", {
        "id": recommendation_id, "schema_version": 1, "opportunity_id": opportunity_id,
        "summary": "Automate reconciliation", "approval_state": "draft",
    })

    automation_id = _new_id()
    repo.put("automations", {
        "id": automation_id, "schema_version": 1, "recommendation_id": recommendation_id,
        "spec": {"kind": "noop"}, "blast_radius": "none", "rollback": "n/a",
        "approval_state": "draft",
    })

    return {
        "session_id": session_id,
        "task_id": task_id,
        "workflow_graph_id": workflow_graph_id,
        "opportunity_id": opportunity_id,
        "recommendation_id": recommendation_id,
        "automation_id": automation_id,
    }


def _build_full_chain(repo: KBRepository, tenant: str, business_name: str = "Acme Co") -> dict:
    business_id = _build_business(repo, tenant, business_name)
    ids = _build_session_chain(repo, business_id, tenant)
    ids["business_id"] = business_id
    return ids


class _FailingConnProxy:
    """Wraps the repo's real sqlite3 connection so one specific DELETE
    statement raises partway through a transaction — used to prove
    delete_session rolls back everything on a mid-cascade failure.
    sqlite3.Connection instances refuse instance-level method monkeypatching
    (its bound methods are read-only slots), so the whole connection
    attribute is swapped for this proxy instead; every other call is
    forwarded unchanged to the real connection, including commit/rollback."""

    def __init__(self, real_conn, fail_on_prefix: str):
        self._real = real_conn
        self._fail_on_prefix = fail_on_prefix

    def execute(self, sql, params=()):
        if sql.strip().startswith(self._fail_on_prefix):
            raise RuntimeError("forced failure for test")
        return self._real.execute(sql, params)

    def commit(self):
        return self._real.commit()

    def rollback(self):
        return self._real.rollback()

    def close(self):
        return self._real.close()


def test_delete_session_happy_path_returns_counts_and_business_survives(repo):
    tenant = "acme"
    ids = _build_full_chain(repo, tenant)

    counts = repo.delete_session(ids["session_id"], tenant)

    assert counts == {
        "sessions": 1,
        "session_turns": 0,
        "tasks": 1,
        "workflow_graphs": 1,
        "opportunities": 1,
        "recommendations": 1,
        "automations": 1,
    }

    assert repo.get("sessions", ids["session_id"], tenant) is None
    assert repo.get("tasks", ids["task_id"], tenant) is None
    assert repo.get("workflow_graphs", ids["workflow_graph_id"], tenant) is None
    assert repo.get("opportunities", ids["opportunity_id"], tenant) is None
    assert repo.get("recommendations", ids["recommendation_id"], tenant) is None
    assert repo.get("automations", ids["automation_id"], tenant) is None

    # the parent business survives, completely untouched.
    assert repo.get("businesses", ids["business_id"], tenant) is not None


def test_delete_session_with_session_turns_deletes_turns_too(repo):
    tenant = "acme"
    business_id = _build_business(repo, tenant, "Chatty Co")
    ids = _build_session_chain(repo, business_id, tenant)
    repo.add_turn(ids["session_id"], "user", "First message")
    repo.add_turn(ids["session_id"], "assistant", "Second message")

    counts = repo.delete_session(ids["session_id"], tenant)

    assert counts["session_turns"] == 2
    assert repo.list_turns(ids["session_id"]) == []
    assert repo.get("businesses", business_id, tenant) is not None


def test_delete_session_with_no_children_deletes_cleanly(repo):
    tenant = "acme"
    business_id = _build_business(repo, tenant, "Bare Co")
    session_id = _new_id()
    repo.put("sessions", {
        "id": session_id, "schema_version": 1, "business_id": business_id,
        "status": "active", "transcript_ref": None,
    })

    counts = repo.delete_session(session_id, tenant)

    assert counts == {
        "sessions": 1,
        "session_turns": 0,
        "tasks": 0,
        "workflow_graphs": 0,
        "opportunities": 0,
        "recommendations": 0,
        "automations": 0,
    }
    assert repo.get("sessions", session_id, tenant) is None
    assert repo.get("businesses", business_id, tenant) is not None


def test_delete_session_nonexistent_returns_none(repo):
    assert repo.delete_session("does-not-exist", "acme") is None


def test_delete_session_wrong_tenant_returns_none(repo):
    ids = _build_full_chain(repo, "acme")

    assert repo.delete_session(ids["session_id"], "wrong-tenant") is None

    # nothing was touched — the session is still there for the real tenant.
    assert repo.get("sessions", ids["session_id"], "acme") is not None


def test_delete_session_twice_second_call_returns_none(repo):
    ids = _build_full_chain(repo, "acme")

    first = repo.delete_session(ids["session_id"], "acme")
    second = repo.delete_session(ids["session_id"], "acme")

    assert first is not None
    assert second is None


def test_delete_session_sibling_session_untouched(repo):
    tenant = "acme"
    business_id = _build_business(repo, tenant, "Acme Co")
    ids_a = _build_session_chain(repo, business_id, tenant)
    ids_b = _build_session_chain(repo, business_id, tenant)

    repo.delete_session(ids_a["session_id"], tenant)

    # session A and everything under it is gone.
    assert repo.get("sessions", ids_a["session_id"], tenant) is None
    assert repo.get("tasks", ids_a["task_id"], tenant) is None
    assert repo.get("workflow_graphs", ids_a["workflow_graph_id"], tenant) is None
    assert repo.get("opportunities", ids_a["opportunity_id"], tenant) is None
    assert repo.get("recommendations", ids_a["recommendation_id"], tenant) is None
    assert repo.get("automations", ids_a["automation_id"], tenant) is None

    # session B and everything under it, plus the shared business, is untouched.
    assert repo.get("businesses", business_id, tenant) is not None
    assert repo.get("sessions", ids_b["session_id"], tenant) is not None
    assert repo.get("tasks", ids_b["task_id"], tenant) is not None
    assert repo.get("workflow_graphs", ids_b["workflow_graph_id"], tenant) is not None
    assert repo.get("opportunities", ids_b["opportunity_id"], tenant) is not None
    assert repo.get("recommendations", ids_b["recommendation_id"], tenant) is not None
    assert repo.get("automations", ids_b["automation_id"], tenant) is not None


def test_delete_session_forced_mid_delete_failure_rolls_back_everything(repo, monkeypatch):
    tenant = "acme"
    ids = _build_full_chain(repo, tenant)
    repo.add_turn(ids["session_id"], "user", "hello")

    # automations/recommendations/opportunities/workflow_graphs are deleted
    # ahead of tasks in delete_session's cascade order, so failing here proves
    # several prior deletes in the same transaction get rolled back too, not
    # just the one that raised.
    proxy = _FailingConnProxy(repo._conn, "DELETE FROM tasks")
    monkeypatch.setattr(repo, "_conn", proxy)

    with pytest.raises(RuntimeError):
        repo.delete_session(ids["session_id"], tenant)

    assert repo.get("sessions", ids["session_id"], tenant) is not None
    assert repo.get("tasks", ids["task_id"], tenant) is not None
    assert repo.get("workflow_graphs", ids["workflow_graph_id"], tenant) is not None
    assert repo.get("opportunities", ids["opportunity_id"], tenant) is not None
    assert repo.get("recommendations", ids["recommendation_id"], tenant) is not None
    assert repo.get("automations", ids["automation_id"], tenant) is not None
    assert repo.list_turns(ids["session_id"]) != []
    assert repo.get("businesses", ids["business_id"], tenant) is not None
