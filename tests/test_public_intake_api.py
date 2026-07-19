"""Public lead intake feature (docs/FEATURE-SPEC-public-lead-intake.md):
Item 1's primitives (_check_public_rate_limit's disjoint-keyspace behavior
and env fallback, _NoLLMCtx.complete() raising, PublicIntakeStartRequest's
validation) and endpoint-level coverage of POST /public/intake (happy-path
repo state, no-auth-required, honeypot no-write, client-supplied-tenant
ignored, 422 validation, the daily cap, and the disjoint per-IP rate limit),
plus Item 2's POST /public/intake/{session_id}/answer: the deterministic
ladder driven end to end, LLM-free completion producing tenant-scoped
pipeline artifacts, contact exclusion from extraction, tenant isolation
(unknown id and real operator-tenant id both 404 identically, no turn
written), the 409 on an already-complete session, validation, and the G4
response-shape guarantee."""
from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from auth.repository import AuthRepository
from kb.repository import KBRepository
from pipeline import _migrate


def _import_symbols():
    from api.main import (
        PublicIntakeStartRequest,
        _NoLLMCtx,
        _check_public_rate_limit,
        _check_rate_limit,
        _public_rate_limit_buckets,
        _rate_limit_buckets,
    )

    return (
        PublicIntakeStartRequest,
        _NoLLMCtx,
        _check_public_rate_limit,
        _check_rate_limit,
        _public_rate_limit_buckets,
        _rate_limit_buckets,
    )


def test_public_rate_limit_disjoint_from_operator_rate_limit(monkeypatch):
    """G5: the two limiters must not be able to consume each other's
    windows. Drive the public limiter to its cap from one host, then confirm
    the operator limiter is still unaffected for the same host, and
    vice versa."""
    (
        _,
        _,
        check_public_rate_limit,
        check_rate_limit,
        public_buckets,
        operator_buckets,
    ) = _import_symbols()

    monkeypatch.setenv("PROCESSFORGE_PUBLIC_RATE_LIMIT_PER_MINUTE", "2")
    monkeypatch.setenv("PROCESSFORGE_RATE_LIMIT_PER_MINUTE", "100")
    public_buckets.clear()
    operator_buckets.clear()

    host = "9.9.9.9"
    check_public_rate_limit(host)
    check_public_rate_limit(host)
    with pytest.raises(HTTPException) as exc_info:
        check_public_rate_limit(host)
    assert exc_info.value.status_code == 429

    # The operator limiter, called from the same host immediately after the
    # public limiter tripped, is unaffected — proves the bucket keyspaces
    # are disjoint, not merely differently-limited.
    check_rate_limit(host)
    assert public_buckets is not operator_buckets
    assert sum(v for k, v in operator_buckets.items() if k[0] == host) == 1


def test_operator_rate_limit_does_not_consume_public_bucket(monkeypatch):
    """Same proof in the other direction: exhausting the operator limiter for
    a host leaves the public limiter's own count, for that same host, at
    zero."""
    (
        _,
        _,
        check_public_rate_limit,
        check_rate_limit,
        public_buckets,
        operator_buckets,
    ) = _import_symbols()

    monkeypatch.setenv("PROCESSFORGE_RATE_LIMIT_PER_MINUTE", "2")
    monkeypatch.setenv("PROCESSFORGE_PUBLIC_RATE_LIMIT_PER_MINUTE", "100")
    public_buckets.clear()
    operator_buckets.clear()

    host = "8.8.4.4"
    check_rate_limit(host)
    check_rate_limit(host)
    with pytest.raises(HTTPException) as exc_info:
        check_rate_limit(host)
    assert exc_info.value.status_code == 429

    # Public limiter for the same host still has full headroom.
    check_public_rate_limit(host)
    assert sum(v for k, v in public_buckets.items() if k[0] == host) == 1


