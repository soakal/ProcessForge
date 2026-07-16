"""API layer: /health and /sessions (auth, happy path, rate limiting)."""
from __future__ import annotations

import importlib
import os
import time

from fastapi.testclient import TestClient


def _client():
    from api.main import app

    return TestClient(app)


def _set_env(monkeypatch, tmp_path, token="secret-token", rate_limit=None):
    monkeypatch.setenv("PROCESSFORGE_API_TOKEN", token)
    monkeypatch.setenv("PROCESSFORGE_DB_PATH", str(tmp_path / "test.db"))
    if rate_limit is not None:
        monkeypatch.setenv("PROCESSFORGE_RATE_LIMIT_PER_MINUTE", str(rate_limit))


def _create_recommendation(client, tenant="acme"):
    """Seed a real, persisted Business/Task/Opportunity/Recommendation chain via
    POST /sessions and return the first recommendation from the response."""
    response = client.post(
        "/sessions",
        headers={"Authorization": "Bearer secret-token"},
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


def test_sessions_wrong_token_rejected(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path)
    client = _client()

    response = client.post(
        "/sessions",
        headers={"Authorization": "Bearer wrong-token"},
        json={
            "business_name": "Test Co",
            "tenant": "test-tenant",
            "answers": ["We manually reconcile invoices every week."],
        },
    )

    assert response.status_code == 401


def test_sessions_valid_token_returns_session_result(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    client = _client()

    response = client.post(
        "/sessions",
        headers={"Authorization": "Bearer secret-token"},
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
    client = _client()

    response = client.post(
        "/sessions",
        headers={"Authorization": "Bearer secret-token"},
        json={
            "business_name": "Test Co",
            "tenant": "test-tenant",
            "answers": ["We manually reconcile invoices every week."],
        },
    )

    assert response.status_code == 200


def test_sessions_rate_limit_returns_429(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=2)
    client = _client()

    payload = {
        "business_name": "Test Co",
        "tenant": "test-tenant",
        "answers": ["We manually reconcile invoices every week."],
    }
    headers = {"Authorization": "Bearer secret-token"}

    statuses = [
        client.post("/sessions", headers=headers, json=payload).status_code
        for _ in range(5)
    ]

    assert 429 in statuses


def test_env_vars_from_dotenv_file_are_loaded_at_import_time(monkeypatch, tmp_path):
    """FIX 1 regression: api/main.py must call load_dotenv() at import time — uvicorn does
    not read .env on its own, so the documented `python -m uvicorn api.main:app` setup would
    never see PROCESSFORGE_API_TOKEN (etc.) from a .env file without this call."""
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
    """FIX 2 regression: hmac.compare_digest raises TypeError (-> 500) on non-ASCII str
    input; a non-ASCII bearer token must be rejected with 401, not crash."""
    from api.main import app

    _set_env(monkeypatch, tmp_path)
    # raise_server_exceptions=False so an unhandled exception surfaces as a real 500
    # response instead of propagating out of the test call.
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/sessions",
        # httpx's own header normalization rejects non-ASCII str header values before
        # the request is even sent, so use raw utf-8 bytes to actually exercise the
        # server-side hmac.compare_digest() call with a non-ASCII token.
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
    bad_headers = {"Authorization": "Bearer wrong-token"}

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
    client = _client()
    recommendation = _create_recommendation(client, tenant="acme")

    response = client.get(
        f"/recommendations/{recommendation['id']}",
        params={"tenant": "acme"},
        headers={"Authorization": "Bearer secret-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == recommendation["id"]
    assert body["opportunity_id"] == recommendation["opportunity_id"]
    assert body["summary"] == recommendation["summary"]
    assert body["approval_state"] == "draft"


def test_get_recommendation_unknown_id_returns_404(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    client = _client()
    _create_recommendation(client, tenant="acme")

    response = client.get(
        "/recommendations/does-not-exist",
        params={"tenant": "acme"},
        headers={"Authorization": "Bearer secret-token"},
    )

    assert response.status_code == 404


def test_get_recommendation_wrong_tenant_returns_404(monkeypatch, tmp_path):
    """Real tenant-isolation test: a valid id under one tenant must be invisible
    (404, not 403 — don't leak that the id exists) when queried under another."""
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    client = _client()
    recommendation = _create_recommendation(client, tenant="acme")

    response = client.get(
        f"/recommendations/{recommendation['id']}",
        params={"tenant": "other-tenant"},
        headers={"Authorization": "Bearer secret-token"},
    )

    assert response.status_code == 404


def test_approve_recommendation_flips_state_to_approved(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    client = _client()
    recommendation = _create_recommendation(client, tenant="acme")

    approve_response = client.post(
        f"/recommendations/{recommendation['id']}/approve",
        params={"tenant": "acme"},
        headers={"Authorization": "Bearer secret-token"},
    )

    assert approve_response.status_code == 200
    assert approve_response.json()["approval_state"] == "approved"

    get_response = client.get(
        f"/recommendations/{recommendation['id']}",
        params={"tenant": "acme"},
        headers={"Authorization": "Bearer secret-token"},
    )

    assert get_response.status_code == 200
    assert get_response.json()["approval_state"] == "approved"


def test_approve_recommendation_unknown_id_returns_404(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    client = _client()
    _create_recommendation(client, tenant="acme")

    response = client.post(
        "/recommendations/does-not-exist/approve",
        params={"tenant": "acme"},
        headers={"Authorization": "Bearer secret-token"},
    )

    assert response.status_code == 404


def test_get_recommendation_missing_token_rejected(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    client = _client()
    recommendation = _create_recommendation(client, tenant="acme")

    response = client.get(
        f"/recommendations/{recommendation['id']}",
        params={"tenant": "acme"},
    )

    assert response.status_code == 401


def test_approve_recommendation_missing_token_rejected(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    client = _client()
    recommendation = _create_recommendation(client, tenant="acme")

    response = client.post(
        f"/recommendations/{recommendation['id']}/approve",
        params={"tenant": "acme"},
    )

    assert response.status_code == 401
