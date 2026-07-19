# ProcessForge: Public Lead Intake (Unauthenticated Prospect Interview) — Implementation Spec

**Planned by Fable, 2026-07-19.** Follows the conventions established in `docs/FEATURE-SPEC-dashboard-and-users.md`, `docs/FEATURE-SPEC-mobile-friendly.md`, and `docs/FEATURE-SPEC-existing-business-interviews.md`. Council-loop's Arbiter decomposes into cycles — one item ≈ one cycle.

**What this is:** a public link (no login) Brian can send a prospective client, where they describe a manual process they want automated. The submission lands as a normal Business + Session (+ deterministic Tasks/Opportunity/Recommendation) inside the existing KB, reviewable on the existing dashboard/businesses/transcript pages. **This is the app's first unauthenticated write-capable surface — the security posture below assumes the endpoint is internet-facing from day one**, even though the actual Tailscale Funnel exposure is a separate, later, Brian-only step (explicitly out of scope, Part E).

---

## Part A — Investigation findings (all verified against actual code)

### A1. Current auth surface (confirmed)

- Every real endpoint in `api/main.py` calls `_authenticate()` (bearer token → `AuthRepository.get_operator_by_token`, uniform 401). The only server-side-unauthenticated routes today are `GET /health` and the `/ui/*` template routes (which are shells — their data calls all require the token client-side via `requireAuth()`/`fetchWithAuth`). There is no public write path anywhere.
- Rate limiting: `_check_rate_limit(client_host)` — fixed one-minute window, per-IP, module-level `_rate_limit_buckets` dict with stale-window pruning, env `PROCESSFORGE_RATE_LIMIT_PER_MINUTE` read fresh per call with defensive parsing (blank/non-int → default 30). This convention (fresh env read, defensive parse) is the template for any new limit.

### A2. The interview machinery is reusable without the LLM (confirmed — this is the load-bearing finding)

- `stages/interviewer.py::_next_question_deterministic(answer_count)` is a pure, importable function: 1→time/frequency, 2→desired outcome, 3→input-file location, 4→filter rules, 5→output format, ≥6→`None`. Calling it **directly** (instead of `next_question()`) never touches `ctx.complete` — a public flow using it makes **zero LLM calls while asking questions**, even on the deployed box where OpenRouter is live.
- The completion path is the hidden LLM surface: `interviewer.run(transcript, ctx)` is LLM-first (`_extract_llm` → `ctx.complete` → `llm.client.complete` → a real paid OpenRouter call on the deployed copy), falling back to `_extract_deterministic` on ANY exception. `mapper`/`analyzer`/`architect` are deterministic and never call `complete()`. So a completion that must be LLM-free needs a ctx whose `complete()` raises — the fallback then runs by construction. `pipeline._Ctx` is a 6-line class; a subclass overriding `complete()` to raise is trivial and lives in `api/main.py` (no `pipeline.py` change).
- `pipeline._finish_pipeline(business, session, tasks, repo, sink, ctx)` is the reusable completion seam `answer_interview` already uses — the public flow reuses it identically, so a public lead produces the **same record shapes** (Tasks → WorkflowGraph → Opportunity → Recommendation) the operator UI already renders.
- `repo.add_turn`/`list_turns` are NOT tenant-scoped (documented pattern: caller must resolve the session tenant-scoped first). Every public handler must do the tenant-scoped session/business `repo.get(...)` **before** any turn read/write — same rule `answer_interview` and the transcript endpoint already follow.

### A3. Data model: zero contract change needed (confirmed)

- `Business.meta: dict` is already in the frozen contract (`contracts/records.py`), stored as a JSON column, free-form. Provenance (`source`, `submitted_at`, `contact`) fits there with **no `contracts/records.py` change and no `schema_version` bump** — same "free-form dict, pure data" precedent as `automation.spec["product_url"]`.
- `BusinessOut` deliberately excludes `meta` ("never serialize meta to this list endpoint") — an existing security posture this spec must not weaken. Consequence: contact info stored only in `meta` would be invisible in the UI; see D4 for the resolution.
- No `kb/repository.py` change and no migration are needed at all: `sink.save` (Business→Session ordering satisfies `_resolve_tenant`), `add_turn`, `list_turns`, `list_businesses` (returns deserialized `meta`, used for the daily cap) all exist.