@pytest.mark.parametrize("raw_env_value", ["", "garbage", "0", "-1"])
def test_public_rate_limit_env_fallback_to_default_ten(monkeypatch, raw_env_value):
    """Blank/non-integer/<1 PROCESSFORGE_PUBLIC_RATE_LIMIT_PER_MINUTE all fall
    back to the documented default of 10 — mirror of
    _assert_interview_cap_falls_back_to_default_twelve's fallback style."""
    (
        _,
        _,
        check_public_rate_limit,
        _check_rate_limit,
        public_buckets,
        _operator_buckets,
    ) = _import_symbols()

    if raw_env_value == "":
        monkeypatch.delenv("PROCESSFORGE_PUBLIC_RATE_LIMIT_PER_MINUTE", raising=False)
    else:
        monkeypatch.setenv("PROCESSFORGE_PUBLIC_RATE_LIMIT_PER_MINUTE", raw_env_value)
    public_buckets.clear()

    host = "1.1.1.1"
    for _ in range(10):
        check_public_rate_limit(host)

    with pytest.raises(HTTPException) as exc_info:
        check_public_rate_limit(host)
    assert exc_info.value.status_code == 429


def test_public_rate_limit_prunes_stale_window_entries(monkeypatch):
    """Same stale-window eviction discipline as _check_rate_limit's own
    regression test, applied to the new public bucket dict."""
    import time

    (
        _,
        _,
        check_public_rate_limit,
        _check_rate_limit,
        public_buckets,
        _operator_buckets,
    ) = _import_symbols()

    monkeypatch.delenv("PROCESSFORGE_PUBLIC_RATE_LIMIT_PER_MINUTE", raising=False)
    public_buckets.clear()

    current_window = int(time.time() // 60)
    stale_window = current_window - 100
    public_buckets[("5.6.7.8", stale_window)] = 5

    check_public_rate_limit("5.6.7.8")

    assert ("5.6.7.8", stale_window) not in public_buckets
    assert all(k[1] in (current_window, current_window - 1) for k in public_buckets)


def test_no_llm_ctx_complete_raises():
    """_NoLLMCtx is the primitive that forces interviewer.run()'s LLM-first
    extraction to fall back to the deterministic path (D1/G3) — complete()
    must raise unconditionally, regardless of the repo/session_id it was
    constructed with."""
    _PublicIntakeStartRequest, _NoLLMCtx, *_rest = _import_symbols()

    ctx = _NoLLMCtx(repo=None, session_id="")

    with pytest.raises(RuntimeError):
        ctx.complete(messages=[], tier="extract")


def test_public_intake_start_request_happy_path_strips_whitespace():
    (PublicIntakeStartRequest, *_rest) = _import_symbols()

    body = PublicIntakeStartRequest(
        business_name="  Acme Co  ", contact="  someone@example.com  "
    )

    assert body.business_name == "Acme Co"
    assert body.contact == "someone@example.com"
    assert body.website == ""


@pytest.mark.parametrize("field", ["business_name", "contact"])
def test_public_intake_start_request_blank_field_rejected(field):
    (PublicIntakeStartRequest, *_rest) = _import_symbols()

    payload = {"business_name": "Acme Co", "contact": "someone@example.com"}
    payload[field] = "   "

    with pytest.raises(ValidationError):
        PublicIntakeStartRequest(**payload)


@pytest.mark.parametrize("field", ["business_name", "contact"])
def test_public_intake_start_request_over_max_length_rejected(field):
    (PublicIntakeStartRequest, *_rest) = _import_symbols()

    payload = {"business_name": "Acme Co", "contact": "someone@example.com"}
    payload[field] = "x" * 501

    with pytest.raises(ValidationError):
        PublicIntakeStartRequest(**payload)


def test_public_intake_start_request_honeypot_defaults_empty_and_accepts_value():
    (PublicIntakeStartRequest, *_rest) = _import_symbols()

    default_body = PublicIntakeStartRequest(
        business_name="Acme Co", contact="someone@example.com"
    )
    assert default_body.website == ""

    filled_body = PublicIntakeStartRequest(
        business_name="Acme Co", contact="someone@example.com", website="http://bot.example"
    )
    assert filled_body.website == "http://bot.example"


# --- Endpoint-level tests: POST /public/intake (Item 1e, wired this cycle) ---

_PUBLIC_TENANT = "public-leads"


def _client() -> TestClient:
    from api.main import app

    return TestClient(app)


def _set_env(monkeypatch, tmp_path) -> str:
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("PROCESSFORGE_DB_PATH", db_path)
    return db_path


@pytest.fixture(autouse=True)
def _reset_public_and_operator_rate_limit_buckets():
    """This file's own autouse fixture: tests/conftest.py's autouse fixture
    only clears the operator bucket dict (_rate_limit_buckets), not the new
    public one — clear both here so the endpoint-level tests below (which
    reuse the same TestClient client_host across many requests within a
    test, and across tests in this session) never see cross-test bleed."""
    from api.main import _public_rate_limit_buckets, _rate_limit_buckets

    _public_rate_limit_buckets.clear()
    _rate_limit_buckets.clear()
    yield


def test_start_public_intake_happy_path_repo_state(monkeypatch, tmp_path):
    from api.main import _INTERVIEW_OPENER, _PUBLIC_CONTACT_QUESTION

    db_path = _set_env(monkeypatch, tmp_path)
    client = _client()

    response = client.post(
        "/public/intake",
        json={"business_name": "  Acme Leads Co  ", "contact": "  lead@example.com  "},
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"session_id", "question"}
    assert body["question"] == _INTERVIEW_OPENER
    assert "business_id" not in body

    repo = KBRepository(db_path)
    try:
        businesses = repo.list_businesses(_PUBLIC_TENANT)
        assert len(businesses) == 1
        business = businesses[0]
        assert business["name"] == "Acme Leads Co"
        assert set(business["meta"].keys()) == {"source", "submitted_at", "contact"}
        assert business["meta"]["source"] == "public_intake"
        assert business["meta"]["contact"] == "lead@example.com"

        session_row = repo.get("sessions", body["session_id"], _PUBLIC_TENANT)
        assert session_row is not None
        assert session_row["status"] == "active"
        assert session_row["business_id"] == business["id"]

        turns = repo.list_turns(body["session_id"])
        assert len(turns) == 3
        assert turns[0]["role"] == "question"
        assert turns[0]["content"] == _PUBLIC_CONTACT_QUESTION
        assert turns[1]["role"] == "answer"
        assert turns[1]["content"] == "lead@example.com"
        assert turns[2]["role"] == "question"
        assert turns[2]["content"] == _INTERVIEW_OPENER
    finally:
        repo.close()


def test_start_public_intake_no_auth_required_garbage_header_ignored(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path)
    client = _client()

    response = client.post(
        "/public/intake",
        headers={"Authorization": "Bearer nonsense"},
        json={"business_name": "Acme Co", "contact": "someone@example.com"},
    )

    assert response.status_code == 200
    assert response.json()["question"]


def test_start_public_intake_honeypot_writes_nothing(monkeypatch, tmp_path):
    from api.main import _INTERVIEW_OPENER

    db_path = _set_env(monkeypatch, tmp_path)
    client = _client()

    response = client.post(
        "/public/intake",
        json={
            "business_name": "Acme Co",
            "contact": "someone@example.com",
            "website": "http://bot.example",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"session_id", "question"}
    assert body["question"] == _INTERVIEW_OPENER

    # Honeypot is checked BEFORE any repo is opened — the db file must not
    # even have been migrated/created, let alone had a row written to it.
    assert not os.path.exists(db_path)


def test_start_public_intake_client_supplied_tenant_ignored(monkeypatch, tmp_path):
    db_path = _set_env(monkeypatch, tmp_path)
    client = _client()

    response = client.post(
        "/public/intake",
        json={
            "business_name": "Acme Co",
            "contact": "someone@example.com",
            "tenant": "acme-real-tenant",
        },
    )

    assert response.status_code == 200
    repo = KBRepository(db_path)
    try:
        assert repo.list_businesses("acme-real-tenant") == []
        assert len(repo.list_businesses(_PUBLIC_TENANT)) == 1
    finally:
        repo.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("business_name", "   "),
        ("contact", "   "),
        ("business_name", "x" * 501),
        ("contact", "x" * 501),
    ],
)
def test_start_public_intake_validation_422_nothing_persisted(monkeypatch, tmp_path, field, value):
    db_path = _set_env(monkeypatch, tmp_path)
    client = _client()
    payload = {"business_name": "Acme Co", "contact": "someone@example.com"}
    payload[field] = value

    response = client.post("/public/intake", json=payload)

    assert response.status_code == 422
    # Validation runs before any repo access — nothing was ever migrated/opened.
    assert not os.path.exists(db_path)


