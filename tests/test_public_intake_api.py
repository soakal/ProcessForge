"""Item 1 primitives for the public lead intake feature
(docs/FEATURE-SPEC-public-lead-intake.md): _check_public_rate_limit's
disjoint-keyspace behavior and env fallback, _NoLLMCtx.complete() raising,
and PublicIntakeStartRequest's validation. Deliberately does NOT exercise
POST /public/intake — that route is not wired until Item 2's cycle; these
tests call the new symbols directly, mirroring
test_check_rate_limit_prunes_stale_window_entries's direct-call style."""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic import ValidationError


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
