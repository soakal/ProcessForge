"""Tests for KBRepository.list_recommendations_by_session and
KBRepository.get_first_turn_ts (item 5): the two data primitives item 6's
GET /businesses/{id}/sessions needs (recommendation_ids, started_at). Exercised
via direct repository calls only (no API this cycle), built against the real
production migration path (pipeline._migrate), same convention as
tests/test_delete_business_repo.py."""
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

    return {
        "business_id": business_id,
        "session_id": session_id,
        "task_id": task_id,
        "opportunity_id": opportunity_id,
        "recommendation_id": recommendation_id,
    }


def test_list_recommendations_by_session_full_chain_returns_recommendation(repo):
    tenant = "acme"
    ids = _build_full_chain(repo, tenant)

    recs = repo.list_recommendations_by_session(ids["session_id"], tenant)

    assert [r["id"] for r in recs] == [ids["recommendation_id"]]
    assert recs[0]["opportunity_id"] == ids["opportunity_id"]
    assert recs[0]["summary"] == "Automate reconciliation"


def test_list_recommendations_by_session_no_tasks_returns_empty(repo):
    tenant = "acme"
    business_id = _new_id()
    repo.put("businesses", {
        "id": business_id, "schema_version": 1, "tenant": tenant,
        "name": "Bare Co", "meta": {},
    })
    session_id = _new_id()
    repo.put("sessions", {
        "id": session_id, "schema_version": 1, "business_id": business_id,
        "status": "active", "transcript_ref": None,
    })

    assert repo.list_recommendations_by_session(session_id, tenant) == []


def test_list_recommendations_by_session_cross_tenant_returns_empty(repo):
    ids = _build_full_chain(repo, "acme")

    assert repo.list_recommendations_by_session(ids["session_id"], "other-tenant") == []


def test_get_first_turn_ts_returns_openers_ts(repo):
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

    turns = repo.list_turns(session_id)
    opener_ts = turns[0]["ts"]

    assert repo.get_first_turn_ts(session_id) == opener_ts


def test_get_first_turn_ts_returns_none_for_turnless_session(repo):
    tenant = "acme"
    business_id = _new_id()
    repo.put("businesses", {
        "id": business_id, "schema_version": 1, "tenant": tenant,
        "name": "Quiet Co", "meta": {},
    })
    session_id = _new_id()
    repo.put("sessions", {
        "id": session_id, "schema_version": 1, "business_id": business_id,
        "status": "active", "transcript_ref": None,
    })

    assert repo.get_first_turn_ts(session_id) is None
