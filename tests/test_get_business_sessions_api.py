"""Tests for GET /businesses/{business_id}/sessions (item 6): auth, identical
404 on unknown-id/wrong-tenant, and the SessionOut shape (status, started_at
via get_first_turn_ts, recommendation_ids via list_recommendations_by_session).
Same TestClient/env-var conventions as tests/test_get_businesses_api.py; the
full-chain seeding mirrors tests/test_list_recommendations_by_session_repo.py's
_build_full_chain."""
from __future__ import annotations

import os
import uuid

from fastapi.testclient import TestClient

from auth.repository import AuthRepository
from kb.repository import KBRepository
from pipeline import _migrate


def _client():
    from api.main import app

    return TestClient(app)


def _set_env(monkeypatch, tmp_path, rate_limit=None):
    monkeypatch.setenv("PROCESSFORGE_DB_PATH", str(tmp_path / "test.db"))
    if rate_limit is not None:
        monkeypatch.setenv("PROCESSFORGE_RATE_LIMIT_PER_MINUTE", str(rate_limit))


def _seed_operator(db_path, username="alice", password="correct-horse-battery"):
    _migrate(db_path)
    repo = AuthRepository(db_path)
    try:
        repo.create_operator(username, password)
    finally:
        repo.close()


def _login_token(client, db_path, username="alice", password="correct-horse-battery"):
    _seed_operator(db_path, username=username, password=password)
    response = client.post(
        "/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return response.json()["token"]


def _new_id() -> str:
    return str(uuid.uuid4())


def _seed_bare_business(db_path, tenant="acme", name="Bare Co"):
    """Migrate the schema and create a business with no children directly via
    KBRepository, mirroring tests/test_get_businesses_api.py's own seeding
    style. Used for the zero-session edge case."""
    _migrate(db_path)
    repo = KBRepository(db_path)
    try:
        business_id = _new_id()
        repo.put("businesses", {
            "id": business_id, "schema_version": 1, "tenant": tenant,
            "name": name, "meta": {},
        })
        return business_id
    finally:
        repo.close()


def _build_full_chain(db_path, tenant="acme", business_name="Acme Co"):
    """Creates a business with one completed session (with a turn, so
    started_at is non-null, and a full task/opportunity/recommendation chain)
    plus one active session with no children at all — mirrors
    tests/test_list_recommendations_by_session_repo.py's _build_full_chain."""
    _migrate(db_path)
    repo = KBRepository(db_path)
    try:
        business_id = _new_id()
        repo.put("businesses", {
            "id": business_id, "schema_version": 1, "tenant": tenant,
            "name": business_name, "meta": {},
        })

        complete_session_id = _new_id()
        repo.put("sessions", {
            "id": complete_session_id, "schema_version": 1, "business_id": business_id,
            "status": "complete", "transcript_ref": None,
        })
        repo.add_turn(complete_session_id, "question", "What task takes the longest?")
        repo.add_turn(complete_session_id, "answer", "Reconciling invoices.")

        task_id = _new_id()
        repo.put("tasks", {
            "id": task_id, "schema_version": 1, "session_id": complete_session_id,
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

        active_session_id = _new_id()
        repo.put("sessions", {
            "id": active_session_id, "schema_version": 1, "business_id": business_id,
            "status": "active", "transcript_ref": None,
        })

        return {
            "business_id": business_id,
            "complete_session_id": complete_session_id,
            "active_session_id": active_session_id,
            "recommendation_id": recommendation_id,
        }
    finally:
        repo.close()


def _get_sessions(client, token, business_id, tenant):
    return client.get(
        f"/businesses/{business_id}/sessions",
        params={"tenant": tenant},
        headers={"Authorization": f"Bearer {token}"},
    )


def test_get_business_sessions_missing_token_rejected(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path)
    client = _client()

    response = client.get(
        f"/businesses/{_new_id()}/sessions", params={"tenant": "acme"}
    )

    assert response.status_code == 401


def test_get_business_sessions_garbage_token_rejected(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path)
    client = _client()

    response = client.get(
        f"/businesses/{_new_id()}/sessions",
        params={"tenant": "acme"},
        headers={"Authorization": "Bearer garbage-token"},
    )

    assert response.status_code == 401


def test_get_business_sessions_unknown_id_and_wrong_tenant_identical_404(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    real_business_id = _seed_bare_business(db_path, tenant="acme")

    unknown_response = _get_sessions(client, token, _new_id(), tenant="acme")
    wrong_tenant_response = _get_sessions(client, token, real_business_id, tenant="other-tenant")

    assert unknown_response.status_code == 404
    assert wrong_tenant_response.status_code == 404
    assert unknown_response.json() == wrong_tenant_response.json()
    assert unknown_response.json() == {"detail": "not found"}


def test_get_business_sessions_zero_sessions_returns_empty_list(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    business_id = _seed_bare_business(db_path, tenant="acme")

    response = _get_sessions(client, token, business_id, tenant="acme")

    assert response.status_code == 200
    assert response.json() == []


def test_get_business_sessions_returns_complete_and_active_sessions(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    ids = _build_full_chain(db_path, tenant="acme")

    response = _get_sessions(client, token, ids["business_id"], tenant="acme")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2

    by_id = {row["id"]: row for row in body}
    complete_row = by_id[ids["complete_session_id"]]
    active_row = by_id[ids["active_session_id"]]

    assert complete_row["status"] == "complete"
    assert complete_row["started_at"] is not None
    assert complete_row["recommendation_ids"] == [ids["recommendation_id"]]

    assert active_row["status"] == "active"
    assert active_row["started_at"] is None
    assert active_row["recommendation_ids"] == []
