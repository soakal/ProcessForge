"""API layer: /health and /sessions (auth, happy path, rate limiting)."""
from __future__ import annotations

import importlib
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from auth.repository import AuthRepository
from pipeline import _migrate


def _client():
    from api.main import app

    return TestClient(app)


def _seed_operator(db_path, username="alice", password="correct-horse-battery"):
    """Migrate the schema and create an operator directly via AuthRepository,
    mirroring this file's existing direct-repo seeding style."""
    _migrate(db_path)
    repo = AuthRepository(db_path)
    try:
        repo.create_operator(username, password)
    finally:
        repo.close()


def _login_token(client, db_path, username="alice", password="correct-horse-battery"):
    """Seed an operator and log in via POST /auth/login to obtain a real bearer
    token — the replacement for the old shared PROCESSFORGE_API_TOKEN in tests
    that call one of the 5 protected endpoints."""
    _seed_operator(db_path, username=username, password=password)
    response = client.post(
        "/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return response.json()["token"]


def _set_env(monkeypatch, tmp_path, rate_limit=None):
    monkeypatch.setenv("PROCESSFORGE_DB_PATH", str(tmp_path / "test.db"))
    if rate_limit is not None:
        monkeypatch.setenv("PROCESSFORGE_RATE_LIMIT_PER_MINUTE", str(rate_limit))


def _create_recommendation(client, token, tenant="acme"):
    """Seed a real, persisted Business/Task/Opportunity/Recommendation chain via
    POST /sessions and return the first recommendation from the response."""
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
    return response.json()["recommendations"][0]


def test_health_unauthenticated(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path)
    client = _client()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_sessions_missing_token_rejected(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path)
    client = _client()

    response = client.post(
        "/sessions",
        json={
            "business_name": "Test Co",
            "tenant": "test-tenant",
            "answers": ["We manually reconcile invoices every week."],
        },
    )

    assert response.status_code == 401


def test_sessions_garbage_token_rejected(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path)
    client = _client()

    response = client.post(
        "/sessions",
        headers={"Authorization": "Bearer garbage-token"},
        json={
            "business_name": "Test Co",
            "tenant": "test-tenant",
            "answers": ["We manually reconcile invoices every week."],
        },
    )

    assert response.status_code == 401


def test_sessions_valid_token_returns_session_result(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)

    response = client.post(
        "/sessions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "business_name": "Test Co",
            "tenant": "test-tenant",
            "answers": [
                "We manually reconcile invoices every week.",
                "It takes about 2 hours each time.",
                "We'd like it automated so no one has to touch a spreadsheet.",
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["task_count"] == 1
    assert len(body["opportunities"]) == 1
    opportunity = body["opportunities"][0]
    assert opportunity["roi_low_hrs"] < opportunity["roi_high_hrs"]
    assert opportunity["assumptions"]
    assert len(body["recommendations"]) == 1
    assert body["recommendations"][0]["approval_state"] == "draft"


def test_sessions_blank_rate_limit_env_falls_back_to_default(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit="")
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)

    response = client.post(
        "/sessions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "business_name": "Test Co",
            "tenant": "test-tenant",
            "answers": ["We manually reconcile invoices every week."],
        },
    )

    assert response.status_code == 200


def test_sessions_rate_limit_returns_429(monkeypatch, tmp_path):
    # Log in under a generous rate limit first — logging in itself counts against
    # the same per-IP bucket the /sessions calls below will use — then drop to a
    # tight limit and clear the bucket so the 5 /sessions attempts below start
    # from a clean count instead of inheriting usage from the login call (or from
    # other tests sharing the same in-process bucket dict within this minute).
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)

    from api.main import _rate_limit_buckets

    monkeypatch.setenv("PROCESSFORGE_RATE_LIMIT_PER_MINUTE", "2")
    _rate_limit_buckets.clear()

    payload = {
        "business_name": "Test Co",
        "tenant": "test-tenant",
        "answers": ["We manually reconcile invoices every week."],
    }
    headers = {"Authorization": f"Bearer {token}"}

    statuses = [
        client.post("/sessions", headers=headers, json=payload).status_code
        for _ in range(5)
    ]

    assert 429 in statuses


