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
import uuid
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator

from auth.hashing import hash_password, verify_password
from auth.repository import AuthRepository
from contracts.records import (
    ApprovalState,
    Automation,
    Business,
    Opportunity,
    Recommendation,
    Session,
    SessionStatus,
    Task,
)
from kb.repository import KBRepository
from pipeline import _Ctx, _finish_pipeline, _migrate, run_session
from sinks.kb_sink import KBSink
from stages import builder, interviewer, qa

load_dotenv()

app = FastAPI()

# api/main.py lives in api/; web/ is a sibling of api/ at the repo root.
_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
app.mount("/ui/static", StaticFiles(directory=_WEB_DIR / "static"), name="static")
templates = Jinja2Templates(directory=_WEB_DIR / "templates")

_DEFAULT_RATE_LIMIT_PER_MINUTE = 30
_rate_limit_buckets: dict[tuple[str, int], int] = defaultdict(int)
# A real, correctly-formatted (but unusable) password hash. Verified against on
# every /auth/login call for an unknown username so that a real PBKDF2
# computation runs either way — this blunts a timing side-channel that could
# otherwise reveal whether a username exists from response latency alone.
_DUMMY_PASSWORD_HASH = hash_password("dummy-placeholder-value")

# Opening question asked by POST /interviews, before any answer has been given.
_INTERVIEW_OPENER = (
    "What task would you like ProcessForge to help you think about automating? "
    "Describe it in your own words."
)
# Hard cap on how many answers a multi-turn interview will collect before it is
# forced to completion, even if stages.interviewer.next_question keeps asking
# for more — this bounds the interview to a finite number of turns.
_MAX_INTERVIEW_ANSWERS = 6


class SessionRequest(BaseModel):
    business_name: str
    tenant: str
    answers: list[str]


class StartInterviewRequest(BaseModel):
    business_name: str
    tenant: str


class AnswerRequest(BaseModel):
    answer: str


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
    # Not part of the frozen Recommendation contract (contracts/records.py) —
    # additive to this API response shape only. Resolved server-side, via
    # _resolve_session_id(), in both get_recommendation and
    # approve_recommendation, using the same tenant-scoped Opportunity ->
    # Task lookup build_automation also uses; stays None whenever no
    # Opportunity/Task can be resolved, never errors.
    session_id: str | None = None
    # Also not part of the frozen Recommendation contract — additive to this
    # API response shape only. Resolved server-side via _resolve_roi(), in
    # both get_recommendation and approve_recommendation, using the same
    # tenant-scoped Opportunity lookup as session_id above; stays None
    # whenever no Opportunity can be resolved, never errors.
    roi_low_hrs: float | None = None
    roi_high_hrs: float | None = None


class TurnOut(BaseModel):
    turn_index: int
    role: str
    content: str


class AutomationOut(BaseModel):
    id: str
    recommendation_id: str
    spec: dict
    blast_radius: str
    rollback: str
    approval_state: ApprovalState


class FeedbackRequest(BaseModel):
    feedback: str


