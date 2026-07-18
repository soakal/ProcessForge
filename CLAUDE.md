# ProcessForge

AI business-process discovery & automation platform (CWI AI brand). Full spec: the handoff doc this repo was scaffolded from — keep it as the source of truth for contracts (§3), KB schema (§4), stage signature (§5), build sequence (§6), and security baseline (§9). Don't re-litigate decisions already made there.

## Status

All council-eligible loops in §6 are complete: Loop 1 (walking skeleton, `pipeline.py`),
Loop 3 (mapper + 2→3 seam), Loop 4 (analyzer + real ROI/cross-check), Loop 5 (architect,
list-in/out), Loop 6 (builder + un-bypassable approval gate), Loop 7 (QA revision stage).
Every stage has a passing seam test in `tests/seams/`; `tests/test_skeleton.py` and
`run-tests.ps1` (pip-audit + full suite) are green.

A minimal API layer also now exists beyond §6's original scope: `api/main.py`
(`GET /health`, `POST /sessions`, `GET /recommendations/{id}`, `POST
/recommendations/{id}/approve`, `POST /recommendations/{id}/build`, `POST
/recommendations/{id}/refine`, `POST
/automations/{id}/feedback`, `GET /audit-log`, `POST /businesses/{id}/delete`,
`POST /interviews`, `POST /interviews/{id}/answer`, `GET
/interviews/{id}/transcript`).
All 6 pipeline stages are now reachable through
the live API — `build` calls `stages/builder.py` (returns `409` via its
`PermissionError` if the recommendation isn't `approved` yet, never a raw
500), `feedback` calls `stages/qa.py` and persists the resulting revision as
a new record. Every endpoint enforces tenant isolation at the DB level
(`WHERE id = ? AND tenant = ?` in `kb/repository.py` — a wrong-tenant request
gets an identical 404 to an unknown id, never a 403, so existence can't be
enumerated). None of the new endpoints call `complete()` — builder/qa stay
deterministic. `db_path` is always resolved server-side from
`PROCESSFORGE_DB_PATH`, never accepted from the client (path-traversal guard).
IP-keyed rate limiting via `PROCESSFORGE_RATE_LIMIT_PER_MINUTE` (defensively
parsed — falls back to a default of 30 on blank/non-integer values). Tested in
`tests/test_api.py` using the real `httpx` package — do **not** install
`httpx2`; despite Starlette's own deprecation warning recommending it,
`httpx2` is not a real project dependency and matches a typosquat pattern
(flagged by Claude Code's safety classifier).

**Real operator login replaced the old shared-token stopgap.** New `auth/`
package: `auth/hashing.py` (salted PBKDF2-HMAC-SHA256, 600k iterations,
`hmac.compare_digest` verify, fail-closed on malformed stored values —
NIST-recommended KDF, no `bcrypt`/`argon2` dependency needed), `auth/repository.py`
(dedicated, non-tenant-scoped data layer for `operators`/`auth_tokens` — a
NEW Alembic migration added these two tables to the same KB SQLite file;
deliberately NOT folded into `kb/repository.py`'s tenant-resolving generic
`get`/`put`, since operator accounts aren't tenant data), `auth/users.py`
(CLI: `python -m auth.users create|passwd|list|delete` — `passwd` sets a new
password for an existing operator and revokes that operator's live tokens via
`AuthRepository.set_password`; no self-serve signup,
matches the decided operator-only model: Brian's team are the only people
who log in, not self-serve multi-tenant client accounts). `POST /auth/login`
issues an opaque 7-day token (`secrets.token_urlsafe`, not a JWT — no
signing-key management needed for a small number of server-revocable
tokens) and returns an IDENTICAL 401 for both a wrong password and an
unknown username (with a real dummy-hash `verify_password` call on the
unknown-username path, so the code takes comparable time either way) — this
blunts username enumeration. `POST /auth/logout` genuinely revokes a token
(deletes the row; a reused token 401s afterward). All 5 previously-protected
endpoints now call one `_authenticate()` helper doing a real
`get_operator_by_token` lookup; `PROCESSFORGE_API_TOKEN` and its
`hmac.compare_digest` check are **fully removed** — zero references remain
anywhere in `api/main.py`. `GET /health` stays unauthenticated. Exhaustively
adversarially reviewed across 6 cycles (2 REVISE rounds caught real bugs: a
fail-open crash on a malformed/negative PBKDF2 iteration count, and a test
that couldn't actually distinguish "token row deleted" from "token merely
unreachable because its operator was deleted") — no bypass path found on
any endpoint for missing/malformed/garbage/expired tokens.

`llm/client.py`'s `complete()` is now fully implemented for **three** providers —
Anthropic direct, OpenRouter, and Ollama (local) — selected at runtime via
`PROCESSFORGE_LLM_PROVIDER` (`anthropic`|`openrouter`|`ollama`), all via the
already-pinned `requests` library (no SDK dependency added). `PROCESSFORGE_OLLAMA_HOST`
controls the local Ollama endpoint (default `http://localhost:11434`). Tested
entirely with mocked `requests.post` in `tests/test_llm_client.py` — **no real
network call or API key was ever used to build or test this**, and
`PROCESSFORGE_LLM_PROVIDER`/`PROCESSFORGE_LLM_API_KEY` still ship blank in
`.env.example`, so the feature stays inert until a provider + key is
configured. Brian has since configured `openrouter` on his machine (key in
Windows Credential Manager, not in any repo file).

`complete()` is now wired into **one** stage: `stages/interviewer.py` tries an
LLM-based extraction first (`Tier.EXTRACT`), falling back to the original
deterministic regex extraction on ANY failure (missing provider config,
network error, malformed response, or a contract-invalid field). The
untrusted transcript is delimited (`<transcript>...</transcript>`) and the
delimiter is neutralized inside user content before interpolation, so an
attacker's answer can't forge a closing tag and break out of the data block —
this closed two real gaps found across two review rounds (unescaped
delimiter, then a whitespace-variant bypass of the escaping). `mapper`/
`analyzer`/`architect`/`builder`/`qa` remain deliberately deterministic and
never call `complete()`. This was NOT run through the normal automated
council ACCEPT gate for its "does the extraction feel right" dimension —
per §6, only a human can judge that; everything else (fallback correctness,
delimiter safety) WAS adversarially tested and reviewed, with zero real LLM
calls made during the entire build.

`PROCESSFORGE_LLM_API_KEY` now has a secure local fallback: if the env var is
unset, `complete()` (for `anthropic`/`openrouter` only — `ollama` needs no key)
checks the Windows Credential Manager via the `keyring` package before failing.
Manage stored keys with `python -m llm.secrets set|status|delete <provider>`
(`set` prompts via `getpass`, never accepts the key as a CLI arg; `status`
reports presence only, never the value). Env var still wins if set, so a future
server/container deployment needs no change. All keyring interactions in
`tests/test_llm_client.py`/`tests/test_llm_secrets_cli.py` are mocked — no test
ever touches the real Credential Manager.

**`tests/conftest.py` exists for a real reason — read this before touching
it.** Once a real LLM provider is configured on a machine (env var + a real
keyring-stored key), `api/main.py`'s module-level `load_dotenv()` leaks that
config into the WHOLE pytest process the first time anything imports
`api.main` — not just the test file that imported it. Every test that then
calls `pipeline.run_session()` for real (`test_skeleton.py`, `test_pipeline.py`,
`test_api.py`, etc.) would silently make real, billable LLM calls. This bit
us for real during this build. The fix: an autouse, function-scoped fixture
in `tests/conftest.py` that `monkeypatch.delenv("PROCESSFORGE_LLM_PROVIDER")`
before every single test in `tests/` (including `tests/seams/`). Verified
closed by proof, not inference — a full run with `requests.post` rigged to
raise on any call passed with zero failures. **Do not remove or narrow this
fixture without re-proving zero network egress the same way.**

**Both remaining spec §9 requirements are now built.** An append-only
`audit_log` table (new Alembic migration, DB-enforced via `BEFORE
UPDATE`/`BEFORE DELETE` triggers that `RAISE(ABORT)` — not just a
convention) records every approval-state change (`POST
/recommendations/{id}/approve` writes one entry: operator, tenant, old/new
state; a redundant re-approve doesn't double-log). Readable via `GET
/audit-log?tenant=<tenant>` (optional `record_id` filter), tenant-scoped
like everything else. `KBRepository.log_approval_change`/`list_audit_log`
deliberately bypass the generic tenant-resolving `get`/`put` machinery —
the caller already knows the tenant, so that machinery doesn't fit.

`POST /businesses/{id}/delete` (right-to-delete): requires `tenant` +
a body `{"confirm_business_id": "<repeat the business_id>"}` that must
match EXACTLY (checked before any DB access at all — a mismatch can't
even read the DB, let alone write to it). `KBRepository.delete_business`
gathers the full child set (sessions → tasks/workflow_graphs →
opportunities via `json_each(task_ids)` intersect → recommendations →
automations) and deletes everything in one atomic transaction, FK-safe
order (children first), all-or-nothing. Deliberately does **not** purge
`audit_log` entries — the append-only trigger correctly blocks this, and
an audit trail outliving the thing it audited is the intended compliance
behavior (like a bank not shredding transaction history when an account
closes), not a bug to route around. Same tenant-isolation-via-404 pattern
as every other endpoint. Adversarially reviewed across 4 cycles — the
`json_each` query, FK ordering, and transaction atomicity were each
independently traced, and the confirmation gate's "can never reach the DB
on mismatch" property was verified structurally, not just by testing.

**Loop 2's real remaining part is now built: a genuine multi-turn interview.**
`POST /interviews` starts one (creates Business + Session, `status=active`,
`transcript_ref=session.id`, seeds a fixed opening question — no adaptive
element needed for the opener, there's no prior context to adapt to yet).
`POST /interviews/{id}/answer` submits one answer at a time: persists it to
the new `session_turns` table (new migration; `KBRepository.add_turn`/
`list_turns`, same bypass-the-generic-machinery pattern as
`audit_log`/`auth`), then calls `stages/interviewer.py`'s new
`next_question(turns, ctx)` — LLM-first (reusing the SAME per-turn
delimiter neutralization already twice-hardened for the extraction path,
since this is a second place untrusted answer text reaches an LLM prompt),
falling back to a **deterministic 6-question script** on ANY failure: time/
frequency, then desired outcome, then input-file location, then filter
rule/column values, then desired output format, then done (the ladder in
`stages/interviewer.py`'s `_next_question_deterministic`; the LLM-first
prompt's goal statement in `_build_next_question_messages` asks about the
same set of dimensions, so both paths probe the same substantive ground).
**Hard-capped at 6 answers regardless of what an LLM would ask** — enforced at the
API layer, checked before `next_question` is even called, so a runaway
adaptive conversation can't happen. Once
complete, reuses `pipeline.py`'s `_finish_pipeline` (extracted from
`run_session` in a byte-for-byte-behavior-preserving refactor) to run
mapper→analyzer→architect and return the exact same response shape
`POST /sessions` already returns. **`POST /sessions` is completely
unaffected — untouched, still one-shot, all its existing tests pass
unchanged** — this was purely additive. Zero real network calls anywhere
in the 4-cycle build; the "does this conversation feel natural" judgment
(per spec §6, only a human can make that call) happens in a real live
conversation separately, not through the automated ACCEPT gate.

A read-only `GET /interviews/{session_id}/transcript?tenant=...` endpoint
returns the full conversation (`{turn_index, role, content}` per turn, ordered)
via `repo.list_turns(session_id)`. Since `list_turns` itself is not
tenant-scoped (it only filters by `session_id`), the endpoint always resolves
`repo.get("sessions", session_id, tenant)` first and returns the identical 404
on both an unknown id and a wrong tenant before ever calling `list_turns` —
same isolation pattern as every other endpoint.

A 7th `/ui` page now consumes that endpoint: `GET
/ui/interview/{session_id}/transcript` (`api/main.py`'s `ui_interview_transcript`,
`web/templates/transcript.html`) — same no-server-side-auth-check /
`requireAuth()` / `session_id`-passed-into-the-template-for-`| tojson`
pattern as `ui_recommendation`. Client-side JS fetches the transcript via
`fetchWithAuth`, sorts turns by `turn_index` before rendering (belt-and-suspenders
alongside the API's own ordering), and renders each turn with
`textContent`/`createElement` only — no `innerHTML`, matching the rest of
`/ui`. 404/401/network-error all fall back to the same "back to dashboard"
pattern as `ui_recommendation`. At the time this page was added, there was
deliberately no link to it from `recommendations.html` yet — `Recommendation`
doesn't carry a `session_id` or transcript reference in its frozen contract,
so wiring that link needed separate plumbing, left for a later cycle (now
closed; see below).

**The frontend is now built too — ProcessForge is a complete, usable product.**
6 pages under `/ui`, served by FastAPI directly (Jinja2 templates + vanilla
JS, no build step, no framework — `jinja2==3.1.6`/`MarkupSafe` pinned in
`requirements.lock.txt`, decided over a React/NEXUS-style split frontend to
avoid a second toolchain for an internal operator tool): `/ui/login`,
`/ui` (dashboard — start an interview), `/ui/interview` (the real
back-and-forth conversation), `/ui/recommendations/{id}` (approve → build →
give feedback, all in place), `/ui/audit-log`, `/ui/businesses/delete`.

**Zero new backend endpoints or database tables across all 4 build
cycles** — every page is a thin client over the API that already existed
and was already reviewed. Auth reuses the existing Bearer-token API
exactly as-is: after login, JS stores the token in `localStorage` and a
shared `fetchWithAuth()` helper (`web/static/app.js`) attaches it to every
call — no new server-side session, no cookies.

**XSS discipline, verified not just asserted:** every dynamic DOM
insertion across all 6 pages uses `textContent`/`createElement` — a
repo-wide grep for `innerHTML` across `web/templates` returns zero
matches. Where a value needs to be embedded into an inline `<script>`
(e.g. a `recommendation_id` path param), it goes through Jinja's `|
tojson` filter, empirically verified safe against a `</script>`
breakout payload, not just assumed safe.

**The delete-business page is not laxer than the API it calls.** Its
client-side confirmation guard (typing the business ID a second time)
runs synchronously before any fetch, uses a strict `!==` string
comparison (no trim/case-fold that could accept a near-miss), and was
traced to confirm there is no code path that reaches the API call on a
mismatch — matching the backend's own pre-DB-open 400 check.

**`stages/builder.py` now emits a deterministic `handoff` brief inside
`automation.spec`** — `{"known": {...}, "open_questions": [...], "suggested_approach":
[...]}`, built purely from the Recommendation + its Opportunity + that Opportunity's
Tasks (all fetched tenant-scoped in `api/main.py`'s `build_automation`, same
identical-404-on-wrong-tenant discipline as every other endpoint; a missing/unresolvable
Opportunity is tolerated — the handoff just comes back thinner, never a different error
or a tenant leak). `known` pulls `task`/`frequency`/`time_spent`/`tools`/
`desired_outcome` straight off the fetched `Task` records; `open_questions` surfaces
thin/missing Task fields (e.g. Task's frozen contract has no field for where source
files live, so that question always fires; empty `tools_used`/`dependencies` each add
their own question); `suggested_approach` is drawn only from the spec's own existing
`steps` list. **Zero invention, zero LLM calls** — `builder.run`'s signature changed to
`run(inp: tuple[Recommendation, Opportunity | None, list[Task]], ctx) -> Automation`
(mirroring `qa.run`'s existing `tuple[Automation, str]` precedent) but the un-bypassable
approval gate (`PermissionError` on a non-`approved` Recommendation) is unchanged
behavior, just reading `inp[0]` now. `tests/seams/test_builder.py` asserts the full
`handoff` shape, that it's deterministic (same inputs → byte-identical `handoff` across
two calls), that it's pure `json.dumps`-round-trippable data, and — via a `_Ctx.complete()`
that raises `AssertionError` if ever called — that the builder never calls `ctx.complete()`
on any code path. This is additive to `automation.spec` (a JSON blob) only; no change to
`contracts/records.py`, no `schema_version` bump.

**The interview's three cycle-4 questions (input-file location, filter rule/column
value, output format) now actually flow into that handoff.** `api/main.py`'s
`build_automation` derives `session_id` from the already tenant-verified `Task`
records it fetched (`tasks[0].session_id`; the id itself was never attacker-supplied,
so calling the non-tenant-scoped `repo.list_turns(session_id)` directly with it is
safe — same reasoning already documented above for the transcript endpoint) and
passes the resulting turns as an optional 4th tuple element to `builder.run` — the
existing 3-tuple call shape still works unmodified (`turns` defaults to `[]` via
`recommendation, opportunity, tasks, *rest = inp`), so every pre-existing seam test
passes unchanged. `stages/builder.py`'s new `_interview_answers` deterministically
pairs an answer to one of the three questions by matching the immediately preceding
question-role turn's text against a keyword group per question (works against both
the deterministic ladder's exact wording and any LLM-generated phrasing of the same
question) — never guesses when a matching question/answer pair isn't present. Present
answers land as new `input_file_location`/`filter_rule`/`output_format` keys in
`handoff.known`; a genuine `input_file_location` answer also drops the matching
"where does the input file live" line from `handoff.open_questions` (the other two
questions never had a corresponding `open_questions` entry to drop). Zero LLM calls
in the new code path — `tests/seams/test_builder.py` adds two seam tests covering
both the happy path (all three answers land, the open question shrinks) and the
no-match path (an orphan answer or off-topic question is correctly ignored, not
guessed into a slot).

**`POST /recommendations/{id}/refine`** lets an operator answer a handoff's open
question(s) *after* an automation already exists, without starting a whole new
interview. Same tenant-scoped identical-404 discipline as every other endpoint;
`session_id` is derived the same already-tenant-verified-Task way `build_automation`
does (never attacker-supplied), so calling the non-tenant-scoped
`repo.add_turn`/`list_turns` with it is safe. The request body's
`turns: [{question, answer}, ...]` are appended to `session_turns` via
`repo.add_turn` — deliberately NOT subject to `_MAX_INTERVIEW_ANSWERS` (that cap only
governs the original `/interviews/{id}/answer` flow) — and `builder.run` is re-run
against the now-fuller turns to regenerate `handoff` from scratch, same deterministic,
zero-`ctx.complete()` path as `build_automation`. The result is persisted as a
**new** `Automation` row (fresh UUID id via `builder.run`, so `repo.put` inserts
rather than updates) with `spec["revision"]` set to one more than the highest
`spec.get("revision", 1)` across every prior Automation for that Recommendation —
reusing `stages/qa.py`'s existing revision-numbering convention rather than
inventing a second one. `KBRepository.list_automations_by_recommendation` (new,
tenant-scoped) finds those prior Automations. Prior Automation rows are never
mutated, so every earlier revision (including the original, pre-refine build)
stays independently readable with its original `handoff` intact. `spec["revision"]`
is free-form data inside the existing `spec: dict` JSON blob — no change to
`contracts/records.py`, no `schema_version` bump. If `turns` is non-empty but no
`session_id` is resolvable (opportunity/tasks missing), the endpoint returns
`409` instead of silently dropping the answers and still persisting a
revision-bumped Automation whose handoff doesn't reflect them; an empty
`turns` list still tolerates a missing session the same as before.

**`POST /automations/{id}/link`** saves a reference to an existing product/tool
an operator found for an Automation (e.g. a Zapier recipe, a vendor's app) —
backend-only this cycle, deliberately not wired into any `/ui` template or
route yet (that clickable-link display is a separate, later cycle's job). New
`LinkRequest` Pydantic model: `product_url: str` (required) + optional
`product_notes: str | None`. `product_url` is validated with a scheme
ALLOW-list via a `field_validator` (`urlparse(value).scheme in ("http",
"https")` and a non-empty `netloc`) — not a blocklist — since this value will
become a clickable `href` in that future cycle; `javascript:`/`file:`/`data:`/
any other scheme, and malformed URLs, are rejected with FastAPI's normal `422`
request-validation response before the handler even runs. Same tenant-scoped
identical-404 discipline as every other automation endpoint (mirrors
`submit_automation_feedback`'s `repo.get("automations", automation_id,
tenant)` pattern exactly). `product_url`/`product_notes` are stored as two new
keys directly inside the existing free-form `automation.spec: dict` JSON blob
via `repo.put` — no change to `contracts/records.py`, no `schema_version`
bump. Both fields are pure data, never executed or evaluated.

**That clickable-link display now exists.** `web/templates/recommendations.html`'s
`renderAutomation()` calls a new `renderProduct()` on every render (initial
build and every later feedback revision) that reads `automation.spec.product_url`/
`product_notes` — pure client-side, no new backend endpoint or route. A
dedicated `#automation-product` block is `display:none` with no appended
content whenever `product_url` is absent (genuinely hidden, not just empty).
When present, the link is built with `document.createElement("a")` +
`textContent` only (never `innerHTML` — a repo-wide `innerHTML` grep across
`web/templates` still returns zero matches), and the URL's scheme is
re-validated client-side (`new URL(productUrl).protocol` checked against
`"http:"`/`"https:"`) immediately before ever assigning `.href` — defense-in-depth
on top of `LinkRequest`'s own backend validator, since a stored value should
never be trusted blindly when building an `href`. If that re-check somehow
fails, the raw value is rendered as plain text via `textContent` instead of a
clickable link, never omitted-vs-shown inconsistently. `product_notes` renders
via `textContent` if present. Previously-rendered link/notes content is
explicitly cleared (`removeChild` loop + `textContent = ""`) at the top of
every `renderProduct()` call so a stale link from a prior automation can't
linger across a feedback-revision re-render.

**The recommendation page now links to its interview transcript.**
`RecommendationOut` (an API response model in `api/main.py`, NOT
`contracts/records.py` — the frozen `Recommendation` contract itself is
untouched) gained an additive `session_id: str | None = None` field.
`GET /recommendations/{id}` resolves it using the exact same tenant-scoped
Opportunity -> Task lookup `build_automation` already established (fetch the
Opportunity, then each of its `task_ids`' Tasks, tenant-scoped; take the
first resolved Task's `session_id`) — a missing/unresolvable Opportunity or
an Opportunity with no resolvable Tasks is tolerated exactly like
`build_automation` already tolerates it, `session_id` just stays `None`,
never a different error or a tenant-info leak. The existing tenant-isolation
discipline is unchanged: a wrong-tenant request still 404s before this
resolution code ever runs. `web/templates/recommendations.html` renders a
"View interview transcript" link (`document.createElement("a")` +
`textContent` only, never `innerHTML`) pointing at
`/ui/interview/{session_id}/transcript?tenant=...` — genuinely hidden
(`display:none`, no appended content) whenever `session_id` is absent,
cleared and re-rendered every time `renderRecommendation()` runs (e.g. after
approve), matching `renderProduct()`'s own clear-before-render discipline.

**Item 7 (clearer UI) is 3 of 4 slices done.** `/ui/login`, `/ui` (dashboard),
`/ui/interview`, and `/ui/interview/{session_id}/transcript` each now open
with two short, plain-language lines: a `.page-intro` purpose sentence (what
this page is for) and a `.next-step` sentence (what to do next), reusing the
two classes already added to `web/static/app.css` in slice 1 — no new CSS
this slice. Both are static template text — nothing dynamic is rendered
through them, so this introduces no new XSS surface. `interview.html`'s `<h1>`
was plainened to "Answer a Few Questions About Your Process" and its
next-step line describes the actual on-page action (read the question, type
an answer, select "Submit Answer") without duplicating the JS-driven
question-refresh flow's own messaging. `transcript.html`'s `<h1>` was
plainened to "Your Interview Transcript"; since that page is read-only (see
above), its next-step line points back to the dashboard rather than implying
a forward data-entry action, consistent with its own existing JS fallback's
"Back to dashboard" link. In both templates the new copy sits directly under
the `<h1>`, above the existing `#interview-missing`/`#transcript-missing`
error divs, so it still renders in any fallback/error state. No element
IDs/classes any existing JS depends on were touched. `tests/test_ui.py`
asserts the new copy appears in `response.text` for both pages, mirroring
the assertions already added for login/dashboard.

**`recommendations.html` is now the third slice, and also gained the ROI
display item 7's acceptance criteria specifically calls for.** `RecommendationOut`
(the API response model, still not `contracts/records.py` — the frozen
`Recommendation` contract is untouched) gained two more additive fields,
`roi_low_hrs`/`roi_high_hrs: float | None = None`, resolved server-side by a
new `_resolve_roi()` helper that mirrors `_resolve_session_id()`'s shape
exactly: same tenant-scoped `repo.get("opportunities", recommendation.opportunity_id,
tenant)` fetch, same "never errors, stays `None` on any unresolvable
Opportunity" tolerance, called from **both** `get_recommendation` and
`approve_recommendation` (repeating `_resolve_session_id`'s own cycle-9 fix,
not its earlier get-only mistake — ROI must survive an Approve click, not
just the initial page load). Unlike `session_id`, ROI lives directly on the
Opportunity, so it doesn't need the Task hop `_resolve_session_id` makes; it
resolves even when the Opportunity's Tasks are gone, and is only `None` when
the Opportunity itself can't be found. `recommendations.html` gained a
`.page-intro`/`.next-step` pair (same two `web/static/app.css` classes as the
other three pages) plus two new prominence-only classes, `.status-line` and
`.roi-line` (bold, slightly larger text — the acceptance criteria's own
wording is "show ROI and status prominently"), applied to the existing status
paragraph and a new ROI paragraph. Unlike the other three pages' static
next-step text, this page's next-step line is JS-driven and changes with the
recommendation's real state — draft ("review the ROI and summary above, then
select Approve"), approved-not-yet-built ("select Build to generate the
automation"), and built ("review the automation below, then submit feedback
if changes are needed") — tracked via a new `currentRecommendation` module
variable alongside the existing `currentAutomation` one, re-evaluated by a
shared `renderNextStep()` called from both `renderRecommendation()` and
`renderAutomation()` so it stays correct after every approve/build/feedback
round-trip. `renderRoi()` is None-safe on the frontend too (checks both
`roi_low_hrs`/`roi_high_hrs` are non-null before rendering, genuinely hides
the element via `display:none` + cleared `textContent` otherwise) and, like
every other dynamic element on this page, is built with `textContent` only —
no `innerHTML`. `tests/test_api.py` adds ROI-resolvable and
ROI-unresolvable-Opportunity coverage for both `get_recommendation` and
`approve_recommendation` (4 new tests, mirroring the existing `session_id`
test shapes); `tests/test_ui.py` adds string assertions for the new
`.page-intro`/`.next-step`/`.status-line`/`.roi-line` markup and all three
next-step message variants, plus a dedicated `renderRoi()`-presence test
mirroring the existing `renderProduct`/`renderTranscriptLink` code-presence
tests.

**Item 7 (clearer UI) is now fully done — the last 2 of 4 slices are
complete.** `audit-log.html` and `businesses_delete.html` each gained their
own `.page-intro`/`.next-step` pair, reusing the same two `web/static/app.css`
classes as the other five pages — no new CSS. Both are static template text,
introducing no new XSS surface. `audit-log.html`'s next-step line describes
the actual on-page action ("enter a tenant below ... then select Search").
`businesses_delete.html`'s next-step line is deliberately CAUTION-framed
rather than a generic nudge — "double-check the business ID before deleting
— this action cannot be undone" — since this is the one destructive-action
page on the site; it sits directly above the existing `#delete-warning`
paragraph (left completely unchanged) rather than replacing or duplicating
it, and none of the existing element IDs, the confirm-match guard logic, or
the `textContent`/`createElement` rendering were touched. `tests/test_ui.py`
extends `test_ui_audit_log_renders_form`/`test_ui_businesses_delete_renders_form`
with the same `.page-intro`/`.next-step` string assertions used for every
other page. **Item 7's four-slice buildout is complete: all 7 `/ui` pages now
carry the plain-language intro/next-step pattern.**

Remaining (none of these are council loops, all are genuinely optional
polish, not blockers to using the product):
- Real multi-tenant client self-serve accounts, if that business model is
  ever wanted (today: Brian's team are the only operators, by design).
- Anything from spec §11's explicitly-out-of-scope list (voice, real
  automation execution against live systems, field-level encryption).

## Build engine

Built via an internal autonomous council-loop tool (Arbiter/Engineer/Realist roles), pointed at this repo as its target. ProcessForge does **not** reimplement council-iteration mechanics itself — no standalone `run-loop.ps1` batch engine here.

Open follow-up: wire the §6.1 per-iteration build-log hook (`tools/brain_log.py`) into that tool's iteration-end point. Needs to be done from the build tool's own session, not this repo's.

## Test command

```powershell
.\run-tests.ps1
```
Runs `pip-audit` against `requirements.lock.txt` then `pytest -q`. A failing pip-audit is a non-ACCEPT (§9) — don't skip it.

## Key non-negotiables (see spec §0/§9 for full detail)

- Contracts in `contracts/records.py` are frozen — additive changes only, bump `schema_version`.
- Every stage is `run(inp, ctx) -> out`, output validated against its contract.
- ROI is always a range (`roi_low_hrs < roi_high_hrs`) with non-empty `assumptions` — enforced in the model.
- Builder refuses to produce an executable Automation unless `approval_state == approved` (see `stages/builder.py` — gate is live even in the Loop 0 stub).
- Tenant isolation is enforced in `kb/repository.py`, not by callers. Opportunity/Recommendation/Automation don't carry tenant in the frozen contract, so the repo resolves it transitively through the parent chain (task_ids → session → business) and stores it as a KB-internal `tenant` column, stripped again on read.
- LLM-generated automation output is data (a declarative spec), never executable code. No `eval`/`exec`/`shell=True` on it, ever.
- Secrets (`PROCESSFORGE_LLM_API_KEY`, `BUILD_LOG_TOKEN`) come from env only — see `.env.example`. Real `.env` is gitignored.

## Env vars

See `.env.example`. `PROCESSFORGE_DB_PATH` for the KB SQLite file, `PROCESSFORGE_MODEL_{EXTRACT,REASON,ARBITER}` + `PROCESSFORGE_LLM_API_KEY`/`PROCESSFORGE_LLM_PROVIDER` for `llm/client.py`, `PROCESSFORGE_RATE_LIMIT_PER_MINUTE` for the API, `BUILD_LOG_URL`/`BUILD_LOG_TOKEN` for build-session logging. No env var for API auth anymore — operator accounts are created via `python -m auth.users create <username>` (see `auth/users.py`), not configured in `.env`.

## Keeping the user manual current

`USER_MANUAL.md` (repo root) is the non-technical, plain-language counterpart to this
file. It must be updated in the **same change** as any future work that alters
user-facing behavior, setup steps, or what's possible today — not filed as a separate
afterthought — mirroring the discipline already used for this file's own Status
section above. `USER_MANUAL.md` must stay written for a non-technical reader: no
jargon creep over time. If a term needs explaining (API, endpoint, env var, CLI,
repo, dependency, etc.), explain it in plain words there rather than assuming the
reader already knows it.
