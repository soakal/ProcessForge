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
falling back to a **deterministic 3-question script** on ANY failure
(matches today's fixed extraction fields: time/frequency, then desired
outcome, then done). **Hard-capped at 6 answers regardless of what an LLM
would ask** — enforced at the API layer, checked before `next_question` is
even called, so a runaway adaptive conversation can't happen. Once
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
pattern as `ui_recommendation`. **Deliberately no link to this page from
`recommendations.html` yet** — `Recommendation` doesn't carry a `session_id`
or transcript reference in its frozen contract, so wiring that link is
separate plumbing left for a later cycle; this change only adds the standalone
page and its route.

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