### A4. Tenant isolation gives structural containment for free (confirmed)

- Isolation is enforced per-query in `kb/repository.py` (`WHERE id = ? AND tenant = ?`), with the identical-404 discipline on every endpoint. A public route that **hardcodes** its tenant server-side (never accepting one from the client) is therefore structurally incapable of reading or writing any other tenant's data — the same mechanism that already separates real tenants separates the public pool from everything else.

### A5. Review-path UI already exists (confirmed)

- `/ui/businesses` auto-loads the remembered tenant, shows per-business session counts, a Sessions expansion (status, `started_at`, transcript link, recommendation links), Rename/Delete/New Interview/Resume. `/ui/interview/{session_id}/transcript` renders full transcripts. **Nothing new is needed to review a lead** beyond getting Brian to the right tenant: `businesses.html` does not yet read URL query params (`businesses_delete.html`'s `URLSearchParams` prefill is the in-repo precedent), and `loadBusinesses(tenant)` currently persists `pf_last_tenant` on every call.

### A6. Deployment/exposure context (confirmed from CLAUDE.md Deployment section)

- The always-on copy runs as a systemd service bound to `0.0.0.0:8010` on an LXC container, reachable over Tailscale. Tailscale Funnel (the later, separate step) can path-mount a **single URL prefix** — so if every public route lives under one `/public` prefix, Brian can expose *only* that prefix to the internet and the operator API/UI never gets a public URL at all. This drives D9.
- Funnel caveat: traffic proxied through `tailscaled` reaches uvicorn with a local source address, so `request.client.host` will be the same value for **all** internet visitors — per-IP limiting collapses into one shared bucket. That is fail-closed for cost (total public throughput stays bounded) but means one abuser can exhaust the public form for everyone until the window resets. Trusting `X-Forwarded-For` safely requires knowing the proxy chain at exposure time — deferred (Part E, Part F#2).

### A7. Anti-abuse infra status (confirmed)

- No CAPTCHA, no email/notification infra, no per-route rate limits exist today. `tests/conftest.py`'s autouse fixture strips `PROCESSFORGE_LLM_PROVIDER` before every test — so the zero-LLM-egress guarantee must be proven by a test that explicitly **sets** a provider and rigs `requests.post` to raise (the same proof-by-rigged-transport technique CLAUDE.md documents for the conftest fixture itself).

---

## Part B — Design decisions (with justification)

**D1 — The public flow is 100% LLM-free, structurally.** Question-asking calls `_next_question_deterministic()` directly (never `interviewer.next_question`, which would try the LLM first on the deployed box). Completion runs `interviewer.run` + `_finish_pipeline` with a `_Ctx` subclass whose `complete()` unconditionally raises, forcing the deterministic extraction fallback. **An anonymous flood can therefore run the OpenRouter bill up by exactly $0** — the cost bound is not a rate limit, it's the absence of any code path that can reach `llm.client.complete`. The LLM engages only when Brian (or an operator) later picks the lead up through existing authenticated flows (`refine`, a fresh operator interview, etc.). Tradeoff accepted: the auto-generated Tasks/Recommendation for a lead are the crude regex extraction — fine, because the artifact Brian actually reviews is the transcript, and the deterministic pipeline output is just a bonus that makes the lead render in every existing UI surface.

**D2 — All public submissions land in one dedicated, reserved tenant: `public-leads`** (module constant `_PUBLIC_TENANT` in `api/main.py`). Hardcoded server-side; no public request ever carries a tenant. Real client tenants can never collide with, be enumerated by, or be written to from the public surface (A4). The tenant name itself is the "this is a lead, not an onboarded client" marker — no per-row badge needed. `public-leads` becomes a documented reserved name operators must not use for real clients.

**D3 — Every submission creates a brand-new Business row (fresh UUID), always.** No lookup-by-name, no attach-to-existing: a self-reported name can never overwrite or link to anything (and name-matching would be an enumeration primitive). Duplicate names within `public-leads` coexist harmlessly — same posture as the existing-business spec's D5 (Rename/Delete exist for cleanup).

**D4 — Contact info is collected up front (on the start form, with the business name) and stored twice, deliberately:** canonical copy in `Business.meta` (`{"source": "public_intake", "submitted_at": <UTC ISO>, "contact": <str>}`), display copy as the session's first turn pair (`Q: contact question / A: contact`) seeded before the opener. Rationale: contact-first means even an **abandoned** partial interview is a usable lead (contact-last would make every abandoned session anonymous — the worst failure mode for a lead form); `BusinessOut`'s never-serialize-`meta` posture stays intact (A3), and the transcript page Brian already reads displays the contact with zero UI changes. The extraction transcript excludes the contact answer (Item 2) so contact text never pollutes Task fields.

**D5 — Proportionate v1 anti-abuse stack, cheapest-first:** (a) D1's zero-LLM guarantee (the real cost bound); (b) a **separate, tighter public rate limiter** — `PROCESSFORGE_PUBLIC_RATE_LIMIT_PER_MINUTE`, default 10, own bucket keyspace so public floods and operator traffic can't consume each other's windows, same fresh-read/defensive-parse convention as `_check_rate_limit`; (c) a **global daily cap on new submissions** — `PROCESSFORGE_PUBLIC_MAX_LEADS_PER_DAY`, default 20, enforced by counting `public-leads` businesses whose `meta.submitted_at` date is today (UTC) — DB-derived, so restart-proof and botnet-resistant (per-IP limits don't bound distributed abuse; this does, and it also bounds DB growth); (d) Pydantic `max_length` caps on every public input field; (e) a honeypot field (D6). Real CAPTCHA is deliberately deferred (Part E): it needs either a third-party service (external dependency + privacy story) or homegrown challenge infra, both disproportionate before the link is even public — the daily cap already caps worst-case junk at ~20 rows/day, deletable with existing tools.

**D6 — Honeypot: a hidden `website` field; if non-empty, return a fake success without touching the DB.** Response is shape-identical to the real one (`{"session_id": <random uuid4>, "question": <opener>}`) so a bot can't tell it was dropped; the fake session_id 404s if answered (indistinguishable from any unknown id). Checked before any repo open. A minimum-time-on-page check is deferred with CAPTCHA (Part E) — it requires a signed-timestamp round-trip for modest gain over honeypot + caps.

**D7 — Completion tells the prospect nothing about the analysis.** The final response is a fixed `{"status": "complete", "message": <thank-you>}` — no ROI, no recommendation, no task_count, no ids beyond the session_id they already hold. The analysis is Brian's sales asset, the numbers are crude deterministic estimates unfit for prospect eyes, and every field withheld is surface not exposed. (Contrast: the operator `answer_interview` completion returns the full `SessionResponse` — that stays operator-only.)

**D8 — The public page is a single fully self-contained template (`web/templates/public_intake.html`): inline CSS + inline JS, no `base.html`, no `app.js`/`app.css`, no link to any `/ui` route.** Three reasons: `base.html` carries the operator nav (Businesses/Operators/Audit Log — pure information disclosure on a public page); `app.js` is auth plumbing a public page must not ship; and under path-mounted Funnel (A6) `/ui/static/*` would be unreachable anyway, so external assets would 404 in exactly the deployment this page exists for. Includes `<meta name="robots" content="noindex">` and a "Powered by CwiAI" footer (Brian's branding rule; the operator UI is internal, but this page is client-facing).

**D9 — Routes: `GET /public/intake` (the page), `POST /public/intake` (start), `POST /public/intake/{session_id}/answer` (answer).** One `/public` prefix = one Funnel path-mount exposes exactly the public surface and nothing else (A6). Deliberately NOT under `/interviews` — the point is that the public family shares a prefix with nothing authenticated. Handlers live in `api/main.py` like everything else. The uuid4 `session_id` in the answer URL is the capability token: unguessable, single-session, and — because the handler resolves it with `repo.get("sessions", id, _PUBLIC_TENANT)` — worthless against any real tenant's session even if leaked.

**D10 — Question script: the existing fixed opener + the existing deterministic 6-question ladder, plus the contact question seeded first.** Same substantive ground as the operator interview (so downstream extraction/builder keyword-matching keeps working), zero new question-authoring, bounded at 7 answers total by construction. No adaptive/LLM variant for anonymous users, per D1.

**D11 — Brian's review path reuses existing pages end-to-end.** New leads = `/ui/businesses` under tenant `public-leads`; transcript (ending... starting with contact info) via the existing transcript page; the deterministic Recommendation via the existing recommendation page. The only additions: a static "Review public leads" link on the dashboard pointing at `/ui/businesses?tenant=public-leads`, and `businesses.html` honoring a `?tenant=` URL param (prefill + auto-load) **without persisting it to `pf_last_tenant`** — so checking leads never hijacks Brian's remembered working tenant. No parallel review system, no new list endpoint, no visual badge (the tenant is the badge, D2).

**D12 — No new-lead notification in v1** (email/Telegram/Hermes). It's a separate integration with its own failure modes; Brian checks the dashboard link. Flagged as a likely fast-follow (Part F#5).

---

## Part C — Numbered implementation spec

**Global constraints binding every item (Arbiter: violations are automatic REVISE):**
- **G1** No changes to `contracts/records.py`, `kb/repository.py`, `auth/*`, `stages/*`, `pipeline.py`, any migration, any existing endpoint, or any existing template except the two files named in Item 5. No new dependencies.
- **G2** `_PUBLIC_TENANT = "public-leads"` is a server-side constant. No `/public` route ever accepts, echoes, or varies on a client-supplied tenant; every record lookup on a `/public` route is tenant-scoped to it; every miss is the identical `404 "not found"`.
- **G3** Zero LLM egress on every `/public` code path, structurally: no `/public` handler may call `interviewer.next_question`, and any ctx passed to `interviewer.run`/`_finish_pipeline` from a `/public` handler must be the raising `_NoLLMCtx`. Proven by test in Item 4, not just asserted.
- **G4** `/public` responses never contain: tenant values, `business_id`, task/opportunity/recommendation/automation ids, ROI numbers, operator concepts, or stack traces. Error bodies are generic.
- **G5** Public endpoints use ONLY the new public rate limiter (never `_check_rate_limit`), with buckets that cannot collide with the operator limiter's keys (e.g. key `("public", host, window)` or a separate dict). Rate limit check is the first statement in every `/public` handler.
- **G6** Template discipline: repo-wide `innerHTML` grep across `web/templates` stays at zero matches; all dynamic DOM via `createElement`/`textContent`; single `@media` breakpoint `(max-width: 640px)` if any.
- **G7** Every cycle lands its tests in the same commit; `.\run-tests.ps1` green per cycle; `tests/conftest.py`'s autouse fixture untouched.
- **G8** `USER_MANUAL.md` and `CLAUDE.md` Status updated in the same change as any user-facing behavior change; `.env.example` gains the two new env vars (with comments) in Item 1's cycle.

### Item 1 — API: `POST /public/intake` (start a public lead interview)

**Changes: `api/main.py`, `.env.example` + new `tests/test_public_intake_api.py`:**
a. New constants: `_PUBLIC_TENANT = "public-leads"`; `_PUBLIC_CONTACT_QUESTION = "What is your name, and what is the best way to reach you (email or phone)?"`; `_PUBLIC_THANKS` (fixed thank-you string, used in Item 2); `_DEFAULT_PUBLIC_RATE_LIMIT_PER_MINUTE = 10`; `_DEFAULT_PUBLIC_MAX_LEADS_PER_DAY = 20`.
b. `_check_public_rate_limit(client_host)`: fixed-window per-IP, mirroring `_check_rate_limit`'s structure (fresh env read of `PROCESSFORGE_PUBLIC_RATE_LIMIT_PER_MINUTE`, defensive parse, stale-window pruning, 429), with bucket keys disjoint from the operator limiter's (G5).
c. `_NoLLMCtx(_Ctx)`: `complete()` unconditionally raises `RuntimeError("LLM calls are disabled on the public intake path")`.
d. `PublicIntakeStartRequest`: `business_name: str = Field(min_length=1, max_length=500)` and `contact: str = Field(min_length=1, max_length=500)`, both with the strip-and-require-nonblank `field_validator` pattern from `EditBusinessRequest`; `website: str = Field(default="", max_length=500)` (honeypot — the name must stay `website`, and it carries no secret, so no 422-redaction concern).
e. Handler order: (1) `_check_public_rate_limit`; (2) honeypot — if `body.website.strip()` is non-empty, return `{"session_id": str(uuid.uuid4()), "question": _INTERVIEW_OPENER}` **without opening any repo** (D6); (3) `_open_repo`; (4) daily cap — count rows from `repo.list_businesses(_PUBLIC_TENANT)` whose `meta.get("submitted_at", "")[:10]` equals today's UTC date; if `>=` the parsed `PROCESSFORGE_PUBLIC_MAX_LEADS_PER_DAY` cap, `429` with a generic "please try again later" detail; (5) create `Business(id=uuid4, tenant=_PUBLIC_TENANT, name=<stripped>, meta={"source": "public_intake", "submitted_at": <UTC ISO>, "contact": <stripped>})` via `KBSink().save`, then the Session (`status=active`, `transcript_ref=session_id`, same shape as `start_interview`), then `add_turn(sid, "question", _PUBLIC_CONTACT_QUESTION)`, `add_turn(sid, "answer", body.contact)`, `add_turn(sid, "question", _INTERVIEW_OPENER)`; (6) return `{"session_id": ..., "question": _INTERVIEW_OPENER}` — **no `business_id`** (G4). `repo.close()` in `finally`.

**Acceptance criteria:**
1. Happy path: 200 with exactly the keys `session_id`/`question`, `question == _INTERVIEW_OPENER`, no `business_id`/tenant anywhere in the response. Direct repo read confirms: one new Business under `public-leads` with all three `meta` keys, one active Session, exactly the three seeded turns in order (contact Q, contact A, opener Q).
2. No auth required; a garbage `Authorization: Bearer nonsense` header changes nothing (still 200).
3. Honeypot: `website` non-blank → 200 with a shape-identical body (same keys, `question == _INTERVIEW_OPENER`), and **zero** rows written (business count, session count, turn count all unchanged — assert via repo); the returned fake `session_id` gets the identical 404 from Item 2's endpoint.
4. A client-supplied `tenant` (or any extra field) in the body has no effect — the Business still lands in `public-leads`.
5. Validation: blank/whitespace-only `business_name` or `contact` → 422; over-max-length fields → 422; nothing persisted in any 4xx case.
6. Daily cap: with `PROCESSFORGE_PUBLIC_MAX_LEADS_PER_DAY=2` (monkeypatched) and 2 businesses seeded with today's `submitted_at`, the next start → 429 and nothing persisted; a business seeded with yesterday's date does not count; blank/non-int/`<1` env values fall back to the default (mirror the `_max_interview_answers` test pattern).
7. Rate limit: with `PROCESSFORGE_PUBLIC_RATE_LIMIT_PER_MINUTE=2`, the third start from one host in a window → 429; an operator endpoint called from the same host immediately after still succeeds (disjoint buckets, G5), and vice versa: public requests don't consume the operator limit.
8. `.env.example` documents both new vars. `.\run-tests.ps1` green.

### Item 2 — API: `POST /public/intake/{session_id}/answer` (deterministic ladder + LLM-free completion)

**Changes: `api/main.py` + `tests/test_public_intake_api.py`:**
a. `PublicAnswerRequest`: `answer: str = Field(min_length=1, max_length=4000)` with the strip-and-require-nonblank validator.
b. Handler order: (1) `_check_public_rate_limit`; (2) `_open_repo`; (3) `session_row = repo.get("sessions", session_id, _PUBLIC_TENANT)` → `None` ⇒ identical `404 "not found"` (this single check is what makes a leaked/guessed operator-tenant session id worthless here — comment it, same as `answer_interview` does); (4) `status != active` ⇒ `409 "interview already complete"`; (5) `add_turn(session_id, "answer", <stripped answer>)`; (6) `n = count of answer-role turns`, `s = n - 1` (substantive answers, excluding the seeded contact answer); (7) `q = interviewer._next_question_deterministic(s)` — called **directly**, never `next_question` (G3); if `q is not None`: `add_turn(sid, "question", q)`, return `{"session_id": ..., "question": q}`.
c. Completion (when `q is None`, i.e. `s >= 6`): fetch the business tenant-scoped (`repo.get("businesses", session_row["business_id"], _PUBLIC_TENANT)`); build the extraction transcript as `"\n".join` of all answer-role turn contents **excluding the first answer turn** (the contact answer, D4); set session `status=complete` and `sink.save` it; `ctx = _NoLLMCtx(repo, session_id=session_id)`; `tasks = interviewer.run(transcript, ctx)`; `_finish_pipeline(business, session, tasks, repo, sink, ctx)`; return exactly `{"status": "complete", "message": _PUBLIC_THANKS}` — discard the `SessionResult` from the response entirely (D7/G4).

**Acceptance criteria:**
1. Full drive: start via Item 1, then 6 answers. Responses 1–5 carry, in order, the exact five ladder question strings from `_next_question_deterministic`; the 6th response is exactly `{"status": "complete", "message": _PUBLIC_THANKS}` — assert the absence of `task_count`, `opportunities`, `recommendations`, ROI values, and any id other than nothing (no ids at all).
2. After completion (assert via direct repo reads or an authenticated operator request under tenant `public-leads`): session `complete`; ≥1 Task, 1 WorkflowGraph, ≥1 Opportunity, ≥1 Recommendation exist and all resolve tenant-scoped under `public-leads`; the transcript is 14 turns (7 Q / 7 A) starting with the contact pair.
3. Contact exclusion: with a distinctive contact string (e.g. `"UNIQUE-CONTACT-MARKER test@example.com"`), no Task field (`task`, `desired_outcome`, etc.) contains it — proving the extraction transcript excluded the contact answer.
4. Isolation: an unknown session id and a **real operator-tenant session id** (created via the authenticated `POST /interviews`) both get the identical 404 (same status AND body) from this endpoint, and neither gains any turn (assert via `list_turns`).
5. Answering a completed public session → 409; the extra answer turn from step (5)... is not the concern — assert no *second* completion artifacts (Task/Opportunity/Recommendation counts unchanged after the 409).
6. Validation: blank and over-4000-char answers → 422 with no turn written. Garbage `Authorization` header changes nothing.
7. No response from this endpoint, on any branch, contains `business_id`, tenant, or any record id besides `session_id` (G4).
8. `.\run-tests.ps1` green.

### Item 3 — UI: `GET /public/intake` — the self-contained public page

**Changes: `api/main.py` (one template route, no auth — mirrors `ui_login`'s comment style), new `web/templates/public_intake.html` + new `tests/test_public_intake_ui.py`:**
a. Standalone HTML (no `{% extends %}`): plain-language heading/intro ("Tell us about a manual process you'd like automated — takes about 5 minutes"), `<meta name="robots" content="noindex">`, inline `<style>` (mobile-first, only breakpoint `(max-width: 640px)` if one is needed), "Powered by CwiAI" footer. No reference to `/ui`, `app.js`, `app.css`, `fetchWithAuth`, or `requireAuth` anywhere (D8).
b. Step 1 form: business name, contact, and the honeypot `website` input hidden via an inline CSS class (`position:absolute; left:-9999px` or equivalent — NOT `type="hidden"`, which bots skip) with `autocomplete="off"` and `tabindex="-1"`.
c. Inline JS (plain `fetch`, no auth header): submit step 1 → `POST /public/intake` → on 200, swap to the Q&A view (question text via `textContent`, answer `<textarea maxlength="4000">`), loop `POST /public/intake/{sid}/answer`; when the response carries `status === "complete"`, show the thank-you message. Every submit button is disabled while a request is in flight and re-enabled on failure (bounds accidental double-submits; the server tolerates them regardless — Item 2 AC5). 429 → "We're receiving a lot of interest right now — please try again later."; other errors → a generic retry message. State lives in a JS variable only — a refresh restarts the form (no resume, Part E).

**Acceptance criteria (string assertions, mirroring `tests/test_ui.py` style):**
1. `GET /public/intake` → 200 with no auth; page contains the heading, the three step-1 fields, the noindex meta, the CwiAI footer, and the hiding class on the honeypot input.
2. Negative assertions: response text contains none of `{% extends`, `app.js`, `app.css`, `fetchWithAuth`, `requireAuth`, `href="/ui`, `innerHTML`, `pf_token`.
3. Script-presence assertions: `POST` fetches to `/public/intake` and `/intake/` + `/answer` fragments, the `status === "complete"` (or equivalent) completion branch, `disabled` handling, and `maxlength` on the answer field.
4. Repo-wide `innerHTML` grep across `web/templates` still zero; all existing `tests/test_ui.py` assertions pass unchanged.
5. `.\run-tests.ps1` green.

### Item 4 — Hardening proof: zero LLM egress + surface adversarial suite

**Changes: new `tests/test_public_intake_hardening.py` (no production code expected; any failure here is a bug in Items 1–2 to fix in this cycle):**
a. **Zero-egress proof (the load-bearing test):** inside the test (after the autouse fixture has run), `monkeypatch.setenv("PROCESSFORGE_LLM_PROVIDER", "openrouter")` + a dummy `PROCESSFORGE_LLM_API_KEY`, and monkeypatch `requests.post` to raise `AssertionError("network egress attempted")`. Drive the complete public flow (start → 6 answers → completion) — every request must succeed and the pipeline artifacts must exist. This proves G3 the same way `tests/conftest.py`'s own guarantee was proven: by rigged transport, not inference.
b. Auth boundary: every authenticated endpoint touched by the review path (`GET /businesses`, `GET /businesses/{id}/sessions`, `GET /interviews/{sid}/transcript`) still returns 401 without a token **when the tenant is `public-leads`** — the public tenant is not an auth bypass anywhere.
c. Capability containment: a public `session_id` obtained from `POST /public/intake` used against the *operator* `POST /interviews/{sid}/answer` without a token → 401 (auth still first); with a valid operator token and `tenant=public-leads` → works (that's Brian legitimately taking over a lead — assert it as intended behavior, not a hole; with any other tenant → 404).
d. Flood behavior: drive the public start endpoint to its 429 and assert row counts stop growing; assert the honeypot path at volume writes nothing.

**Acceptance criteria:** all four groups implemented and green; the egress test fails if anyone later swaps `_next_question_deterministic` for `next_question` or drops `_NoLLMCtx` (verify by temporarily reverting mentally, not in code); `.\run-tests.ps1` green.

### Item 5 — UI: Brian's review path (dashboard link + `?tenant=` param)

**Changes: `web/templates/dashboard.html`, `web/templates/businesses.html` + `tests/test_ui.py`:**
a. `dashboard.html`: in the existing "Your Businesses & Past Interviews" section, a static `<a href="/ui/businesses?tenant=public-leads">Review public leads</a>` (plain link, always present, like the existing "Manage businesses" link).
b. `businesses.html`: on load, read `new URLSearchParams(window.location.search).get("tenant")`; if present, prefill the tenant input with it and call `loadBusinesses(param)` — this takes precedence over the `pf_last_tenant` auto-load. The URL-param path must NOT write `pf_last_tenant` (D11): move the `localStorage.setItem` out of `loadBusinesses` into the submit handler (manual Load still persists; the existing `pf_last_tenant` auto-load path is unaffected since re-persisting the same value or not persisting are both fine there).
c. No new CSS, no new endpoint, `businesses_delete.html` untouched.

**Acceptance criteria:**
1. `tests/test_ui.py`: dashboard contains the new link with the exact href; `businesses.html` contains the `URLSearchParams` read, the param-precedence load, and `localStorage.setItem("pf_last_tenant"` now appears only in the submit path (string-assert its position/count or the guard).
2. All pre-existing dashboard and businesses-page assertions pass unchanged; `innerHTML` grep still zero.
3. `.\run-tests.ps1` green.

### Item 6 — Docs closeout

Update `USER_MANUAL.md` (plain language: what the public link is, that it asks a fixed set of ~7 questions and is free to run, where leads appear — the "Review public leads" link — and that `public-leads` is a reserved name not to use for a real client) and `CLAUDE.md` Status (new `/public` route family, the reserved tenant, both env vars, the zero-LLM design, and a Deployment note: when Funnel exposure happens later, path-mount **only `/public`** so the operator surface never gets a public URL, and re-check the per-IP-bucket caveat from A6). No-op checkpoint if G8 was honored per-cycle.

**Acceptance criteria:** both docs cover the feature accurately at their respective audiences' level; full `.\run-tests.ps1` green.

**Suggested sequencing:** 1 → 2 → 4 (strictly after 2) → 3 → 5 (independent of 3) → 6.

---

## Part D — Judgment calls the Arbiter must not silently re-decide

1. **Zero LLM on the public path is structural, not configurational** — `_next_question_deterministic` called directly + `_NoLLMCtx`; a rate limit alone would NOT be an acceptable substitute (D1). The cost bound must be $0, not "small".
2. The public tenant is the hardcoded reserved constant `public-leads`; no public request carries a tenant, ever (D2/G2).
3. Every submission is a fresh Business — no name matching, no attach-to-existing, even if the same prospect submits twice (D3).
4. Contact is asked FIRST (start form), dual-stored in `meta` + as the transcript's first turn pair; the extraction transcript excludes the contact answer. `BusinessOut` continues to never serialize `meta` (D4/A3).
5. Completion returns only a thank-you — the analysis (ROI, recommendation, ids) is never shown to the prospect (D7).
6. Honeypot returns a shape-identical fake success and touches no DB; CAPTCHA and time-on-page checks are deferred, on purpose, with the daily cap as the backstop (D5/D6).
7. The daily cap is DB-derived from `meta.submitted_at` (restart-proof), not an in-memory counter (D5).
8. Public endpoints use their own limiter with disjoint buckets — never `_check_rate_limit` (G5).
9. The page is fully self-contained (inline CSS/JS, no base.html/app.js, no `/ui` links) specifically so a path-mounted Funnel of `/public` alone serves it completely (D8/A6).
10. Route family is `/public/*` — not under `/interviews` — so the later Funnel step can expose exactly one prefix (D9).
11. Review path = existing pages + one dashboard link + `?tenant=` param that does NOT persist to `pf_last_tenant`; no parallel lead UI, no per-row badge (D11).
12. No contract change, no migration, no `kb/repository.py` change — if an implementation cycle claims to need one, that's a REVISE-and-rethink, not a bump (A3/G1).

## Part E — Explicitly OUT OF SCOPE

- **The Tailscale Funnel exposure itself** — Brian performs it separately, after this feature is built and reviewed. Nothing in this spec configures Tailscale.
- Real CAPTCHA (Turnstile/hCaptcha/etc.) and minimum-time-on-page tokens — deferred; honeypot + tight per-IP limit + global daily cap are the v1 posture (D5/D6). Revisit only if real-world junk volume shows up (Part F#3).
- `X-Forwarded-For`-based per-IP limiting — requires trusting a known proxy chain; decide at Funnel-exposure time (A6).
- New-lead notifications (email/Telegram/Hermes) — likely fast-follow, not v1 (D12, Part F#5).
- "Promoting" a lead into a real client tenant (data migration between tenants) — Brian re-runs a proper interview under the real tenant when onboarding; Rename/Delete + existing flows cover cleanup.
- Public-side resume/read-back: a prospect who refreshes mid-interview starts over; no public transcript endpoint, no capability re-issue.
- LLM-adaptive questioning for public submitters, in any form (D1).
- Exposing `meta`/contact through any API response model.
- Attachments/file upload on the public form; localization; styling beyond a clean minimal page.
- The two pre-existing carried bugs (operator double-submit duplicate turns; raw task UUIDs in recommendation summaries) — still not planned here.

## Part F — Could not verify statically / needs Brian's live check later

1. **Live browser click-through** — string-assertion tests can't execute JS: submit a real lead end-to-end on the LAN deployment, then confirm it appears via the dashboard "Review public leads" link, the transcript shows contact first, and the deterministic recommendation renders.
2. **Funnel source-IP behavior at exposure time** — verify whether all public traffic really arrives as one `client.host` (A6); if so, decide whether the shared-bucket per-minute limit (default 10) is acceptable or needs raising/XFF work before going live.
3. **The default knob values** — 10/min and 20/day are educated guesses; tune after real traffic. Also: is 20 leads/day generous enough for a mailshot day?
4. **Does the fixed 7-question ladder feel acceptable to a prospect?** Same "only a human judges conversation quality" carve-out as every prior spec — Brian should run it as a fake prospect once and judge the tone (the questions were written for operators transcribing a client's words, not for clients directly).
5. **Notification preference** — does Brian want a Hermes/Telegram ping on new leads (Part E deferral), or is checking the dashboard fine?
6. **Reserved-tenant name** — confirm `public-leads` is acceptable as a permanently reserved tenant name before first deploy (renaming later means a data migration).
7. **Deployed env** — defaults require no `/etc/processforge.env` change; only touch it if #3's values need overriding. Restart the systemd service after deploy (and per [[feedback-restart-after-council-build]], restart the local dev server before verifying).
