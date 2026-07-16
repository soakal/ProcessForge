"""FastAPI app for ProcessForge.

Auth stopgap (§9 pattern, matches llm/client.py): this is single-tenant-per-deployment
auth. A single shared bearer token (PROCESSFORGE_API_TOKEN) gates all callers of this
deployment; there is no per-tenant credential yet, even though requests carry a
`tenant` field. Replace with real per-tenant auth before this is exposed beyond a
trusted, single-tenant deployment.
"""
from __future__ import annotations
import hmac
import os
import time
from collections import defaultdict

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from contracts.records import ApprovalState
from pipeline import run_session

load_dotenv()

app = FastAPI()

_DEFAULT_RATE_LIMIT_PER_MINUTE = 30
_rate_limit_buckets: dict[tuple[str, int], int] = defaultdict(int)


class SessionRequest(BaseModel):
    business_name: str
    tenant: str
    answers: list[str]


class OpportunityOut(BaseModel):
    id: str
    task_ids: list[str]
    roi_low_hrs: float
    roi_high_hrs: float
    assumptions: list[str]
    complexity: int
    confidence: float
    crosscheck_flags: list[str]


class RecommendationOut(BaseModel):
    id: str
    opportunity_id: str
    summary: str
    approval_state: ApprovalState


class SessionResponse(BaseModel):
    business_id: str
    session_id: str
    task_count: int
    opportunities: list[OpportunityOut]
    recommendations: list[RecommendationOut]


def _check_rate_limit(client_host: str) -> None:
    raw_limit = os.environ.get("PROCESSFORGE_RATE_LIMIT_PER_MINUTE", "")
    try:
        # Env var may be set but empty (e.g. .env.example copied verbatim).
        limit = int(raw_limit) if raw_limit.strip() else _DEFAULT_RATE_LIMIT_PER_MINUTE
    except ValueError:
        limit = _DEFAULT_RATE_LIMIT_PER_MINUTE
    window = int(time.time() // 60)
    # Prune stale windows so the bucket dict doesn't grow without bound on a
    # long-running server; only the current and immediately-prior window can
    # still be relevant to the fixed-window scheme used here.
    for stale_key in [k for k in _rate_limit_buckets if k[1] not in (window, window - 1)]:
        del _rate_limit_buckets[stale_key]
    key = (client_host, window)
    _rate_limit_buckets[key] += 1
    if _rate_limit_buckets[key] > limit:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/sessions", response_model=SessionResponse)
def create_session(
    body: SessionRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> SessionResponse:
    # Rate-limit before auth so failed-auth (e.g. token brute-force) requests
    # count against the per-IP limit too, not just successful ones.
    client_host = request.client.host if request.client else "unknown"
    _check_rate_limit(client_host)

    expected_token = os.environ.get("PROCESSFORGE_API_TOKEN", "")
    provided_token = ""
    if authorization and authorization.startswith("Bearer "):
        provided_token = authorization[len("Bearer "):]
    # Encode to bytes first: hmac.compare_digest raises TypeError on str inputs
    # containing non-ASCII characters.
    if not expected_token or not hmac.compare_digest(
        provided_token.encode("utf-8", "surrogateescape"),
        expected_token.encode("utf-8", "surrogateescape"),
    ):
        raise HTTPException(status_code=401, detail="Not authenticated")

    db_path = os.environ.get("PROCESSFORGE_DB_PATH", "./kb/processforge.db")
    result = run_session(body.business_name, body.tenant, body.answers, db_path)

    return SessionResponse(
        business_id=result.business.id,
        session_id=result.session.id,
        task_count=len(result.tasks),
        opportunities=[OpportunityOut(**o.model_dump()) for o in result.opportunities],
        recommendations=[RecommendationOut(**r.model_dump()) for r in result.recommendations],
    )
