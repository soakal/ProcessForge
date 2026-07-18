"""Tests for KBRepository.delete_business: atomic cascade delete of a business
and its full child chain, exercised via direct repository calls only (no API
this cycle), built against the real production migration path
(pipeline._migrate), same convention as tests/test_audit_log.py."""
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


def _build_full_chain(repo: KBRepository, tenant: str, business_name: str = "Acme Co") -> dict:
    """Creates a business with one of every child record, wired together
    exactly like pipeline.run_session does, and returns the ids used."""
    business_id = _new_id()
    repo.put("businesses", {
        "id": business_id, "schema_version": 1, "tenant": tenant,
        "name": business_name, "meta": {},
    })

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
        "business_id": business_id,
        "session_id": session_id,
        "task_id": task_id,
        "workflow_graph_id": workflow_graph_id,
        "opportunity_id": opportunity_id,
        "recommendation_id": recommendation_id,
        "automation_id": automation_id,
    }


def test_delete_business_happy_path_returns_counts_and_removes_everything(repo):
    tenant = "acme"
    ids = _build_full_chain(repo, tenant)

    counts = repo.delete_business(ids["business_id"], tenant)

    assert counts == {
        "businesses": 1,
        "sessions": 1,
        "session_turns": 0,
        "tasks": 1,
        "workflow_graphs": 1,
        "opportunities": 1,
        "recommendations": 1,
        "automations": 1,
    }

    assert repo.get("businesses", ids["business_id"], tenant) is None
    assert repo.get("sessions", ids["session_id"], tenant) is None
    assert repo.get("tasks", ids["task_id"], tenant) is None
    assert repo.get("workflow_graphs", ids["workflow_graph_id"], tenant) is None
    assert repo.get("opportunities", ids["opportunity_id"], tenant) is None
    assert repo.get("recommendations", ids["recommendation_id"], tenant) is None
    assert repo.get("automations", ids["automation_id"], tenant) is None


def test_delete_business_with_no_children_deletes_cleanly(repo):
    tenant = "acme"
    business_id = _new_id()
    repo.put("businesses", {
        "id": business_id, "schema_version": 1, "tenant": tenant,
        "name": "Bare Co", "meta": {},
    })

    counts = repo.delete_business(business_id, tenant)

    assert counts == {
        "businesses": 1,
        "sessions": 0,
        "session_turns": 0,
        "tasks": 0,
        "workflow_graphs": 0,
        "opportunities": 0,
        "recommendations": 0,
        "automations": 0,
    }
    assert repo.get("businesses", business_id, tenant) is None


def test_delete_business_cross_tenant_isolation(repo):
    ids_a = _build_full_chain(repo, "acme", business_name="Acme Co")
    ids_b = _build_full_chain(repo, "other", business_name="Other Co")

    repo.delete_business(ids_a["business_id"], "acme")

    # business A and everything under it is gone.
    assert repo.get("businesses", ids_a["business_id"], "acme") is None

    # business B and everything under it is completely untouched.
    assert repo.get("businesses", ids_b["business_id"], "other") is not None
    assert repo.get("sessions", ids_b["session_id"], "other") is not None
    assert repo.get("tasks", ids_b["task_id"], "other") is not None
    assert repo.get("workflow_graphs", ids_b["workflow_graph_id"], "other") is not None
    assert repo.get("opportunities", ids_b["opportunity_id"], "other") is not None
    assert repo.get("recommendations", ids_b["recommendation_id"], "other") is not None
    assert repo.get("automations", ids_b["automation_id"], "other") is not None


def test_delete_business_nonexistent_returns_none(repo):
    assert repo.delete_business("does-not-exist", "acme") is None


def test_delete_business_wrong_tenant_returns_none(repo):
    ids = _build_full_chain(repo, "acme")

    assert repo.delete_business(ids["business_id"], "wrong-tenant") is None

    # nothing was touched — the business is still there for the real tenant.
    assert repo.get("businesses", ids["business_id"], "acme") is not None


def test_delete_business_with_session_turns_deletes_turns_too(repo):
    tenant = "acme"
    business_id = _new_id()
    repo.put("businesses", {
        "id": business_id, "schema_version": 1, "tenant": tenant,
        "name": "Chatty Co", "meta": {},
    })

    session_id = _new_id()
    repo.put("sessions", {
        "id": session_id, "schema_version": 1, "business_id": business_id,
        "status": "active", "transcript_ref": None,
    })

    repo.add_turn(session_id, "user", "First message")
    repo.add_turn(session_id, "assistant", "Second message")

    counts = repo.delete_business(business_id, tenant)

    assert counts == {
        "businesses": 1,
        "sessions": 1,
        "session_turns": 2,
        "tasks": 0,
        "workflow_graphs": 0,
        "opportunities": 0,
        "recommendations": 0,
        "automations": 0,
    }

    assert repo.get("businesses", business_id, tenant) is None
    assert repo.list_turns(session_id) == []


def test_delete_business_twice_second_call_returns_none(repo):
    ids = _build_full_chain(repo, "acme")

    first = repo.delete_business(ids["business_id"], "acme")
    second = repo.delete_business(ids["business_id"], "acme")

    assert first is not None
    assert second is None