def test_env_vars_from_dotenv_file_are_loaded_at_import_time(monkeypatch, tmp_path):
    """FIX 1 regression: api/main.py must call load_dotenv() at import time — uvicorn does
    not read .env on its own, so the documented `python -m uvicorn api.main:app` setup would
    never see settings (e.g. PROCESSFORGE_RATE_LIMIT_PER_MINUTE) from a .env file without
    this call."""
    import dotenv.main as dotenv_main

    import api.main as main_module

    probe_var = "PROCESSFORGE_TEST_DOTENV_PROBE"
    env_file = tmp_path / ".env"
    env_file.write_text(f"{probe_var}=loaded-from-dotenv\n", encoding="utf-8")

    monkeypatch.delenv(probe_var, raising=False)
    # load_dotenv() with no explicit path calls find_dotenv() internally to locate the
    # .env file; point that at our temp file instead of touching the real repo's .env.
    monkeypatch.setattr(dotenv_main, "find_dotenv", lambda *a, **kw: str(env_file))

    try:
        importlib.reload(main_module)
        assert os.environ.get(probe_var) == "loaded-from-dotenv"
    finally:
        monkeypatch.delenv(probe_var, raising=False)


def test_sessions_non_ascii_token_rejected_not_500(monkeypatch, tmp_path):
    """FIX 2 regression (historical): a non-ASCII bearer token used to crash the old
    hmac.compare_digest-based check with a TypeError (-> 500). That comparison is gone
    now that auth is a real DB lookup, but a non-ASCII token is still just an unresolvable
    token and must be rejected with 401, not crash."""
    from api.main import app

    _set_env(monkeypatch, tmp_path)
    # raise_server_exceptions=False so an unhandled exception surfaces as a real 500
    # response instead of propagating out of the test call.
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/sessions",
        # httpx's own header normalization rejects non-ASCII str header values before
        # the request is even sent, so use raw utf-8 bytes to actually exercise the
        # server-side token lookup with a non-ASCII token.
        headers={"Authorization": "Bearer café-token".encode("utf-8")},
        json={
            "business_name": "Test Co",
            "tenant": "test-tenant",
            "answers": ["We manually reconcile invoices every week."],
        },
    )

    assert response.status_code == 401


def test_sessions_bad_token_requests_count_against_rate_limit(monkeypatch, tmp_path):
    """FIX 3 regression: rate limiting must run before auth so token brute-forcing
    (repeated bad-token requests) is throttled, not just repeated valid requests."""
    _set_env(monkeypatch, tmp_path, rate_limit=2)
    client = _client()

    payload = {
        "business_name": "Test Co",
        "tenant": "test-tenant",
        "answers": ["We manually reconcile invoices every week."],
    }
    bad_headers = {"Authorization": "Bearer garbage-token"}

    statuses = [
        client.post("/sessions", headers=bad_headers, json=payload).status_code
        for _ in range(5)
    ]

    assert 429 in statuses