def test_start_public_intake_daily_cap_enforced(monkeypatch, tmp_path):
    db_path = _set_env(monkeypatch, tmp_path)
    monkeypatch.setenv("PROCESSFORGE_PUBLIC_MAX_LEADS_PER_DAY", "2")
    client = _client()

    first = client.post(
        "/public/intake", json={"business_name": "Lead One", "contact": "a@example.com"}
    )
    second = client.post(
        "/public/intake", json={"business_name": "Lead Two", "contact": "b@example.com"}
    )
    assert first.status_code == 200
    assert second.status_code == 200

    third = client.post(
        "/public/intake", json={"business_name": "Lead Three", "contact": "c@example.com"}
    )
    assert third.status_code == 429

    repo = KBRepository(db_path)
    try:
        assert len(repo.list_businesses(_PUBLIC_TENANT)) == 2
    finally:
        repo.close()


def test_start_public_intake_daily_cap_ignores_prior_day_submissions(monkeypatch, tmp_path):
    db_path = _set_env(monkeypatch, tmp_path)
    monkeypatch.setenv("PROCESSFORGE_PUBLIC_MAX_LEADS_PER_DAY", "1")
    client = _client()

    _migrate(db_path)
    repo = KBRepository(db_path)
    try:
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        repo.put(
            "businesses",
            {
                "id": str(uuid.uuid4()),
                "schema_version": 1,
                "tenant": _PUBLIC_TENANT,
                "name": "Old Lead",
                "meta": {
                    "source": "public_intake",
                    "submitted_at": yesterday,
                    "contact": "old@example.com",
                },
            },
        )
    finally:
        repo.close()

    # Cap is 1/day; the only existing row is dated yesterday (UTC), so today's
    # count is still 0 and this submission must succeed.
    response = client.post(
        "/public/intake", json={"business_name": "New Lead", "contact": "new@example.com"}
    )

    assert response.status_code == 200
    repo = KBRepository(db_path)
    try:
        assert len(repo.list_businesses(_PUBLIC_TENANT)) == 2
    finally:
        repo.close()


