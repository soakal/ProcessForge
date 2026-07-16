"""FastAPI app for ProcessForge.

Auth: real per-operator login. Operator accounts are created out-of-band via
`python -m auth.users create <username>` (see auth/users.py); there is no
self-service signup endpoint. `POST /auth/login` exchanges a username/password
for a bearer token (auth/repository.py's AuthRepository.create_token, with a
7-day TTL); that token is then required, via the `Authorization: Bearer <token>`
header, on every protected endpoint below. `_authenticate()` resolves the token
to an operator on each request, rejecting missing, malformed, unknown, or
expired tokens with the same 401 "invalid credentials" response. Tokens are
still shared across tenants — there is no per-tenant credential yet, even
though requests carry a `tenant` field.
"""
from __future__ import annotations
import os
import time
from collections import defaultdict

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from auth.hashing import hash_password, verify_password
from auth.repository import AuthRepository
from contracts.records import ApprovalState, Automation, Recommendation
from kb.repository import KBRepository
from pipeline import _Ctx, _migrate, run_session
from stages import builder, qa

load_dotenv()

app = FastAPI()

_DEFAULT_RATE_LIMIT_PER_MINUTE = 30
_rate_limit_buckets: dict[tuple[str, int], int] = defaultdict(int)
# A real, correctly-formatted (but unusable) password hash. Verified against on
# every /auth/login call for an unknown username so that a real PBKDF2
# computation runs either way — this blunts a timing side-channel that could
# otherwise reveal whether a username exists from response latency alone.
_DUMMY_PASSWORD_HASH = hash_password("dummy-placeholder-value")


class SessionRequest(BaseModel):
    business_name: str
    tenant: str
    answers: list[str]


class LoginRequest(BaseModel):
    username: str
    password: str


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


class AutomationOut(BaseModel):
    id: str
    recommendation_id: str
    spec: dict
    blast_radius: str
    rollback: str
    approval_state: ApprovalState


class FeedbackRequest(BaseModel):
    feedback: str


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


def _open_repo(db_path: str) -> tuple[KBRepository, _Ctx]:
    """Migrate + open a repo/ctx pair, mirroring pipeline.run_session's setup.
    Callers own cleanup: `repo.close()` in a `finally` block."""
    _migrate(db_path)
    repo = KBRepository(db_path)
    ctx = _Ctx(repo, session_id="")
    return repo, ctx


def _authenticate(authorization: str | None, db_path: str) -> dict:
    """Resolve the bearer token in `authorization` to an operator dict via
    AuthRepository.get_operator_by_token, mirroring /auth/logout's own
    lookup. Raises the same 401 "invalid credentials" used by /auth/login
    for a missing header, a malformed header, an unknown token, or an
    expired one — callers never learn which."""
    provided_token = ""
    if authorization and authorization.startswith("Bearer "):
        provided_token = authorization[len("Bearer "):]

    _migrate(db_path)
    repo = AuthRepository(db_path)
    try:
        operator = repo.get_operator_by_token(provided_token)
    finally:
        repo.close()
    if operator is None:
        raise HTTPException(status_code=401, detail="invalid credentials")
    return operator


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

    db_path = os.environ.get("PROCESSFORGE_DB_PATH", "./kb/processforge.db")
    _authenticate(authorization, db_path)
    result = run_session(body.business_name, body.tenant, body.answers, db_path)

    return SessionResponse(
        business_id=result.business.id,
        session_id=result.session.id,
        task_count=len(result.tasks),
        opportunities=[OpportunityOut(**o.model_dump()) for o in result.opportunities],
        recommendations=[RecommendationOut(**r.model_dump()) for r in result.recommendations],
    )


