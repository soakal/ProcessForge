"""API layer: /health and /sessions (auth, happy path, rate limiting)."""
from __future__ import annotations

import importlib
import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from auth.repository import AuthRepository
from kb.repository import KBRepository
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


def _seed_bare_business(db_path, tenant="acme", name="Bare Co"):
    """Migrate the schema and create a business with no children directly via
    KBRepository, mirroring tests/test_delete_business_repo.py's own seeding
    style. Used for the no-children edge case, which POST /sessions can't
    produce on its own (it always creates a full chain)."""
    _migrate(db_path)
    repo = KBRepository(db_path)
    try:
        business_id = str(uuid.uuid4())
        repo.put("businesses", {
            "id": business_id, "schema_version": 1, "tenant": tenant,
            "name": name, "meta": {},
        })
        return business_id
    finally:
        repo.close()


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


def test_get_recommendation_includes_session_id_when_resolvable(monkeypatch, tmp_path):
    """session_id is resolved server-side via the same tenant-scoped
    Opportunity -> Task lookup build_automation already uses, so
    recommendations.html can render a "View interview transcript" link."""
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
    session_body = session_response.json()
    recommendation = session_body["recommendations"][0]

    response = client.get(
        f"/recommendations/{recommendation['id']}",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["session_id"] == session_body["session_id"]


def test_get_recommendation_session_id_absent_when_no_tasks(monkeypatch, tmp_path):
    """A thin/missing Opportunity or Task set must never error — session_id
    just stays None, matching build_automation's own tolerance for the exact
    same case."""
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    recommendation = _create_recommendation(client, token, tenant="acme")

    # Remove every task backing this recommendation's opportunity so no
    # session_id can be resolved, same technique already used by
    # test_refine_recommendation_turns_with_no_resolvable_session_returns_409.
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM tasks WHERE tenant = ?", ("acme",))
        conn.commit()
    finally:
        conn.close()

    response = client.get(
        f"/recommendations/{recommendation['id']}",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["session_id"] is None


def test_get_recommendation_includes_roi_when_resolvable(monkeypatch, tmp_path):
    """roi_low_hrs/roi_high_hrs are resolved server-side from the
    Recommendation's Opportunity, via the same tenant-scoped Opportunity
    lookup _resolve_session_id uses, so recommendations.html can render ROI
    prominently."""
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
    session_body = session_response.json()
    recommendation = session_body["recommendations"][0]
    opportunity = session_body["opportunities"][0]

    response = client.get(
        f"/recommendations/{recommendation['id']}",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["roi_low_hrs"] == opportunity["roi_low_hrs"]
    assert body["roi_high_hrs"] == opportunity["roi_high_hrs"]


def test_get_recommendation_roi_absent_when_opportunity_missing(monkeypatch, tmp_path):
    """A missing Opportunity must never error — roi_low_hrs/roi_high_hrs just
    stay None, matching _resolve_session_id's own tolerance for the same
    unresolvable case."""
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    recommendation = _create_recommendation(client, token, tenant="acme")

    # Remove the opportunity backing this recommendation so ROI can't be
    # resolved, same DELETE technique already used by
    # test_get_recommendation_session_id_absent_when_no_tasks.
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM opportunities WHERE tenant = ?", ("acme",))
        conn.commit()
    finally:
        conn.close()

    response = client.get(
        f"/recommendations/{recommendation['id']}",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["roi_low_hrs"] is None
    assert body["roi_high_hrs"] is None


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


def test_approve_recommendation_includes_session_id_when_resolvable(monkeypatch, tmp_path):
    """session_id must be resolved on the approve response too, via the same
    shared _resolve_session_id() helper get_recommendation uses — otherwise
    the recommendations.html approve-button handler, which renders straight
    from this response without a fresh GET, would silently lose the "View
    interview transcript" link the moment a user clicks Approve."""
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
    session_body = session_response.json()
    recommendation = session_body["recommendations"][0]

    response = client.post(
        f"/recommendations/{recommendation['id']}/approve",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["session_id"] == session_body["session_id"]


def test_approve_recommendation_includes_roi_when_resolvable(monkeypatch, tmp_path):
    """roi_low_hrs/roi_high_hrs must be resolved on the approve response too,
    via the same shared _resolve_roi() helper get_recommendation uses —
    otherwise recommendations.html, which renders straight from this
    response without a fresh GET, would silently lose the ROI display the
    moment a user clicks Approve."""
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
    session_body = session_response.json()
    recommendation = session_body["recommendations"][0]
    opportunity = session_body["opportunities"][0]

    response = client.post(
        f"/recommendations/{recommendation['id']}/approve",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["roi_low_hrs"] == opportunity["roi_low_hrs"]
    assert body["roi_high_hrs"] == opportunity["roi_high_hrs"]


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


def test_link_automation_product_valid_url_persists(monkeypatch, tmp_path):
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

    link_response = client.post(
        f"/automations/{automation['id']}/link",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
        json={
            "product_url": "https://example.com/product/123",
            "product_notes": "Matches our reconciliation tool.",
        },
    )

    assert link_response.status_code == 200
    linked = link_response.json()
    assert linked["spec"]["product_url"] == "https://example.com/product/123"
    assert linked["spec"]["product_notes"] == "Matches our reconciliation tool."

    # Fetch the automation afterward directly from the DB (there is no GET
    # /automations/{id} endpoint, so this mirrors this file's existing
    # sqlite-verification style used by e.g. test_build_automation_*) and
    # confirm the persisted spec still matches.
    conn = sqlite3.connect(db_path)
    try:
        spec_json = conn.execute(
            "SELECT spec FROM automations WHERE id = ?", (automation["id"],)
        ).fetchone()[0]
    finally:
        conn.close()
    persisted_spec = json.loads(spec_json)
    assert persisted_spec["product_url"] == "https://example.com/product/123"
    assert persisted_spec["product_notes"] == "Matches our reconciliation tool."


def test_link_automation_product_malformed_url_rejected(monkeypatch, tmp_path):
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

    for bad_url in [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "data:text/html,<script>alert(1)</script>",
        "not-a-url",
        "ftp://example.com/file",
    ]:
        response = client.post(
            f"/automations/{automation['id']}/link",
            params={"tenant": "acme"},
            headers={"Authorization": f"Bearer {token}"},
            json={"product_url": bad_url},
        )
        assert response.status_code == 422, f"expected 422 for {bad_url!r}"

    # Confirm nothing was persisted from any of the rejected attempts.
    conn = sqlite3.connect(db_path)
    try:
        spec_json = conn.execute(
            "SELECT spec FROM automations WHERE id = ?", (automation["id"],)
        ).fetchone()[0]
    finally:
        conn.close()
    persisted_spec = json.loads(spec_json)
    assert "product_url" not in persisted_spec


def test_link_automation_product_wrong_tenant_returns_404(monkeypatch, tmp_path):
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
        f"/automations/{automation['id']}/link",
        params={"tenant": "other-tenant"},
        headers={"Authorization": f"Bearer {token}"},
        json={"product_url": "https://example.com/product/123"},
    )

    assert response.status_code == 404


def test_refine_recommendation_regenerates_handoff_and_new_revision(monkeypatch, tmp_path):
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
    original = build_response.json()
    original_open_questions = original["spec"]["handoff"]["open_questions"]
    assert any("input file" in q.lower() for q in original_open_questions)
    assert "revision" not in original["spec"]

    answer = "It comes from the shared drive's nightly export folder."
    refine_response = client.post(
        f"/recommendations/{recommendation['id']}/refine",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
        json={
            "turns": [
                {
                    "question": "Where does the input file live for this task?",
                    "answer": answer,
                }
            ]
        },
    )

    assert refine_response.status_code == 200
    refined = refine_response.json()
    assert refined["id"] != original["id"]
    assert refined["recommendation_id"] == original["recommendation_id"]
    # The new answer landed in the regenerated handoff, and the matching
    # open question was dropped.
    assert refined["spec"]["handoff"]["known"]["input_file_location"] == answer
    assert not any(
        "input file" in q.lower() for q in refined["spec"]["handoff"]["open_questions"]
    )
    # A new revision was recorded, reusing stages/qa.py's existing pattern.
    assert refined["spec"]["revision"] == 2

    # The prior automation is still fetchable, unmodified.
    repo = KBRepository(db_path)
    try:
        prior_row = repo.get("automations", original["id"], "acme")
    finally:
        repo.close()
    assert prior_row is not None
    assert prior_row["spec"] == original["spec"]


def test_refine_recommendation_unknown_id_returns_404(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    _create_recommendation(client, token, tenant="acme")

    response = client.post(
        "/recommendations/does-not-exist/refine",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
        json={"turns": []},
    )

    assert response.status_code == 404


def test_refine_recommendation_wrong_tenant_returns_404(monkeypatch, tmp_path):
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

    response = client.post(
        f"/recommendations/{recommendation['id']}/refine",
        params={"tenant": "other-tenant"},
        headers={"Authorization": f"Bearer {token}"},
        json={"turns": []},
    )

    assert response.status_code == 404


def test_refine_recommendation_turns_with_no_resolvable_session_returns_409(
    monkeypatch, tmp_path
):
    """If the caller submits non-empty turns but no session is resolvable for
    this recommendation's opportunity/tasks, refine must fail explicitly
    instead of silently discarding the answers while still returning 200 with
    a bumped revision and an unchanged handoff."""
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    recommendation = _create_recommendation(client, token, tenant="acme")
    _approve_recommendation(client, recommendation["id"], token, tenant="acme")

    # Remove every task backing this recommendation's opportunity so
    # refine_recommendation can't derive a session_id to attach turns to.
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM tasks WHERE tenant = ?", ("acme",))
        conn.commit()
    finally:
        conn.close()

    response = client.post(
        f"/recommendations/{recommendation['id']}/refine",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
        json={
            "turns": [
                {
                    "question": "Where does the input file live for this task?",
                    "answer": "It comes from the shared drive.",
                }
            ]
        },
    )

    assert response.status_code == 409
    assert "session" in response.json()["detail"].lower()

    # No new Automation was persisted, and no revision was recorded, for this
    # recommendation.
    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM automations WHERE recommendation_id = ?",
            (recommendation["id"],),
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 0


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


def _create_business(client, token, tenant="acme"):
    """Seed a real, persisted full chain (business/session/task/opportunity/
    recommendation) via POST /sessions and return the business_id."""
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
    return response.json()["business_id"]


def _delete_business(client, business_id, confirm_business_id, token, tenant="acme"):
    return client.post(
        f"/businesses/{business_id}/delete",
        params={"tenant": tenant},
        headers={"Authorization": f"Bearer {token}"},
        json={"confirm_business_id": confirm_business_id},
    )


def test_delete_business_happy_path_returns_counts(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    business_id = _create_business(client, token, tenant="acme")

    response = _delete_business(client, business_id, business_id, token, tenant="acme")

    assert response.status_code == 200
    assert response.json() == {
        "businesses": 1,
        "sessions": 1,
        "session_turns": 0,
        "tasks": 1,
        "workflow_graphs": 1,
        "opportunities": 1,
        "recommendations": 1,
        "automations": 0,
    }


def test_delete_business_with_no_children_returns_zero_counts(monkeypatch, tmp_path):
    """No-children edge case: POST /sessions always creates a full chain, so this
    seeds a bare business directly via the repository (already covered at the
    repository level in test_delete_business_repo.py) to exercise the endpoint's
    handling of an all-zero counts dict."""
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    business_id = _seed_bare_business(db_path, tenant="acme", name="Bare Co")

    response = _delete_business(client, business_id, business_id, token, tenant="acme")

    assert response.status_code == 200
    assert response.json() == {
        "businesses": 1,
        "sessions": 0,
        "session_turns": 0,
        "tasks": 0,
        "workflow_graphs": 0,
        "opportunities": 0,
        "recommendations": 0,
        "automations": 0,
    }


def test_delete_business_cross_tenant_returns_404_and_deletes_nothing(monkeypatch, tmp_path):
    """Real tenant-isolation test: a valid business_id under one tenant must be
    invisible (404, not 403 — don't leak that it exists) when the delete is
    attempted under another tenant, and nothing must actually be deleted."""
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    business_id = _create_business(client, token, tenant="acme")

    response = _delete_business(client, business_id, business_id, token, tenant="other-tenant")

    assert response.status_code == 404

    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM businesses WHERE id = ?", (business_id,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 1


def test_delete_business_wrong_confirmation_returns_400_and_deletes_nothing(monkeypatch, tmp_path):
    """Most important test in this cycle: a mismatched confirm_business_id must
    be rejected with 400 BEFORE any repository access, so the business row (and
    its whole child chain) must still exist afterward, verified directly
    against the sqlite file rather than through the API."""
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    business_id = _create_business(client, token, tenant="acme")

    response = _delete_business(
        client, business_id, "not-the-right-business-id", token, tenant="acme"
    )

    assert response.status_code == 400
    assert "confirm_business_id" in response.json()["detail"]

    conn = sqlite3.connect(db_path)
    try:
        business_count = conn.execute(
            "SELECT COUNT(*) FROM businesses WHERE id = ?", (business_id,)
        ).fetchone()[0]
        session_count = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE business_id = ?", (business_id,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert business_count == 1
    assert session_count == 1


def test_delete_business_unknown_id_returns_404(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)

    response = _delete_business(
        client, "does-not-exist", "does-not-exist", token, tenant="acme"
    )

    assert response.status_code == 404


def test_delete_business_missing_token_rejected(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    business_id = _create_business(client, token, tenant="acme")

    response = client.post(
        f"/businesses/{business_id}/delete",
        params={"tenant": "acme"},
        json={"confirm_business_id": business_id},
    )

    assert response.status_code == 401

    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM businesses WHERE id = ?", (business_id,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 1


def test_delete_business_invalid_token_rejected(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    business_id = _create_business(client, token, tenant="acme")

    response = client.post(
        f"/businesses/{business_id}/delete",
        params={"tenant": "acme"},
        headers={"Authorization": "Bearer garbage-token"},
        json={"confirm_business_id": business_id},
    )

    assert response.status_code == 401

    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM businesses WHERE id = ?", (business_id,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 1


def test_delete_business_twice_returns_404_on_second_attempt(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    business_id = _create_business(client, token, tenant="acme")

    first_response = _delete_business(client, business_id, business_id, token, tenant="acme")
    assert first_response.status_code == 200

    second_response = _delete_business(client, business_id, business_id, token, tenant="acme")

    assert second_response.status_code == 404


def _start_interview(client, token, tenant="acme", business_name="Test Co"):
    response = client.post(
        "/interviews",
        headers={"Authorization": f"Bearer {token}"},
        json={"business_name": business_name, "tenant": tenant},
    )
    assert response.status_code == 200
    return response.json()


def _answer_interview(client, session_id, answer, token, tenant="acme"):
    return client.post(
        f"/interviews/{session_id}/answer",
        params={"tenant": tenant},
        headers={"Authorization": f"Bearer {token}"},
        json={"answer": answer},
    )


def _assert_session_response_shape(body):
    assert isinstance(body["business_id"], str) and body["business_id"]
    assert isinstance(body["session_id"], str) and body["session_id"]
    assert body["task_count"] == 1
    assert len(body["opportunities"]) == 1
    opportunity = body["opportunities"][0]
    assert opportunity["roi_low_hrs"] < opportunity["roi_high_hrs"]
    assert opportunity["assumptions"]
    assert len(body["recommendations"]) == 1
    assert body["recommendations"][0]["approval_state"] == "draft"


def test_start_interview_returns_opening_question(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)

    started = _start_interview(client, token, tenant="acme")

    assert started["business_id"]
    assert started["session_id"]
    assert isinstance(started["question"], str) and started["question"]


def test_interview_full_flow_completes_after_six_deterministic_answers(monkeypatch, tmp_path):
    """With PROCESSFORGE_LLM_PROVIDER stripped (tests/conftest.py's autouse fixture),
    stages.interviewer.next_question always falls back to its deterministic 6-step
    ladder, keyed off the number of answers given so far: the 6th answer causes
    _next_question_deterministic to return None, which completes the interview and
    must return the same shape /sessions returns."""
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)

    started = _start_interview(client, token, tenant="acme")
    session_id = started["session_id"]

    first = _answer_interview(
        client, session_id, "We manually reconcile invoices every week.", token, tenant="acme"
    )
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["session_id"] == session_id
    assert isinstance(first_body["question"], str) and first_body["question"]

    second = _answer_interview(
        client, session_id, "It takes about 2 hours each time.", token, tenant="acme"
    )
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["session_id"] == session_id
    assert isinstance(second_body["question"], str) and second_body["question"]

    third = _answer_interview(
        client,
        session_id,
        "We'd like it automated so no one has to touch a spreadsheet.",
        token,
        tenant="acme",
    )
    assert third.status_code == 200
    third_body = third.json()
    assert third_body["session_id"] == session_id
    assert isinstance(third_body["question"], str) and third_body["question"]

    fourth = _answer_interview(
        client, session_id, "The files live in a shared network drive.", token, tenant="acme"
    )
    assert fourth.status_code == 200
    fourth_body = fourth.json()
    assert fourth_body["session_id"] == session_id
    assert isinstance(fourth_body["question"], str) and fourth_body["question"]

    fifth = _answer_interview(
        client, session_id, "Only rows where status is 'open'.", token, tenant="acme"
    )
    assert fifth.status_code == 200
    fifth_body = fifth.json()
    assert fifth_body["session_id"] == session_id
    assert isinstance(fifth_body["question"], str) and fifth_body["question"]

    sixth = _answer_interview(
        client, session_id, "An Excel spreadsheet.", token, tenant="acme"
    )
    assert sixth.status_code == 200
    _assert_session_response_shape(sixth.json())


def test_interview_turn_cap_forces_completion_at_default_twelfth_answer(monkeypatch, tmp_path):
    """Mock next_question to always want another question, so only the hard
    _max_interview_answers() cap (not the deterministic ladder) can end the
    interview — confirms the default cap of 12 forces completion on the 12th
    answer, and not before."""
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)

    monkeypatch.setattr(
        "stages.interviewer.next_question", lambda turns, ctx: "Tell me more?"
    )

    started = _start_interview(client, token, tenant="acme")
    session_id = started["session_id"]

    for i in range(11):
        response = _answer_interview(client, session_id, f"Answer {i + 1}.", token, tenant="acme")
        assert response.status_code == 200
        body = response.json()
        assert body["session_id"] == session_id
        assert body["question"] == "Tell me more?"

    twelfth = _answer_interview(client, session_id, "Answer 12.", token, tenant="acme")
    assert twelfth.status_code == 200
    _assert_session_response_shape(twelfth.json())


def test_interview_turn_cap_env_override_forces_completion_at_configured_value(monkeypatch, tmp_path):
    """PROCESSFORGE_MAX_INTERVIEW_ANSWERS=8 lowers the cap: with next_question
    always wanting another question, completion is forced on the 8th answer."""
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    monkeypatch.setenv("PROCESSFORGE_MAX_INTERVIEW_ANSWERS", "8")
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)

    monkeypatch.setattr(
        "stages.interviewer.next_question", lambda turns, ctx: "Tell me more?"
    )

    started = _start_interview(client, token, tenant="acme")
    session_id = started["session_id"]

    for i in range(7):
        response = _answer_interview(client, session_id, f"Answer {i + 1}.", token, tenant="acme")
        assert response.status_code == 200
        body = response.json()
        assert body["session_id"] == session_id
        assert body["question"] == "Tell me more?"

    eighth = _answer_interview(client, session_id, "Answer 8.", token, tenant="acme")
    assert eighth.status_code == 200
    _assert_session_response_shape(eighth.json())


def _assert_interview_cap_falls_back_to_default_twelve(monkeypatch, tmp_path, raw_env_value):
    """Shared body for the blank/non-int/zero -> default-12 fallback cases."""
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    monkeypatch.setenv("PROCESSFORGE_MAX_INTERVIEW_ANSWERS", raw_env_value)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)

    monkeypatch.setattr(
        "stages.interviewer.next_question", lambda turns, ctx: "Tell me more?"
    )

    started = _start_interview(client, token, tenant="acme")
    session_id = started["session_id"]

    for i in range(11):
        response = _answer_interview(client, session_id, f"Answer {i + 1}.", token, tenant="acme")
        assert response.status_code == 200
        assert response.json()["question"] == "Tell me more?"

    twelfth = _answer_interview(client, session_id, "Answer 12.", token, tenant="acme")
    assert twelfth.status_code == 200
    _assert_session_response_shape(twelfth.json())


def test_interview_turn_cap_env_blank_falls_back_to_default_twelve(monkeypatch, tmp_path):
    _assert_interview_cap_falls_back_to_default_twelve(monkeypatch, tmp_path, "")


def test_interview_turn_cap_env_non_integer_falls_back_to_default_twelve(monkeypatch, tmp_path):
    _assert_interview_cap_falls_back_to_default_twelve(monkeypatch, tmp_path, "garbage")


def test_interview_turn_cap_env_zero_falls_back_to_default_twelve(monkeypatch, tmp_path):
    _assert_interview_cap_falls_back_to_default_twelve(monkeypatch, tmp_path, "0")


def test_interview_real_fallback_ladder_still_completes_at_six_with_cap_raised(monkeypatch, tmp_path):
    """Locks in that raising the cap doesn't change the no-provider deterministic
    fallback ladder's own independent 6-answer length (PROCESSFORGE_LLM_PROVIDER
    is stripped by tests/conftest.py's autouse fixture, so next_question always
    falls back to stages.interviewer._next_question_deterministic here)."""
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    monkeypatch.setenv("PROCESSFORGE_MAX_INTERVIEW_ANSWERS", "12")
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)

    started = _start_interview(client, token, tenant="acme")
    session_id = started["session_id"]

    answers = [
        "We manually reconcile invoices every week.",
        "It takes about 2 hours each time.",
        "We'd like it automated so no one has to touch a spreadsheet.",
        "The files live in a shared network drive.",
        "Only rows where status is 'open'.",
        "An Excel spreadsheet.",
    ]
    for answer in answers[:-1]:
        response = _answer_interview(client, session_id, answer, token, tenant="acme")
        assert response.status_code == 200
        assert isinstance(response.json()["question"], str) and response.json()["question"]

    sixth = _answer_interview(client, session_id, answers[-1], token, tenant="acme")
    assert sixth.status_code == 200
    _assert_session_response_shape(sixth.json())


def test_answer_on_already_complete_interview_returns_409(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)

    started = _start_interview(client, token, tenant="acme")
    session_id = started["session_id"]

    _answer_interview(client, session_id, "We manually reconcile invoices every week.", token, tenant="acme")
    _answer_interview(client, session_id, "It takes about 2 hours each time.", token, tenant="acme")
    _answer_interview(
        client,
        session_id,
        "We'd like it automated so no one has to touch a spreadsheet.",
        token,
        tenant="acme",
    )
    _answer_interview(client, session_id, "The files live in a shared network drive.", token, tenant="acme")
    _answer_interview(client, session_id, "Only rows where status is 'open'.", token, tenant="acme")
    completed = _answer_interview(client, session_id, "An Excel spreadsheet.", token, tenant="acme")

    assert completed.status_code == 200

    seventh = _answer_interview(client, session_id, "One more thing.", token, tenant="acme")

    assert seventh.status_code == 409


def test_answer_interview_wrong_tenant_returns_404(monkeypatch, tmp_path):
    """Real tenant-isolation test: a session started under one tenant must be
    invisible (404, not 403 — don't leak that it exists) when answered under
    another tenant."""
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)

    started = _start_interview(client, token, tenant="acme")
    session_id = started["session_id"]

    response = _answer_interview(client, session_id, "Some answer.", token, tenant="other-tenant")

    assert response.status_code == 404


def test_answer_interview_unknown_session_id_returns_404(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)

    response = _answer_interview(client, "does-not-exist", "Some answer.", token, tenant="acme")

    assert response.status_code == 404


def test_get_interview_transcript_happy_path(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)

    started = _start_interview(client, token, tenant="acme")
    session_id = started["session_id"]
    _answer_interview(
        client, session_id, "We manually reconcile invoices every week.", token, tenant="acme"
    )

    response = client.get(
        f"/interviews/{session_id}/transcript",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    turns = response.json()
    # opening question (0), the answer just submitted (1), the follow-up
    # question next_question asked in response (2) — ordered by turn_index.
    assert [t["turn_index"] for t in turns] == [0, 1, 2]
    assert turns[0] == {"turn_index": 0, "role": "question", "content": started["question"]}
    assert turns[1]["role"] == "answer"
    assert turns[1]["content"] == "We manually reconcile invoices every week."
    assert turns[2]["role"] == "question"


def test_get_interview_transcript_wrong_tenant_returns_404(monkeypatch, tmp_path):
    """Real tenant-isolation test: a session started under one tenant must be
    invisible (404, not 403 — don't leak that it exists) when its transcript is
    requested under another tenant."""
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)

    started = _start_interview(client, token, tenant="acme")
    session_id = started["session_id"]

    response = client.get(
        f"/interviews/{session_id}/transcript",
        params={"tenant": "other-tenant"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404


def test_start_interview_missing_token_rejected(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    client = _client()

    response = client.post(
        "/interviews",
        json={"business_name": "Test Co", "tenant": "acme"},
    )

    assert response.status_code == 401


def test_answer_interview_missing_token_rejected(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    started = _start_interview(client, token, tenant="acme")

    response = client.post(
        f"/interviews/{started['session_id']}/answer",
        params={"tenant": "acme"},
        json={"answer": "Some answer."},
    )

    assert response.status_code == 401