@pytest.mark.parametrize("raw_env_value", ["", "garbage", "0", "-1"])
def test_public_daily_cap_env_fallback_to_default_twenty(monkeypatch, raw_env_value):
    """Blank/non-integer/<1 PROCESSFORGE_PUBLIC_MAX_LEADS_PER_DAY all fall
    back to the documented default of 20 — mirror of
    _assert_interview_cap_falls_back_to_default_twelve's fallback style."""
    from api.main import _public_daily_cap

    if raw_env_value == "":
        monkeypatch.delenv("PROCESSFORGE_PUBLIC_MAX_LEADS_PER_DAY", raising=False)
    else:
        monkeypatch.setenv("PROCESSFORGE_PUBLIC_MAX_LEADS_PER_DAY", raw_env_value)

    assert _public_daily_cap() == 20


def test_start_public_intake_rate_limit_enforced_at_configured_threshold(monkeypatch, tmp_path):
    db_path = _set_env(monkeypatch, tmp_path)
    monkeypatch.setenv("PROCESSFORGE_PUBLIC_RATE_LIMIT_PER_MINUTE", "2")
    client = _client()

    first = client.post(
        "/public/intake", json={"business_name": "Lead One", "contact": "a@example.com"}
    )
    second = client.post(
        "/public/intake", json={"business_name": "Lead Two", "contact": "b@example.com"}
    )
    assert first.status_code == 200
    assert second.status_code == 200

    third = client.post(
        "/public/intake", json={"business_name": "Lead Three", "contact": "c@example.com"}
    )
    assert third.status_code == 429

    repo = KBRepository(db_path)
    try:
        assert len(repo.list_businesses(_PUBLIC_TENANT)) == 2
    finally:
        repo.close()


