"""API layer: /health and /sessions (auth, happy path, rate limiting)."""
from __future__ import annotations

from fastapi.testclient import TestClient


def _client():
    from api.main import app

    return TestClient(app)


def _set_env(monkeypatch, tmp_path, token="secret-token", rate_limit=None):
    monkeypatch.setenv("PROCESSFORGE_API_TOKEN", token)
    monkeypatch.setenv("PROCESSFORGE_DB_PATH", str(tmp_path / "test.db"))
    if rate_limit is not None:
        monkeypatch.setenv("PROCESSFORGE_RATE_LIMIT_PER_MINUTE", str(rate_limit))


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
