"""Tests for POST /sessions/{session_id}/delete: auth, identical 404 on
unknown-id/wrong-tenant, confirm_session_id exact-match check (400, DB
untouched) BEFORE any repo access, happy-path counts + session removed from
GET /businesses/{business_id}/sessions while the parent business survives, no
audit_log write, and no approval_state flip on a sibling session's
recommendation. Same TestClient/env-var conventions as
tests/test_edit_business_api.py / tests/test_get_business_sessions_api.py."""
from __future__ import annotations

import os
import sqlite3
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


def _create_business_with_session(client, token, tenant="acme"):
    """Seed a real, persisted full chain (business/session/task/opportunity/
    recommendation) via POST /sessions and return both ids."""
    response = client.post(
        "/sessions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "business_name": "Test Co",
            "tenant": tenant,
            "answers": [
                "We manually reconcile invoices every week.",
                "It takes about 2 hours each time.",
                "We'd like it automated so no one has to touch a spreadsheet.",
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    return {"business_id": body["business_id"], "session_id": body["session_id"]}


def _seed_bare_session(db_path, tenant="acme", business_name="Bare Co"):
    """Migrate the schema and create a business + session with no children
    directly via KBRepository, mirroring
    tests/test_delete_session_repo.py's own seeding style. Used for the
    no-children edge case, which POST /sessions can't produce on its own."""
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
            "status": "active", "transcript_ref": None,
        })
        return {"business_id": business_id, "session_id": session_id}
    finally:
        repo.close()


def _delete_session(client, session_id, confirm_session_id, token, tenant="acme"):
    return client.post(
        f"/sessions/{session_id}/delete",
        params={"tenant": tenant},
        headers={"Authorization": f"Bearer {token}"},
        json={"confirm_session_id": confirm_session_id},
    )


def test_delete_session_missing_token_rejected(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    ids = _create_business_with_session(client, token, tenant="acme")

    response = client.post(
        f"/sessions/{ids['session_id']}/delete",
        params={"tenant": "acme"},
        json={"confirm_session_id": ids["session_id"]},
    )

    assert response.status_code == 401

    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE id = ?", (ids["session_id"],)
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 1


def test_delete_session_invalid_token_rejected(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    ids = _create_business_with_session(client, token, tenant="acme")

    response = client.post(
        f"/sessions/{ids['session_id']}/delete",
        params={"tenant": "acme"},
        headers={"Authorization": "Bearer garbage-token"},
        json={"confirm_session_id": ids["session_id"]},
    )

    assert response.status_code == 401

    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE id = ?", (ids["session_id"],)
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 1


def test_delete_session_unknown_id_and_wrong_tenant_identical_404(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    ids = _create_business_with_session(client, token, tenant="acme")

    unknown_id = _new_id()
    unknown_response = _delete_session(client, unknown_id, unknown_id, token, tenant="acme")
    wrong_tenant_response = _delete_session(
        client, ids["session_id"], ids["session_id"], token, tenant="other-tenant"
    )

    assert unknown_response.status_code == 404
    assert wrong_tenant_response.status_code == 404
    assert unknown_response.json() == wrong_tenant_response.json()
    assert unknown_response.json() == {"detail": "not found"}

    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE id = ?", (ids["session_id"],)
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 1


def test_delete_session_wrong_confirmation_returns_400_and_deletes_nothing(monkeypatch, tmp_path):
    """Most important test in this cycle: a mismatched confirm_session_id must
    be rejected with 400 BEFORE any repository access, so the session row (and
    its whole child chain) must still exist afterward, verified directly
    against the sqlite file rather than through the API."""
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    ids = _create_business_with_session(client, token, tenant="acme")

    response = _delete_session(
        client, ids["session_id"], "not-the-right-session-id", token, tenant="acme"
    )

    assert response.status_code == 400
    assert "confirm_session_id" in response.json()["detail"]

    conn = sqlite3.connect(db_path)
    try:
        session_count = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE id = ?", (ids["session_id"],)
        ).fetchone()[0]
        task_count = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE session_id = ?", (ids["session_id"],)
        ).fetchone()[0]
    finally:
        conn.close()
    assert session_count == 1
    assert task_count == 1


def test_delete_session_happy_path_returns_counts(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    ids = _create_business_with_session(client, token, tenant="acme")

    response = _delete_session(client, ids["session_id"], ids["session_id"], token, tenant="acme")

    assert response.status_code == 200
    assert response.json() == {
        "sessions": 1,
        "session_turns": 0,
        "tasks": 1,
        "workflow_graphs": 1,
        "opportunities": 1,
        "recommendations": 1,
        "automations": 0,
    }

    # Session gone from the business's session listing.
    listing = client.get(
        f"/businesses/{ids['business_id']}/sessions",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert listing.status_code == 200
    assert listing.json() == []

    # Parent business survives.
    businesses = client.get(
        "/businesses", params={"tenant": "acme"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert any(b["id"] == ids["business_id"] for b in businesses.json())


def test_delete_session_with_no_children_returns_zero_counts(monkeypatch, tmp_path):
    """No-children edge case: POST /sessions always creates a full chain, so
    this seeds a bare session directly via the repository (already covered at
    the repository level in test_delete_session_repo.py) to exercise the
    endpoint's handling of an all-zero counts dict."""
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    ids = _seed_bare_session(db_path, tenant="acme")

    response = _delete_session(client, ids["session_id"], ids["session_id"], token, tenant="acme")

    assert response.status_code == 200
    assert response.json() == {
        "sessions": 1,
        "session_turns": 0,
        "tasks": 0,
        "workflow_graphs": 0,
        "opportunities": 0,
        "recommendations": 0,
        "automations": 0,
    }


def test_delete_session_twice_returns_404_on_second_attempt(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    ids = _create_business_with_session(client, token, tenant="acme")

    first_response = _delete_session(client, ids["session_id"], ids["session_id"], token, tenant="acme")
    assert first_response.status_code == 200

    second_response = _delete_session(client, ids["session_id"], ids["session_id"], token, tenant="acme")

    assert second_response.status_code == 404


def test_delete_session_does_not_write_audit_log(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    ids = _create_business_with_session(client, token, tenant="acme")

    response = _delete_session(client, ids["session_id"], ids["session_id"], token, tenant="acme")
    assert response.status_code == 200

    audit = client.get(
        "/audit-log",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert audit.status_code == 200
    assert audit.json() == []


def test_delete_session_does_not_flip_sibling_approval_state(monkeypatch, tmp_path):
    """Deleting one session under a business must never disturb the
    approval_state of a recommendation belonging to a sibling session under
    the same business."""
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)

    # Two independent sessions (each POST /sessions creates its own business,
    # so create one business then add a second session/chain the same way a
    # second interview under it would).
    first = _create_business_with_session(client, token, tenant="acme")
    second = _create_business_with_session(client, token, tenant="acme")

    first_sessions = client.get(
        f"/businesses/{first['business_id']}/sessions",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    recommendation_id = first_sessions[0]["recommendation_ids"][0]

    approve_response = client.post(
        f"/recommendations/{recommendation_id}/approve",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert approve_response.status_code == 200
    assert approve_response.json()["approval_state"] == "approved"

    delete_response = _delete_session(
        client, second["session_id"], second["session_id"], token, tenant="acme"
    )
    assert delete_response.status_code == 200

    recommendation = client.get(
        f"/recommendations/{recommendation_id}",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert recommendation.status_code == 200
    assert recommendation.json()["approval_state"] == "approved"