def test_start_public_intake_rate_limit_disjoint_from_operator_endpoint(monkeypatch, tmp_path):
    """G5, proven end-to-end at the wired route: exhausting the public
    limiter (cap 1) for a host must not affect the separate operator limiter
    (also cap 1) for the same host — the very next operator-endpoint request
    still gets its own full quota and fails on auth (401), not on rate limit
    (429), which is what it would get if the buckets were shared."""
    _set_env(monkeypatch, tmp_path)
    monkeypatch.setenv("PROCESSFORGE_PUBLIC_RATE_LIMIT_PER_MINUTE", "1")
    monkeypatch.setenv("PROCESSFORGE_RATE_LIMIT_PER_MINUTE", "1")
    client = _client()

    ok = client.post(
        "/public/intake", json={"business_name": "Lead One", "contact": "a@example.com"}
    )
    assert ok.status_code == 200
    tripped = client.post(
        "/public/intake", json={"business_name": "Lead Two", "contact": "b@example.com"}
    )
    assert tripped.status_code == 429

    operator_response = client.post(
        "/sessions",
        json={"business_name": "Real Co", "tenant": "acme", "answers": ["x"]},
    )
    assert operator_response.status_code == 401


# --- Item 2: POST /public/intake/{session_id}/answer ---


def _seed_operator(db_path, username="alice", password="correct-horse-battery"):
    """Migrate the schema and create an operator directly via AuthRepository,
    mirroring tests/test_api.py's own seeding style — needed here only for
    the isolation tests that must reach a real, authenticated operator-tenant
    session/endpoint."""
    _migrate(db_path)
    repo = AuthRepository(db_path)
    try:
        repo.create_operator(username, password)
    finally:
        repo.close()


def _login_token(client, db_path, username="alice", password="correct-horse-battery"):
    _seed_operator(db_path, username=username, password=password)
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return response.json()["token"]


def _start_operator_interview(client, token, tenant="acme", business_name="Real Co"):
    response = client.post(
        "/interviews",
        headers={"Authorization": f"Bearer {token}"},
        json={"business_name": business_name, "tenant": tenant},
    )
    assert response.status_code == 200
    return response.json()


def _start_public(client, business_name="Acme Leads Co", contact="lead@example.com"):
    response = client.post(
        "/public/intake", json={"business_name": business_name, "contact": contact}
    )
    assert response.status_code == 200
    return response.json()


def _answer_public(client, session_id, answer):
    return client.post(f"/public/intake/{session_id}/answer", json={"answer": answer})


def _drive_public_to_completion(client, session_id):
    """Submits the 6 answers a public prospect gives after the opening
    question (the 1st answers the opener; the 2nd-6th answer each ladder
    question in turn) and returns the 6 responses in order. Response 6 is
    always the completion body per Item 2's design."""
    contents = [
        "answering the opener",
        "about 2 hours, once a week",
        "a clean automated report",
        "a shared drive folder",
        "only rows marked open",
        "an Excel file",
    ]
    return [_answer_public(client, session_id, content) for content in contents]


def test_answer_public_intake_full_drive_ladder_then_completion(monkeypatch, tmp_path):
    from api.main import _PUBLIC_THANKS

    from stages.interviewer import _next_question_deterministic

    _set_env(monkeypatch, tmp_path)
    client = _client()
    started = _start_public(client)

    responses = _drive_public_to_completion(client, started["session_id"])

    for i, expected_s in enumerate([1, 2, 3, 4, 5], start=0):
        assert responses[i].status_code == 200
        body = responses[i].json()
        assert set(body.keys()) == {"session_id", "question"}
        assert body["question"] == _next_question_deterministic(expected_s)

    final = responses[5]
    assert final.status_code == 200
    final_body = final.json()
    assert final_body == {"status": "complete", "message": _PUBLIC_THANKS}
    for forbidden in ("task_count", "opportunities", "recommendations", "session_id", "business_id", "id"):
        assert forbidden not in final_body


