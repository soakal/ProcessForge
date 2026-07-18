"""Tests for POST /businesses/{business_id}/edit (item 7): auth, identical
404 on unknown-id/wrong-tenant, rename persistence + audit-log visibility,
whitespace-only/>500-char name -> 422, same-name no-op -> 200 with no new
audit row, and that a rename never disturbs any approval_state. Same
TestClient/env-var conventions as tests/test_get_business_sessions_api.py."""
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
    KBRepository, mirroring tests/test_get_business_sessions_api.py's own
    seeding style."""
    _migrate(db_path)
    repo = KBRepository(db_path)
    try:
        business_id = _new_id()
        repo.put("businesses", {
            "id": business_id, "schema_version": 1, "tenant": tenant,
            "name": name, "meta": {"secret": "must-never-be-touched"},
        })
        return business_id
    finally:
        repo.close()


def _build_chain_with_recommendation(db_path, tenant="acme", business_name="Acme Co"):
    """Business -> session -> task -> opportunity -> recommendation, mirroring
    tests/test_get_business_sessions_api.py's _build_full_chain, trimmed to
    what the approval_state-untouched test needs."""
    _migrate(db_path)
    repo = KBRepository(db_path)
    try:
        business_id = _new_id()
        repo.put("businesses", {
            "id": business_id, "schema_version": 1, "tenant": tenant,
            "name": business_name, "meta": {},
        })

        session_id = _new_id()
        repo.put("sessions", {
            "id": session_id, "schema_version": 1, "business_id": business_id,
            "status": "complete", "transcript_ref": None,
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

        return {"business_id": business_id, "recommendation_id": recommendation_id}
    finally:
        repo.close()


def _edit_business(client, token, business_id, tenant, name):
    return client.post(
        f"/businesses/{business_id}/edit",
        params={"tenant": tenant},
        json={"name": name},
        headers={"Authorization": f"Bearer {token}"},
    )


def test_edit_business_missing_token_rejected(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path)
    client = _client()

    response = client.post(
        f"/businesses/{_new_id()}/edit",
        params={"tenant": "acme"},
        json={"name": "New Name"},
    )

    assert response.status_code == 401


def test_edit_business_garbage_token_rejected(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path)
    client = _client()

    response = client.post(
        f"/businesses/{_new_id()}/edit",
        params={"tenant": "acme"},
        json={"name": "New Name"},
        headers={"Authorization": "Bearer garbage-token"},
    )

    assert response.status_code == 401


def test_edit_business_unknown_id_and_wrong_tenant_identical_404(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    real_business_id = _seed_bare_business(db_path, tenant="acme")

    unknown_response = _edit_business(client, token, _new_id(), "acme", "New Name")
    wrong_tenant_response = _edit_business(client, token, real_business_id, "other-tenant", "New Name")

    assert unknown_response.status_code == 404
    assert wrong_tenant_response.status_code == 404
    assert unknown_response.json() == wrong_tenant_response.json()
    assert unknown_response.json() == {"detail": "not found"}


def test_edit_business_rename_persists_and_audit_logged(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    business_id = _seed_bare_business(db_path, tenant="acme", name="Old Name")

    response = _edit_business(client, token, business_id, "acme", "New Name")

    assert response.status_code == 200
    assert response.json()["name"] == "New Name"

    # Rename persisted.
    listing = client.get(
        "/businesses", params={"tenant": "acme"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert listing.json() == [{"id": business_id, "name": "New Name", "session_count": 0}]

    # Audit entry visible via GET /audit-log.
    audit = client.get(
        "/audit-log",
        params={"tenant": "acme", "record_id": business_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert audit.status_code == 200
    rows = audit.json()
    assert len(rows) == 1
    assert rows[0]["record_kind"] == "business"
    assert rows[0]["record_id"] == business_id
    assert rows[0]["field"] == "name"
    assert rows[0]["old_value"] == "Old Name"
    assert rows[0]["new_value"] == "New Name"


def test_edit_business_whitespace_only_name_rejected(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    business_id = _seed_bare_business(db_path, tenant="acme", name="Old Name")

    response = _edit_business(client, token, business_id, "acme", "   ")

    assert response.status_code == 422

    listing = client.get(
        "/businesses", params={"tenant": "acme"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert listing.json()[0]["name"] == "Old Name"


def test_edit_business_name_too_long_rejected(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    business_id = _seed_bare_business(db_path, tenant="acme", name="Old Name")

    response = _edit_business(client, token, business_id, "acme", "x" * 501)

    assert response.status_code == 422


def test_edit_business_same_name_no_audit_row(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    business_id = _seed_bare_business(db_path, tenant="acme", name="Same Name")

    # Also exercise strip-then-compare: leading/trailing whitespace around an
    # otherwise-identical name is still a no-op.
    response = _edit_business(client, token, business_id, "acme", "  Same Name  ")

    assert response.status_code == 200
    assert response.json()["name"] == "Same Name"

    audit = client.get(
        "/audit-log",
        params={"tenant": "acme", "record_id": business_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert audit.json() == []


def test_edit_business_rename_does_not_touch_approval_state(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    ids = _build_chain_with_recommendation(db_path, tenant="acme")

    approve_response = client.post(
        f"/recommendations/{ids['recommendation_id']}/approve",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert approve_response.status_code == 200
    assert approve_response.json()["approval_state"] == "approved"

    rename_response = _edit_business(client, token, ids["business_id"], "acme", "Renamed Co")
    assert rename_response.status_code == 200

    recommendation = client.get(
        f"/recommendations/{ids['recommendation_id']}",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert recommendation.status_code == 200
    assert recommendation.json()["approval_state"] == "approved"