@app.get("/recommendations/{recommendation_id}", response_model=RecommendationOut)
def get_recommendation(
    recommendation_id: str,
    tenant: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> RecommendationOut:
    # Rate-limit before auth so failed-auth (e.g. token brute-force) requests
    # count against the per-IP limit too, not just successful ones.
    client_host = request.client.host if request.client else "unknown"
    _check_rate_limit(client_host)

    db_path = os.environ.get("PROCESSFORGE_DB_PATH", "./kb/processforge.db")
    _authenticate(authorization, db_path)
    repo, _ctx = _open_repo(db_path)
    try:
        row = repo.get("recommendations", recommendation_id, tenant)
        if row is None:
            # Same 404 for unknown id and wrong tenant — don't leak which.
            raise HTTPException(status_code=404, detail="not found")
        recommendation = Recommendation(**row)
        return RecommendationOut(**recommendation.model_dump())
    finally:
        repo.close()


@app.post("/recommendations/{recommendation_id}/approve", response_model=RecommendationOut)
def approve_recommendation(
    recommendation_id: str,
    tenant: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> RecommendationOut:
    # Rate-limit before auth so failed-auth (e.g. token brute-force) requests
    # count against the per-IP limit too, not just successful ones.
    client_host = request.client.host if request.client else "unknown"
    _check_rate_limit(client_host)

    db_path = os.environ.get("PROCESSFORGE_DB_PATH", "./kb/processforge.db")
    _authenticate(authorization, db_path)
    repo, _ctx = _open_repo(db_path)
    try:
        row = repo.get("recommendations", recommendation_id, tenant)
        if row is None:
            # Same 404 for unknown id and wrong tenant — don't leak which.
            raise HTTPException(status_code=404, detail="not found")
        recommendation = Recommendation(**row)
        recommendation.approval_state = ApprovalState.approved
        repo.put("recommendations", recommendation.model_dump(mode="json"))
        return RecommendationOut(**recommendation.model_dump())
    finally:
        repo.close()


@app.post("/recommendations/{recommendation_id}/build", response_model=AutomationOut)
def build_automation(
    recommendation_id: str,
    tenant: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> AutomationOut:
    # Rate-limit before auth so failed-auth (e.g. token brute-force) requests
    # count against the per-IP limit too, not just successful ones.
    client_host = request.client.host if request.client else "unknown"
    _check_rate_limit(client_host)

    db_path = os.environ.get("PROCESSFORGE_DB_PATH", "./kb/processforge.db")
    _authenticate(authorization, db_path)
    repo, ctx = _open_repo(db_path)
    try:
        row = repo.get("recommendations", recommendation_id, tenant)
        if row is None:
            # Same 404 for unknown id and wrong tenant — don't leak which.
            raise HTTPException(status_code=404, detail="not found")
        recommendation = Recommendation(**row)
        try:
            automation = builder.run(recommendation, ctx)
        except PermissionError:
            raise HTTPException(
                status_code=409,
                detail="the recommendation must be approved before it can be built",
            )
        repo.put("automations", automation.model_dump(mode="json"))
        return AutomationOut(**automation.model_dump())
    finally:
        repo.close()


@app.post("/automations/{automation_id}/feedback", response_model=AutomationOut)
def submit_automation_feedback(
    automation_id: str,
    tenant: str,
    body: FeedbackRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> AutomationOut:
    # Rate-limit before auth so failed-auth (e.g. token brute-force) requests
    # count against the per-IP limit too, not just successful ones.
    client_host = request.client.host if request.client else "unknown"
    _check_rate_limit(client_host)

    db_path = os.environ.get("PROCESSFORGE_DB_PATH", "./kb/processforge.db")
    _authenticate(authorization, db_path)
    repo, ctx = _open_repo(db_path)
    try:
        row = repo.get("automations", automation_id, tenant)
        if row is None:
            # Same 404 for unknown id and wrong tenant — don't leak which.
            raise HTTPException(status_code=404, detail="not found")
        automation = Automation(**row)
        revised = qa.run((automation, body.feedback), ctx)
        repo.put("automations", revised.model_dump(mode="json"))
        return AutomationOut(**revised.model_dump())
    finally:
        repo.close()


@app.post("/auth/login")
def login(body: LoginRequest, request: Request) -> dict:
    # Rate-limit before auth so failed-auth (e.g. credential brute-force) requests
    # count against the per-IP limit too, not just successful ones.
    client_host = request.client.host if request.client else "unknown"
    _check_rate_limit(client_host)

    db_path = os.environ.get("PROCESSFORGE_DB_PATH", "./kb/processforge.db")
    _migrate(db_path)
    repo = AuthRepository(db_path)
    try:
        operator = repo.get_operator(body.username)
        if operator is not None:
            valid = verify_password(body.password, operator["password_hash"])
        else:
            # Unknown username: still run a real password verification against a
            # dummy hash (see _DUMMY_PASSWORD_HASH) so the response takes roughly
            # the same time either way, then fail the same as a wrong password.
            verify_password(body.password, _DUMMY_PASSWORD_HASH)
            valid = False
        if not valid:
            # Identical status/detail for "unknown username" and "wrong password" —
            # don't leak which one it was.
            raise HTTPException(status_code=401, detail="invalid credentials")
        token = repo.create_token(operator["id"])
        return {"token": token}
    finally:
        repo.close()


@app.post("/auth/logout")
def logout(request: Request, authorization: str | None = Header(default=None)) -> dict:
    # Rate-limit before auth so failed-auth (e.g. token brute-force) requests
    # count against the per-IP limit too, not just successful ones.
    client_host = request.client.host if request.client else "unknown"
    _check_rate_limit(client_host)

    provided_token = ""
    if authorization and authorization.startswith("Bearer "):
        provided_token = authorization[len("Bearer "):]

    db_path = os.environ.get("PROCESSFORGE_DB_PATH", "./kb/processforge.db")
    _migrate(db_path)
    repo = AuthRepository(db_path)
    try:
        operator = repo.get_operator_by_token(provided_token)
        if operator is None:
            raise HTTPException(status_code=401, detail="invalid credentials")
        repo.delete_token(provided_token)
        return {"status": "logged out"}
    finally:
        repo.close()