class LinkRequest(BaseModel):
    # This value will become a clickable href in a future cycle, so validation
    # uses a scheme ALLOW-list (http/https only), never a blocklist — rejects
    # javascript:/file:/data:/any other scheme as well as malformed URLs.
    # max_length bounds mirror the DoS defense-in-depth pattern established
    # for RefineRequest.turns (bounded input against unbounded storage/render
    # cost) rather than any functional requirement of a real product URL/note.
    product_url: str = Field(max_length=2048)
    product_notes: str | None = Field(default=None, max_length=4000)

    @field_validator("product_url")
    @classmethod
    def _require_http_scheme(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
            raise ValueError("product_url must be a well-formed http:// or https:// URL")
        # Reject embedded userinfo (e.g. http://trusted.com@evil.com/ or
        # http://user:pass@evil.com) — structurally a valid http(s) URL with
        # a non-empty netloc, so the checks above alone would accept it, but
        # it's a classic URL-spoofing pattern where the pre-'@' text reads as
        # a trusted hostname while the browser actually navigates to
        # whatever follows '@'. Rejecting it now costs nothing and matters
        # precisely because this value is destined to become a clickable
        # href a person will trust in a future cycle.
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("product_url must not contain embedded userinfo (user:pass@ or user@)")
        return value


class RefineTurn(BaseModel):
    question: str
    answer: str


class RefineRequest(BaseModel):
    # Bounded defense-in-depth against a single request driving an unbounded
    # number of repo.add_turn() calls (each does an O(n) COUNT(*) over
    # session_turns, so an unbounded list is a self-inflicted quadratic-cost
    # DoS vector for an authenticated caller). This is unrelated to
    # _MAX_INTERVIEW_ANSWERS, which gates a different flow's completion logic,
    # not raw request size — refine still accepts as many turns as a normal
    # follow-up would ever need.
    turns: list[RefineTurn] = Field(default_factory=list, max_length=50)


class DeleteBusinessRequest(BaseModel):
    confirm_business_id: str


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


@app.get("/ui/login")
def ui_login(request: Request):
    # No auth required — this IS the login page.
    return templates.TemplateResponse(request, "login.html")


@app.get("/ui")
def ui_dashboard(request: Request):
    # No server-side auth check — requireAuth() in dashboard.html redirects to
    # /ui/login client-side if there's no token, same as the rest of /ui.
    return templates.TemplateResponse(request, "dashboard.html")


@app.get("/ui/interview")
def ui_interview(request: Request):
    # No server-side auth check — requireAuth() in interview.html redirects to
    # /ui/login client-side if there's no token, same as the rest of /ui.
    return templates.TemplateResponse(request, "interview.html")


@app.get("/ui/recommendations/{recommendation_id}")
def ui_recommendation(recommendation_id: str, request: Request):
    # No server-side auth check — requireAuth() in recommendations.html
    # redirects to /ui/login client-side if there's no token, same as the
    # rest of /ui. recommendation_id is passed into the template so the
    # inline script can embed it as a JS constant without re-parsing the URL.
    return templates.TemplateResponse(
        request, "recommendations.html", {"recommendation_id": recommendation_id}
    )


@app.get("/ui/interview/{session_id}/transcript")
def ui_interview_transcript(session_id: str, request: Request):
    # No server-side auth check — requireAuth() in transcript.html redirects
    # to /ui/login client-side if there's no token, same as the rest of /ui.
    # session_id is passed into the template so the inline script can embed
    # it as a JS constant without re-parsing the URL, matching
    # ui_recommendation's pattern.
    return templates.TemplateResponse(
        request, "transcript.html", {"session_id": session_id}
    )


@app.get("/ui/audit-log")
def ui_audit_log(request: Request):
    # No server-side auth check — requireAuth() in audit-log.html redirects to
    # /ui/login client-side if there's no token, same as the rest of /ui.
    return templates.TemplateResponse(request, "audit-log.html")


@app.get("/ui/businesses/delete")
def ui_businesses_delete(request: Request):
    # No server-side auth check — requireAuth() in businesses_delete.html
    # redirects to /ui/login client-side if there's no token, same as the
    # rest of /ui.
    return templates.TemplateResponse(request, "businesses_delete.html")


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


@app.post("/interviews")
def start_interview(
    body: StartInterviewRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict:
    # Rate-limit before auth so failed-auth (e.g. token brute-force) requests
    # count against the per-IP limit too, not just successful ones.
    client_host = request.client.host if request.client else "unknown"
    _check_rate_limit(client_host)

    db_path = os.environ.get("PROCESSFORGE_DB_PATH", "./kb/processforge.db")
    _authenticate(authorization, db_path)
    repo, ctx = _open_repo(db_path)
    try:
        sink = KBSink()
        business = Business(id=str(uuid.uuid4()), tenant=body.tenant, name=body.business_name)
        sink.save(business, ctx)

        # transcript_ref points at the session's own id (turns are stored keyed
        # by session_id via repo.add_turn/list_turns, not a separate blob).
        session_id = str(uuid.uuid4())
        session = Session(
            id=session_id,
            business_id=business.id,
            status=SessionStatus.active,
            transcript_ref=session_id,
        )
        sink.save(session, ctx)

        repo.add_turn(session.id, "question", _INTERVIEW_OPENER)

        return {
            "business_id": business.id,
            "session_id": session.id,
            "question": _INTERVIEW_OPENER,
        }
    finally:
        repo.close()


@app.post("/interviews/{session_id}/answer")
def answer_interview(
    session_id: str,
    tenant: str,
    body: AnswerRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict:
    # Rate-limit before auth so failed-auth (e.g. token brute-force) requests
    # count against the per-IP limit too, not just successful ones.
    client_host = request.client.host if request.client else "unknown"
    _check_rate_limit(client_host)

    db_path = os.environ.get("PROCESSFORGE_DB_PATH", "./kb/processforge.db")
    _authenticate(authorization, db_path)
    repo, _ctx = _open_repo(db_path)
    try:
        session_row = repo.get("sessions", session_id, tenant)
        if session_row is None:
            # Same 404 for unknown id and wrong tenant — don't leak which.
            raise HTTPException(status_code=404, detail="not found")
        if session_row["status"] != SessionStatus.active.value:
            raise HTTPException(status_code=409, detail="interview already complete")

        repo.add_turn(session_id, "answer", body.answer)
        turns = repo.list_turns(session_id)
        answer_count = sum(1 for turn in turns if turn["role"] == "answer")

        # Critical: this ctx's session_id must be the REAL session id, not the
        # empty one _open_repo's ctx carries — otherwise Task.session_id (set
        # from ctx.session_id by stages.interviewer.run) would persist blank.
        ctx = _Ctx(repo, session_id=session_id)

        if answer_count >= _MAX_INTERVIEW_ANSWERS:
            # Cap hit: force completion, skip asking for another answer.
            question = None
        else:
            question = interviewer.next_question(turns, ctx)

        if question is not None:
            repo.add_turn(session_id, "question", question)
            return {"session_id": session_id, "question": question}

        # Completion path: either next_question decided there's enough
        # information, or the answer cap forced it.
        business_row = repo.get("businesses", session_row["business_id"], tenant)
        business = Business(**business_row)
        session = Session(**session_row)
        session.status = SessionStatus.complete

        sink = KBSink()
        sink.save(session, ctx)

        transcript = "\n".join(turn["content"] for turn in turns if turn["role"] == "answer")
        tasks = interviewer.run(transcript, ctx)
        result = _finish_pipeline(business, session, tasks, repo, sink, ctx)

        return SessionResponse(
            business_id=result.business.id,
            session_id=result.session.id,
            task_count=len(result.tasks),
            opportunities=[OpportunityOut(**o.model_dump()) for o in result.opportunities],
            recommendations=[RecommendationOut(**r.model_dump()) for r in result.recommendations],
        ).model_dump(mode="json")
    finally:
        repo.close()


@app.get("/interviews/{session_id}/transcript", response_model=list[TurnOut])
def get_interview_transcript(
    session_id: str,
    tenant: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> list[TurnOut]:
    # Rate-limit before auth so failed-auth (e.g. token brute-force) requests
    # count against the per-IP limit too, not just successful ones.
    client_host = request.client.host if request.client else "unknown"
    _check_rate_limit(client_host)

    db_path = os.environ.get("PROCESSFORGE_DB_PATH", "./kb/processforge.db")
    _authenticate(authorization, db_path)
    repo, _ctx = _open_repo(db_path)
    try:
        session_row = repo.get("sessions", session_id, tenant)
        if session_row is None:
            # Same 404 for unknown id and wrong tenant — don't leak which. Must
            # be resolved BEFORE list_turns is ever called: list_turns itself
            # is not tenant-scoped (it only filters by session_id), so this
            # check is the only thing preventing cross-tenant transcript reads.
            raise HTTPException(status_code=404, detail="not found")
        turns = repo.list_turns(session_id)
        return [
            TurnOut(turn_index=turn["turn_index"], role=turn["role"], content=turn["content"])
            for turn in turns
        ]
    finally:
        repo.close()


def _resolve_session_id(repo: KBRepository, recommendation: Recommendation, tenant: str) -> str | None:
    """Resolve the interview session_id for a "View interview transcript" link.

    Shared by get_recommendation and approve_recommendation so both responses
    carry the same value. Uses the same tenant-scoped Opportunity -> Task
    lookup build_automation already uses: an unresolvable (missing or
    wrong-tenant) Opportunity, or an Opportunity with no resolvable Tasks, is
    tolerated, not fatal — this returns None, never a different error or a
    tenant-info leak. Both callers already 404 on a missing/wrong-tenant
    recommendation before reaching here, so the tenant scoping below is not
    the caller's only check.
    """
    opportunity_row = repo.get("opportunities", recommendation.opportunity_id, tenant)
    opportunity = Opportunity(**opportunity_row) if opportunity_row is not None else None
    tasks: list[Task] = []
    if opportunity is not None:
        for task_id in opportunity.task_ids:
            task_row = repo.get("tasks", task_id, tenant)
            if task_row is not None:
                tasks.append(Task(**task_row))
    return tasks[0].session_id if tasks else None


def _resolve_roi(repo: KBRepository, recommendation: Recommendation, tenant: str) -> tuple[float | None, float | None]:
    """Resolve (roi_low_hrs, roi_high_hrs) from the Recommendation's Opportunity for display.

    Shared by get_recommendation and approve_recommendation so both responses
    carry the same value. Same tenant-scoped Opportunity lookup as
    _resolve_session_id: an unresolvable (missing or wrong-tenant) Opportunity
    is tolerated, not fatal — this returns (None, None), never a different
    error or a tenant-info leak. Both callers already 404 on a
    missing/wrong-tenant recommendation before reaching here, so the tenant
    scoping below is not the caller's only check.
    """
    opportunity_row = repo.get("opportunities", recommendation.opportunity_id, tenant)
    if opportunity_row is None:
        return None, None
    opportunity = Opportunity(**opportunity_row)
    return opportunity.roi_low_hrs, opportunity.roi_high_hrs


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
        session_id = _resolve_session_id(repo, recommendation, tenant)
        roi_low_hrs, roi_high_hrs = _resolve_roi(repo, recommendation, tenant)
        return RecommendationOut(
            **recommendation.model_dump(),
            session_id=session_id,
            roi_low_hrs=roi_low_hrs,
            roi_high_hrs=roi_high_hrs,
        )
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
    operator = _authenticate(authorization, db_path)
    repo, _ctx = _open_repo(db_path)
    try:
        row = repo.get("recommendations", recommendation_id, tenant)
        if row is None:
            # Same 404 for unknown id and wrong tenant — don't leak which.
            raise HTTPException(status_code=404, detail="not found")
        recommendation = Recommendation(**row)
        old_state = recommendation.approval_state
        recommendation.approval_state = ApprovalState.approved
        repo.put("recommendations", recommendation.model_dump(mode="json"))
        if old_state != ApprovalState.approved:
            # Don't double-log a re-approve of an already-approved recommendation.
            repo.log_approval_change(
                operator_id=operator["id"],
                tenant=tenant,
                record_kind="recommendation",
                record_id=recommendation_id,
                field="approval_state",
                old_value=old_state.value,
                new_value=ApprovalState.approved.value,
            )
        session_id = _resolve_session_id(repo, recommendation, tenant)
        roi_low_hrs, roi_high_hrs = _resolve_roi(repo, recommendation, tenant)
        return RecommendationOut(
            **recommendation.model_dump(),
            session_id=session_id,
            roi_low_hrs=roi_low_hrs,
            roi_high_hrs=roi_high_hrs,
        )
    finally:
        repo.close()


@app.get("/audit-log")
def get_audit_log(
    tenant: str,
    request: Request,
    record_id: str | None = None,
    authorization: str | None = Header(default=None),
) -> list[dict]:
    # Rate-limit before auth so failed-auth (e.g. token brute-force) requests
    # count against the per-IP limit too, not just successful ones.
    client_host = request.client.host if request.client else "unknown"
    _check_rate_limit(client_host)

    db_path = os.environ.get("PROCESSFORGE_DB_PATH", "./kb/processforge.db")
    _authenticate(authorization, db_path)
    repo, _ctx = _open_repo(db_path)
    try:
        return repo.list_audit_log(tenant, record_id)
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

        # Best-effort enrichment for the builder's deterministic handoff: an
        # unresolvable (missing or wrong-tenant) Opportunity is tolerated, not
        # fatal — the recommendation itself already passed its own tenant-scoped
        # 404 check above, so a thin/missing Opportunity just means a thinner
        # handoff, never a different error or a tenant-info leak.
        opportunity_row = repo.get("opportunities", recommendation.opportunity_id, tenant)
        opportunity = Opportunity(**opportunity_row) if opportunity_row is not None else None
        tasks: list[Task] = []
        if opportunity is not None:
            for task_id in opportunity.task_ids:
                task_row = repo.get("tasks", task_id, tenant)
                if task_row is not None:
                    tasks.append(Task(**task_row))

        # Feed the interview Q&A into the builder's handoff too: session_id
        # comes from an already tenant-verified Task fetched above (never
        # attacker-supplied), so calling the non-tenant-scoped list_turns()
        # directly with it is safe — same reasoning already documented for
        # session_turns elsewhere. No turns (or no tasks) just yields an
        # empty transcript, same as the pre-existing 3-tuple call shape.
        session_id = tasks[0].session_id if tasks else None
        turns = repo.list_turns(session_id) if session_id else []

        try:
            automation = builder.run((recommendation, opportunity, tasks, turns), ctx)
        except PermissionError:
            raise HTTPException(
                status_code=409,
                detail="the recommendation must be approved before it can be built",
            )
        repo.put("automations", automation.model_dump(mode="json"))
        return AutomationOut(**automation.model_dump())
    finally:
        repo.close()


@app.post("/recommendations/{recommendation_id}/refine", response_model=AutomationOut)
def refine_recommendation(
    recommendation_id: str,
    tenant: str,
    body: RefineRequest,
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

        # Best-effort enrichment for the builder's deterministic handoff, same
        # tolerant pattern as build_automation: an unresolvable (missing or
        # wrong-tenant) Opportunity just yields a thinner handoff, never a
        # different error or a tenant-info leak.
        opportunity_row = repo.get("opportunities", recommendation.opportunity_id, tenant)
        opportunity = Opportunity(**opportunity_row) if opportunity_row is not None else None
        tasks: list[Task] = []
        if opportunity is not None:
            for task_id in opportunity.task_ids:
                task_row = repo.get("tasks", task_id, tenant)
                if task_row is not None:
                    tasks.append(Task(**task_row))

        # session_id comes from an already tenant-verified Task fetched above
        # (never attacker-supplied), same reasoning already documented for
        # build_automation and the transcript endpoint — safe to pass directly
        # to the non-tenant-scoped add_turn/list_turns below. If no session_id
        # is resolvable (e.g. no tasks) AND the caller submitted no turns, that
        # mirrors the same tolerant "thinner, never different" behavior used
        # for a missing Opportunity above. But if the caller DID submit turns,
        # silently discarding them while still returning a normal 200 would
        # bump the revision without the handoff ever reflecting the new
        # answers — so that combination is a hard error instead.
        session_id = tasks[0].session_id if tasks else None
        if session_id is None and body.turns:
            raise HTTPException(
                status_code=409,
                detail=(
                    "cannot record refine answers: no session is resolvable "
                    "for this recommendation's opportunity/tasks"
                ),
            )
        if session_id is not None:
            # Append the refine request's follow-up Q&A pairs as new
            # session_turns. Deliberately NOT gated by _MAX_INTERVIEW_ANSWERS —
            # that cap only governs the original /interviews/{id}/answer
            # flow; refine is a separate flow with its own turns.
            for turn in body.turns:
                repo.add_turn(session_id, "question", turn.question)
                repo.add_turn(session_id, "answer", turn.answer)
        turns = repo.list_turns(session_id) if session_id else []

        # Prior latest revision across every Automation ever built/refined for
        # this Recommendation, so the refined Automation's revision reflects
        # the new answers while every prior version stays retrievable,
        # unmodified (a fresh UUID id is assigned below via builder.run, so
        # repo.put inserts a new row rather than updating an old one). Reuses
        # stages/qa.py's own `spec.get("revision", 1)` default so an
        # automation that was never revised (its spec has no "revision" key
        # yet, e.g. straight off build_automation) is treated the same way
        # qa.py treats it.
        prior_automations = repo.list_automations_by_recommendation(recommendation_id, tenant)
        prior_revision = max(
            (a["spec"].get("revision", 1) for a in prior_automations), default=0
        )

        try:
            automation = builder.run((recommendation, opportunity, tasks, turns), ctx)
        except PermissionError:
            raise HTTPException(
                status_code=409,
                detail="the recommendation must be approved before it can be refined",
            )
        automation.spec["revision"] = prior_revision + 1
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


@app.post("/automations/{automation_id}/link", response_model=AutomationOut)
def link_automation_product(
    automation_id: str,
    tenant: str,
    body: LinkRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> AutomationOut:
    # Rate-limit before auth so failed-auth (e.g. token brute-force) requests
    # count against the per-IP limit too, not just successful ones.
    client_host = request.client.host if request.client else "unknown"
    _check_rate_limit(client_host)

    db_path = os.environ.get("PROCESSFORGE_DB_PATH", "./kb/processforge.db")
    _authenticate(authorization, db_path)
    repo, _ctx = _open_repo(db_path)
    try:
        row = repo.get("automations", automation_id, tenant)
        if row is None:
            # Same 404 for unknown id and wrong tenant — don't leak which.
            raise HTTPException(status_code=404, detail="not found")
        automation = Automation(**row)
        # product_url/product_notes are pure data stored inside the existing
        # free-form spec: dict JSON blob — no contracts/records.py change,
        # no schema_version bump.
        automation.spec["product_url"] = body.product_url
        automation.spec["product_notes"] = body.product_notes
        repo.put("automations", automation.model_dump(mode="json"))
        return AutomationOut(**automation.model_dump())
    finally:
        repo.close()


@app.post("/businesses/{business_id}/delete")
def delete_business(
    business_id: str,
    tenant: str,
    body: DeleteBusinessRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict:
    # Rate-limit before auth so failed-auth (e.g. token brute-force) requests
    # count against the per-IP limit too, not just successful ones.
    client_host = request.client.host if request.client else "unknown"
    _check_rate_limit(client_host)

    db_path = os.environ.get("PROCESSFORGE_DB_PATH", "./kb/processforge.db")
    _authenticate(authorization, db_path)
    # Confirmation check happens BEFORE any repository is opened: a mismatched
    # confirm_business_id must never be able to reach the DB, even read-only.
    if body.confirm_business_id != business_id:
        raise HTTPException(status_code=400, detail="confirm_business_id does not match business_id")
    repo, _ctx = _open_repo(db_path)
    try:
        result = repo.delete_business(business_id, tenant)
        if result is None:
            # Same 404 for unknown id and wrong tenant — don't leak which.
            raise HTTPException(status_code=404, detail="not found")
        return result
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