def test_answer_public_intake_completion_creates_pipeline_artifacts_tenant_scoped(monkeypatch, tmp_path):
    db_path = _set_env(monkeypatch, tmp_path)
    client = _client()
    token = _login_token(client, db_path)
    started = _start_public(client)
    session_id = started["session_id"]

    responses = _drive_public_to_completion(client, session_id)
    assert responses[-1].json()["status"] == "complete"

    # Session is complete and the full transcript is 14 turns (7 Q / 7 A),
    # starting with the contact pair — verified via the existing
    # authenticated transcript endpoint under tenant=public-leads.
    transcript_response = client.get(
        f"/interviews/{session_id}/transcript",
        params={"tenant": _PUBLIC_TENANT},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert transcript_response.status_code == 200
    turns = transcript_response.json()
    assert len(turns) == 14
    assert turns[0]["role"] == "question"
    assert turns[1]["role"] == "answer"
    q_count = sum(1 for t in turns if t["role"] == "question")
    a_count = sum(1 for t in turns if t["role"] == "answer")
    assert q_count == 7
    assert a_count == 7

    repo = KBRepository(db_path)
    try:
        session_row = repo.get("sessions", session_id, _PUBLIC_TENANT)
        assert session_row["status"] == "complete"
        business_id = session_row["business_id"]
    finally:
        repo.close()

    conn = sqlite3.connect(db_path)
    try:
        task_count = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE session_id = ? AND tenant = ?",
            (session_id, _PUBLIC_TENANT),
        ).fetchone()[0]
        graph_count = conn.execute(
            "SELECT COUNT(*) FROM workflow_graphs WHERE session_id = ? AND tenant = ?",
            (session_id, _PUBLIC_TENANT),
        ).fetchone()[0]
        opportunity_count = conn.execute(
            "SELECT COUNT(*) FROM opportunities WHERE tenant = ?", (_PUBLIC_TENANT,)
        ).fetchone()[0]
        recommendation_count = conn.execute(
            "SELECT COUNT(*) FROM recommendations WHERE tenant = ?", (_PUBLIC_TENANT,)
        ).fetchone()[0]
    finally:
        conn.close()

    assert task_count >= 1
    assert graph_count == 1
    assert opportunity_count >= 1
    assert recommendation_count >= 1
    assert business_id  # sanity: session resolved to a real business


def test_answer_public_intake_contact_excluded_from_extraction(monkeypatch, tmp_path):
    db_path = _set_env(monkeypatch, tmp_path)
    client = _client()
    marker = "UNIQUE-CONTACT-MARKER test@example.com"
    started = _start_public(client, contact=marker)
    session_id = started["session_id"]

    responses = _drive_public_to_completion(client, session_id)
    assert responses[-1].json()["status"] == "complete"

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT task, frequency, time_spent_min, desired_outcome, tools_used, dependencies "
            "FROM tasks WHERE session_id = ? AND tenant = ?",
            (session_id, _PUBLIC_TENANT),
        ).fetchall()
    finally:
        conn.close()

    assert rows
    for row in rows:
        for field in row:
            assert marker not in str(field)


def test_answer_public_intake_unknown_session_id_404(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path)
    client = _client()

    response = _answer_public(client, str(uuid.uuid4()), "some answer")

    assert response.status_code == 404
    assert response.json() == {"detail": "not found"}


def test_answer_public_intake_operator_tenant_session_gets_identical_404_and_no_turn_written(
    monkeypatch, tmp_path
):
    db_path = _set_env(monkeypatch, tmp_path)
    client = _client()
    token = _login_token(client, db_path)
    real_session = _start_operator_interview(client, token, tenant="acme")

    unknown_response = _answer_public(client, str(uuid.uuid4()), "some answer")
    real_tenant_response = _answer_public(client, real_session["session_id"], "some answer")

    assert real_tenant_response.status_code == unknown_response.status_code == 404
    assert real_tenant_response.json() == unknown_response.json() == {"detail": "not found"}

    repo = KBRepository(db_path)
    try:
        turns = repo.list_turns(real_session["session_id"])
    finally:
        repo.close()
    # Only the single opener question seeded by /interviews itself — the
    # rejected public answer attempt must not have added a turn.
    assert len(turns) == 1
    assert turns[0]["role"] == "question"