def test_check_rate_limit_prunes_stale_window_entries(monkeypatch):
    """FIX 4 regression: _rate_limit_buckets must not grow without bound — stale windows
    (not the current or immediately-prior one) must be evicted on each check."""
    from api.main import _check_rate_limit, _rate_limit_buckets

    monkeypatch.delenv("PROCESSFORGE_RATE_LIMIT_PER_MINUTE", raising=False)
    _rate_limit_buckets.clear()

    current_window = int(time.time() // 60)
    stale_window = current_window - 100
    _rate_limit_buckets[("1.2.3.4", stale_window)] = 5

    _check_rate_limit("1.2.3.4")

    assert ("1.2.3.4", stale_window) not in _rate_limit_buckets
    assert all(k[1] in (current_window, current_window - 1) for k in _rate_limit_buckets)


def test_get_recommendation_happy_path(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    recommendation = _create_recommendation(client, token, tenant="acme")

    response = client.get(
        f"/recommendations/{recommendation['id']}",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == recommendation["id"]
    assert body["opportunity_id"] == recommendation["opportunity_id"]
    assert body["summary"] == recommendation["summary"]
    assert body["approval_state"] == "draft"


def test_get_recommendation_unknown_id_returns_404(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    _create_recommendation(client, token, tenant="acme")

    response = client.get(
        "/recommendations/does-not-exist",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404


def test_get_recommendation_wrong_tenant_returns_404(monkeypatch, tmp_path):
    """Real tenant-isolation test: a valid id under one tenant must be invisible
    (404, not 403 — don't leak that the id exists) when queried under another."""
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    recommendation = _create_recommendation(client, token, tenant="acme")

    response = client.get(
        f"/recommendations/{recommendation['id']}",
        params={"tenant": "other-tenant"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404


def test_approve_recommendation_flips_state_to_approved(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    recommendation = _create_recommendation(client, token, tenant="acme")

    approve_response = client.post(
        f"/recommendations/{recommendation['id']}/approve",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert approve_response.status_code == 200
    assert approve_response.json()["approval_state"] == "approved"

    get_response = client.get(
        f"/recommendations/{recommendation['id']}",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert get_response.status_code == 200
    assert get_response.json()["approval_state"] == "approved"


def test_approve_recommendation_unknown_id_returns_404(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    _create_recommendation(client, token, tenant="acme")

    response = client.post(
        "/recommendations/does-not-exist/approve",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404


def test_get_recommendation_missing_token_rejected(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    recommendation = _create_recommendation(client, token, tenant="acme")

    response = client.get(
        f"/recommendations/{recommendation['id']}",
        params={"tenant": "acme"},
    )

    assert response.status_code == 401


def test_approve_recommendation_missing_token_rejected(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    recommendation = _create_recommendation(client, token, tenant="acme")

    response = client.post(
        f"/recommendations/{recommendation['id']}/approve",
        params={"tenant": "acme"},
    )

    assert response.status_code == 401


def _approve_recommendation(client, recommendation_id, token, tenant="acme"):
    response = client.post(
        f"/recommendations/{recommendation_id}/approve",
        params={"tenant": tenant},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    return response.json()


def _get_audit_log(client, token, tenant, record_id=None):
    params = {"tenant": tenant}
    if record_id is not None:
        params["record_id"] = record_id
    response = client.get(
        "/audit-log",
        params=params,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    return response.json()


def test_approve_recommendation_writes_audit_log_entry(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    recommendation = _create_recommendation(client, token, tenant="acme")

    _approve_recommendation(client, recommendation["id"], token, tenant="acme")

    entries = _get_audit_log(client, token, tenant="acme")

    matching = [e for e in entries if e["record_id"] == recommendation["id"]]
    assert len(matching) == 1
    entry = matching[0]
    assert entry["field"] == "approval_state"
    assert entry["old_value"] == "draft"
    assert entry["new_value"] == "approved"
    assert entry["operator_id"]


def test_audit_log_isolated_by_tenant(monkeypatch, tmp_path):
    """Real tenant-isolation test: an audit entry written under one tenant must
    not appear when the audit log is queried under a different tenant."""
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    recommendation = _create_recommendation(client, token, tenant="acme")
    _approve_recommendation(client, recommendation["id"], token, tenant="acme")

    other_tenant_entries = _get_audit_log(client, token, tenant="other-tenant")
    assert all(e["record_id"] != recommendation["id"] for e in other_tenant_entries)

    acme_entries = _get_audit_log(client, token, tenant="acme")
    assert any(e["record_id"] == recommendation["id"] for e in acme_entries)


def test_audit_log_record_id_filter(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    recommendation_one = _create_recommendation(client, token, tenant="acme")
    recommendation_two = _create_recommendation(client, token, tenant="acme")
    _approve_recommendation(client, recommendation_one["id"], token, tenant="acme")
    _approve_recommendation(client, recommendation_two["id"], token, tenant="acme")

    entries = _get_audit_log(client, token, tenant="acme", record_id=recommendation_one["id"])

    assert len(entries) == 1
    assert entries[0]["record_id"] == recommendation_one["id"]


def test_audit_log_missing_token_rejected(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    client = _client()

    response = client.get("/audit-log", params={"tenant": "acme"})

    assert response.status_code == 401


def test_audit_log_empty_for_tenant_with_no_entries(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)

    entries = _get_audit_log(client, token, tenant="empty-tenant")

    assert entries == []


def test_reapproving_already_approved_recommendation_does_not_double_log(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    recommendation = _create_recommendation(client, token, tenant="acme")

    _approve_recommendation(client, recommendation["id"], token, tenant="acme")
    _approve_recommendation(client, recommendation["id"], token, tenant="acme")

    entries = _get_audit_log(client, token, tenant="acme", record_id=recommendation["id"])

    assert len(entries) == 1


def test_build_automation_on_unapproved_recommendation_returns_409(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    recommendation = _create_recommendation(client, token, tenant="acme")

    response = client.post(
        f"/recommendations/{recommendation['id']}/build",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409
    assert "approved" in response.json()["detail"]

    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM automations WHERE recommendation_id = ?",
            (recommendation["id"],),
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 0


def test_build_automation_on_approved_recommendation_returns_200(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    recommendation = _create_recommendation(client, token, tenant="acme")
    _approve_recommendation(client, recommendation["id"], token, tenant="acme")

    response = client.post(
        f"/recommendations/{recommendation['id']}/build",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["recommendation_id"] == recommendation["id"]
    assert body["spec"]
    assert body["blast_radius"]
    assert body["rollback"]
    assert body["approval_state"] == "draft"


def test_build_automation_unknown_id_returns_404(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    _create_recommendation(client, token, tenant="acme")

    response = client.post(
        "/recommendations/does-not-exist/build",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404


def test_build_automation_wrong_tenant_returns_404(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    recommendation = _create_recommendation(client, token, tenant="acme")
    _approve_recommendation(client, recommendation["id"], token, tenant="acme")

    response = client.post(
        f"/recommendations/{recommendation['id']}/build",
        params={"tenant": "other-tenant"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404


def test_build_automation_missing_token_rejected(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    recommendation = _create_recommendation(client, token, tenant="acme")

    response = client.post(
        f"/recommendations/{recommendation['id']}/build",
        params={"tenant": "acme"},
    )

    assert response.status_code == 401


def test_submit_automation_feedback_happy_path(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    recommendation = _create_recommendation(client, token, tenant="acme")
    _approve_recommendation(client, recommendation["id"], token, tenant="acme")

    build_response = client.post(
        f"/recommendations/{recommendation['id']}/build",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert build_response.status_code == 200
    automation = build_response.json()

    feedback = "The rollback step is missing a notification to the on-call engineer."
    feedback_response = client.post(
        f"/automations/{automation['id']}/feedback",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
        json={"feedback": feedback},
    )

    assert feedback_response.status_code == 200
    revised = feedback_response.json()
    assert revised["id"] != automation["id"]
    assert revised["recommendation_id"] == automation["recommendation_id"]
    assert revised["spec"]["feedback"] == feedback
    assert feedback in revised["spec"]["revision_notes"]


def test_submit_automation_feedback_unknown_id_returns_404(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    _create_recommendation(client, token, tenant="acme")

    response = client.post(
        "/automations/does-not-exist/feedback",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
        json={"feedback": "Needs more detail."},
    )

    assert response.status_code == 404


def test_submit_automation_feedback_wrong_tenant_returns_404(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    recommendation = _create_recommendation(client, token, tenant="acme")
    _approve_recommendation(client, recommendation["id"], token, tenant="acme")

    build_response = client.post(
        f"/recommendations/{recommendation['id']}/build",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert build_response.status_code == 200
    automation = build_response.json()

    response = client.post(
        f"/automations/{automation['id']}/feedback",
        params={"tenant": "other-tenant"},
        headers={"Authorization": f"Bearer {token}"},
        json={"feedback": "Needs more detail."},
    )

    assert response.status_code == 404


def test_submit_automation_feedback_missing_token_rejected(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    recommendation = _create_recommendation(client, token, tenant="acme")
    _approve_recommendation(client, recommendation["id"], token, tenant="acme")

    build_response = client.post(
        f"/recommendations/{recommendation['id']}/build",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert build_response.status_code == 200
    automation = build_response.json()

    response = client.post(
        f"/automations/{automation['id']}/feedback",
        params={"tenant": "acme"},
        json={"feedback": "Needs more detail."},
    )

    assert response.status_code == 401


def test_login_correct_credentials_returns_token(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    _seed_operator(db_path, username="alice", password="correct-horse-battery")
    client = _client()

    response = client.post(
        "/auth/login",
        json={"username": "alice", "password": "correct-horse-battery"},
    )

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["token"], str)
    assert body["token"]


def test_login_wrong_password_rejected(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    _seed_operator(db_path, username="alice", password="correct-horse-battery")
    client = _client()

    response = client.post(
        "/auth/login",
        json={"username": "alice", "password": "wrong-password"},
    )

    assert response.status_code == 401


def test_login_unknown_username_matches_wrong_password_response(monkeypatch, tmp_path):
    """Unknown username and wrong password must be indistinguishable to the caller —
    identical status code and identical response body, not just "both happen to be 401"."""
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    _seed_operator(db_path, username="alice", password="correct-horse-battery")
    client = _client()

    wrong_password_response = client.post(
        "/auth/login",
        json={"username": "alice", "password": "wrong-password"},
    )
    unknown_username_response = client.post(
        "/auth/login",
        json={"username": "does-not-exist", "password": "wrong-password"},
    )

    assert wrong_password_response.status_code == 401
    assert unknown_username_response.status_code == 401
    assert wrong_password_response.status_code == unknown_username_response.status_code
    assert wrong_password_response.json() == unknown_username_response.json()


def test_login_rate_limited(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=2)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    _seed_operator(db_path, username="alice", password="correct-horse-battery")
    client = _client()

    statuses = [
        client.post(
            "/auth/login",
            json={"username": "alice", "password": "wrong-password"},
        ).status_code
        for _ in range(5)
    ]

    assert 429 in statuses


def test_logout_valid_token_then_same_token_rejected(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    _seed_operator(db_path, username="alice", password="correct-horse-battery")
    client = _client()

    login_response = client.post(
        "/auth/login",
        json={"username": "alice", "password": "correct-horse-battery"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["token"]

    logout_response = client.post(
        "/auth/logout",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert logout_response.status_code == 200

    second_logout_response = client.post(
        "/auth/logout",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert second_logout_response.status_code == 401


def test_logout_missing_token_rejected(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    client = _client()

    response = client.post("/auth/logout")

    assert response.status_code == 401


def test_logout_invalid_token_rejected(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    client = _client()

    response = client.post(
        "/auth/logout",
        headers={"Authorization": "Bearer not-a-real-token"},
    )

    assert response.status_code == 401


def test_logout_rate_limited(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=2)
    client = _client()

    statuses = [
        client.post(
            "/auth/logout",
            headers={"Authorization": "Bearer not-a-real-token"},
        ).status_code
        for _ in range(5)
    ]

    assert 429 in statuses


def test_login_e2e_authenticates_protected_endpoint(monkeypatch, tmp_path):
    """End-to-end: a token obtained via a real POST /auth/login call must
    successfully authenticate against a protected endpoint (POST /sessions) —
    this is now the only auth path, replacing the old shared static token."""
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)

    response = client.post(
        "/sessions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "business_name": "Test Co",
            "tenant": "test-tenant",
            "answers": ["We manually reconcile invoices every week."],
        },
    )

    assert response.status_code == 200
    assert response.json()["task_count"] == 1


def test_expired_token_rejected_on_protected_endpoint(monkeypatch, tmp_path):
    """An expired token must be rejected on a protected endpoint the same as a
    nonexistent one — mirrors the direct-repo expiry test in
    tests/test_auth_repository.py, but exercised through POST /sessions
    instead of calling AuthRepository directly."""
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)

    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("UPDATE auth_tokens SET expires_at = ? WHERE token = ?", (past, token))
        conn.commit()
    finally:
        conn.close()

    response = client.post(
        "/sessions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "business_name": "Test Co",
            "tenant": "test-tenant",
            "answers": ["We manually reconcile invoices every week."],
        },
    )

    assert response.status_code == 401
