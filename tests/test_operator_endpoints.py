"""Tests for the four Item-17 operator-management endpoints:
GET /auth/operators, POST /auth/operators, POST /auth/operators/password,
POST /auth/operators/delete. Same TestClient/env-var conventions as
tests/test_edit_business_api.py; auth-only endpoints, so no tenant param
(auth/operator tables are exempt from tenant scoping, see docs/
FEATURE-SPEC-dashboard-and-users.md Part A)."""
from __future__ import annotations

import os

from fastapi.testclient import TestClient

from auth.repository import AuthRepository
from pipeline import _migrate

_SHORT_PASSWORD = "sh0rt12"  # 7 chars, below _MIN_PASSWORD_LENGTH (8)


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


def _login_existing(client, username, password):
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return response.json()["token"]


def _list_operators(client, token):
    return client.get("/auth/operators", headers={"Authorization": f"Bearer {token}"})


def test_all_four_endpoints_reject_missing_or_bad_token(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    client = _client()

    get_response = client.get("/auth/operators")
    create_response = client.post(
        "/auth/operators",
        json={"username": "bob", "password": "long-enough-pw"},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    password_response = client.post(
        "/auth/operators/password",
        json={"username": "bob", "new_password": "long-enough-pw"},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    delete_response = client.post(
        "/auth/operators/delete",
        json={"username": "bob"},
        headers={"Authorization": "Bearer not-a-real-token"},
    )

    assert get_response.status_code == 401
    assert create_response.status_code == 401
    assert password_response.status_code == 401
    assert delete_response.status_code == 401


def test_create_operator_appears_in_list(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)

    response = client.post(
        "/auth/operators",
        json={"username": "bob", "password": "long-enough-pw"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == {"username": "bob", "status": "created"}

    listing = _list_operators(client, token)
    assert listing.status_code == 200
    usernames = [row["username"] for row in listing.json()]
    assert "bob" in usernames
    assert "alice" in usernames


def test_create_operator_duplicate_returns_409_list_unchanged(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)

    first = client.post(
        "/auth/operators",
        json={"username": "bob", "password": "long-enough-pw"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert first.status_code == 200
    before = _list_operators(client, token).json()

    duplicate = client.post(
        "/auth/operators",
        json={"username": "bob", "password": "another-long-pw"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert duplicate.status_code == 409
    after = _list_operators(client, token).json()
    assert before == after


def test_create_operator_short_password_rejected_no_password_echo(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)

    response = client.post(
        "/auth/operators",
        json={"username": "bob", "password": _SHORT_PASSWORD},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400
    assert _SHORT_PASSWORD not in response.text

    listing = _list_operators(client, token)
    assert "bob" not in [row["username"] for row in listing.json()]


def test_get_operators_response_has_no_password_hash(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)

    listing = _list_operators(client, token)

    assert listing.status_code == 200
    for row in listing.json():
        assert "password_hash" not in row


def test_set_password_unknown_operator_404(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)

    response = client.post(
        "/auth/operators/password",
        json={"username": "does-not-exist", "new_password": "long-enough-pw"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404


def test_set_password_short_password_rejected_no_password_echo(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)

    response = client.post(
        "/auth/operators/password",
        json={"username": "alice", "new_password": _SHORT_PASSWORD},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400
    assert _SHORT_PASSWORD not in response.text


def test_set_password_revokes_targets_prior_tokens(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    admin_token = _login_token(client, db_path, username="admin", password="admin-pw-long-enough")
    create = client.post(
        "/auth/operators",
        json={"username": "bob", "password": "bobs-original-pw"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert create.status_code == 200
    bob_token = _login_existing(client, "bob", "bobs-original-pw")

    response = client.post(
        "/auth/operators/password",
        json={"username": "bob", "new_password": "bobs-new-password"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200

    stale_check = client.get("/auth/operators", headers={"Authorization": f"Bearer {bob_token}"})
    assert stale_check.status_code == 401

    fresh_token = _login_existing(client, "bob", "bobs-new-password")
    fresh_check = client.get("/auth/operators", headers={"Authorization": f"Bearer {fresh_token}"})
    assert fresh_check.status_code == 200


def test_self_password_change_own_token_401_after(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path, username="alice", password="alices-original-pw")

    change_response = client.post(
        "/auth/operators/password",
        json={"username": "alice", "new_password": "alices-new-password"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert change_response.status_code == 200

    next_request = client.get("/auth/operators", headers={"Authorization": f"Bearer {token}"})
    assert next_request.status_code == 401


def test_delete_operator_revokes_tokens_and_removes_from_list(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    admin_token = _login_token(client, db_path, username="admin", password="admin-pw-long-enough")
    create = client.post(
        "/auth/operators",
        json={"username": "bob", "password": "bobs-original-pw"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert create.status_code == 200
    bob_token = _login_existing(client, "bob", "bobs-original-pw")

    response = client.post(
        "/auth/operators/delete",
        json={"username": "bob"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200

    stale_check = client.get("/auth/operators", headers={"Authorization": f"Bearer {bob_token}"})
    assert stale_check.status_code == 401

    listing = _list_operators(client, admin_token)
    assert "bob" not in [row["username"] for row in listing.json()]


def test_delete_operator_unknown_404(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)

    response = client.post(
        "/auth/operators/delete",
        json={"username": "does-not-exist"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404


def test_create_operator_missing_username_does_not_echo_password(monkeypatch, tmp_path):
    """A malformed body (username omitted) fails Pydantic validation before
    the handler runs, so the in-handler length check never sees it. FastAPI's
    default 422 handler echoes the entire submitted body as `input` on the
    "missing" error — this asserts our RequestValidationError handler
    redacts that before it reaches the client."""
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    client = _client()

    response = client.post("/auth/operators", json={"password": "SUPER-SECRET-PW-VALUE"})

    assert response.status_code == 422
    assert "SUPER-SECRET-PW-VALUE" not in response.text


def test_create_operator_wrong_type_password_does_not_echo(monkeypatch, tmp_path):
    """password sent as the wrong JSON type: FastAPI's default 422 echoes
    the offending value verbatim as `input` on the field's own error."""
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    client = _client()

    response = client.post(
        "/auth/operators",
        json={"username": "bob2", "password": ["SECRET-MARKER-XYZ"]},
    )

    assert response.status_code == 422
    assert "SECRET-MARKER-XYZ" not in response.text


def test_set_password_missing_username_does_not_echo_new_password(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    client = _client()

    response = client.post(
        "/auth/operators/password",
        json={"new_password": "SUPER-SECRET-PW-VALUE-2"},
    )

    assert response.status_code == 422
    assert "SUPER-SECRET-PW-VALUE-2" not in response.text


def test_login_missing_username_does_not_echo_password(monkeypatch, tmp_path):
    """Same redaction applied to the pre-existing LoginRequest, which has
    the same password field / same leak class."""
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    client = _client()

    response = client.post("/auth/login", json={"password": "SUPER-SECRET-PW-VALUE-3"})

    assert response.status_code == 422
    assert "SUPER-SECRET-PW-VALUE-3" not in response.text


def test_self_delete_forbidden(monkeypatch, tmp_path):
    """Sole-remaining-operator self-delete: proves the operator count can
    never reach zero through the web."""
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path, username="alice", password="alices-password")

    response = client.post(
        "/auth/operators/delete",
        json={"username": "alice"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409

    listing = _list_operators(client, token)
    assert "alice" in [row["username"] for row in listing.json()]

    still_works = client.get("/auth/operators", headers={"Authorization": f"Bearer {token}"})
    assert still_works.status_code == 200