def test_answer_public_intake_completed_session_409_no_duplicate_artifacts(monkeypatch, tmp_path):
    db_path = _set_env(monkeypatch, tmp_path)
    client = _client()
    started = _start_public(client)
    session_id = started["session_id"]
    responses = _drive_public_to_completion(client, session_id)
    assert responses[-1].json()["status"] == "complete"

    conn = sqlite3.connect(db_path)
    try:
        before = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE session_id = ? AND tenant = ?",
            (session_id, _PUBLIC_TENANT),
        ).fetchone()[0]
    finally:
        conn.close()

    extra = _answer_public(client, session_id, "one more answer after completion")

    assert extra.status_code == 409
    assert extra.json() == {"detail": "interview already complete"}

    conn = sqlite3.connect(db_path)
    try:
        after = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE session_id = ? AND tenant = ?",
            (session_id, _PUBLIC_TENANT),
        ).fetchone()[0]
    finally:
        conn.close()

    assert after == before


@pytest.mark.parametrize("value", ["   ", "x" * 4001])
def test_answer_public_intake_validation_422_no_turn_written(monkeypatch, tmp_path, value):
    db_path = _set_env(monkeypatch, tmp_path)
    client = _client()
    started = _start_public(client)
    session_id = started["session_id"]

    response = client.post(
        f"/public/intake/{session_id}/answer",
        headers={"Authorization": "Bearer nonsense"},
        json={"answer": value},
    )

    assert response.status_code == 422

    repo = KBRepository(db_path)
    try:
        turns = repo.list_turns(session_id)
    finally:
        repo.close()
    # Only the 3 turns start_public_intake seeded — nothing added by the
    # rejected request.
    assert len(turns) == 3


def test_answer_public_intake_garbage_auth_header_ignored(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path)
    client = _client()
    started = _start_public(client)

    response = client.post(
        f"/public/intake/{started['session_id']}/answer",
        headers={"Authorization": "Bearer nonsense"},
        json={"answer": "a perfectly fine answer"},
    )

    assert response.status_code == 200
    assert response.json()["question"]


def test_answer_public_intake_no_response_leaks_ids_tenant_or_business_id(monkeypatch, tmp_path):
    """G4, across every branch this endpoint can take: question-branch
    responses carry only session_id/question, and the completion response
    carries neither (checked in the full-drive test above). No response body
    here may ever contain the word "tenant", a business_id key, or any id
    besides the session_id the caller already holds."""
    _set_env(monkeypatch, tmp_path)
    client = _client()
    started = _start_public(client)
    session_id = started["session_id"]

    responses = _drive_public_to_completion(client, session_id)

    for response in responses[:5]:
        body = response.json()
        assert set(body.keys()) == {"session_id", "question"}
        assert body["session_id"] == session_id

    completion_body = responses[5].json()
    assert set(completion_body.keys()) == {"status", "message"}


def test_answer_public_intake_rate_limit_is_first_statement(monkeypatch, tmp_path):
    """A tripped public rate limit blocks the answer endpoint too — proves
    _check_public_rate_limit (not _check_rate_limit) gates this handler and
    runs before any repo access (an unknown session id would otherwise also
    404, so a 429 here is the only way to prove the rate limiter, not the
    session lookup, fired first)."""
    _set_env(monkeypatch, tmp_path)
    monkeypatch.setenv("PROCESSFORGE_PUBLIC_RATE_LIMIT_PER_MINUTE", "1")
    client = _client()
    started = _start_public(client)

    # The start call above already consumed the window's one slot.
    response = _answer_public(client, started["session_id"], "some answer")

    assert response.status_code == 429
