"""Tests for GET /businesses: the API twin of KBRepository.list_businesses
(tests/test_list_businesses_repo.py) — auth, tenant isolation, unknown-tenant
posture, response shape (never `meta`), and session_count via a real
interview. Same TestClient/env-var conventions as tests/test_api.py."""
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


def _seed_bare_business(db_path, tenant="acme", name="Bare Co"):
    """Migrate the schema and create a business with no children directly via
    KBRepository, mirroring tests/test_api.py's own seeding style."""
    _migrate(db_path)
    repo = KBRepository(db_path)
    try:
        business_id = str(uuid.uuid4())
        repo.put("businesses", {
            "id": business_id, "schema_version": 1, "tenant": tenant,
            "name": name, "meta": {"secret": "must-never-be-serialized"},
        })
        return business_id
    finally:
        repo.close()


def _get_businesses(client, token, tenant):
    return client.get(
        "/businesses",
        params={"tenant": tenant},
        headers={"Authorization": f"Bearer {token}"},
    )


def test_get_businesses_missing_token_rejected(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path)
    client = _client()

    response = client.get("/businesses", params={"tenant": "acme"})

    assert response.status_code == 401


def test_get_businesses_garbage_token_rejected(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path)
    client = _client()

    response = client.get(
        "/businesses",
        params={"tenant": "acme"},
        headers={"Authorization": "Bearer garbage-token"},
    )

    assert response.status_code == 401


def test_get_businesses_unknown_tenant_returns_empty_list(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    _seed_bare_business(db_path, tenant="acme")

    response = _get_businesses(client, token, tenant="does-not-exist")

    assert response.status_code == 200
    assert response.json() == []


def test_get_businesses_tenant_isolation_and_shape(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    acme_id = _seed_bare_business(db_path, tenant="acme", name="Acme Co")
    _seed_bare_business(db_path, tenant="other-tenant", name="Other Co")

    response = _get_businesses(client, token, tenant="acme")

    assert response.status_code == 200
    body = response.json()
    assert body == [{"id": acme_id, "name": "Acme Co", "session_count": 0}]
    # Never leak meta, even though the seeded row carries one.
    assert "meta" not in body[0]
    assert "tenant" not in body[0]


def test_get_businesses_session_count_reflects_real_interview_session(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)

    session_response = client.post(
        "/sessions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "business_name": "Test Co",
            "tenant": "acme",
            "answers": [
                "We manually reconcile invoices every week.",
                "It takes about 2 hours each time.",
                "We'd like it automated so no one has to touch a spreadsheet.",
            ],
        },
    )
    assert session_response.status_code == 200

    response = _get_businesses(client, token, tenant="acme")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "Test Co"
    assert body[0]["session_count"] == 1
