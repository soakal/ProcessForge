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
/automations/{id}/feedback`). All 6 pipeline stages are now reachable through
the live API — `build` calls `stages/builder.py` (returns `409` via its
`PermissionError` if the recommendation isn't `approved` yet, never a raw
500), `feedback` calls `stages/qa.py` and persists the resulting revision as
a new record. Every endpoint enforces tenant isolation at the DB level
(`WHERE id = ? AND tenant = ?` in `kb/repository.py` — a wrong-tenant request
gets an identical 404 to an unknown id, never a 403, so existence can't be
enumerated). None of the new endpoints call `complete()` — builder/qa stay
deterministic. With an explicit **stopgap auth model** —
single shared bearer token via `PROCESSFORGE_API_TOKEN`, single-tenant-per-
deployment, compared with `hmac.compare_digest`. `db_path` is always resolved
server-side from `PROCESSFORGE_DB_PATH`, never accepted from the client
(path-traversal guard). IP-keyed rate limiting via
`PROCESSFORGE_RATE_LIMIT_PER_MINUTE` (defensively parsed — falls back to a
default of 30 on blank/non-integer values). Tested in `tests/test_api.py`
using the real `httpx` package — do **not** install `httpx2`; despite
Starlette's own deprecation warning recommending it, `httpx2` is not a real
project dependency and matches a typosquat pattern (flagged by Claude Code's
safety classifier). **This auth model is an intentional stopgap, same pattern
as `llm/client.py`'s stub — replace with real per-tenant auth before any
multi-tenant deployment.**

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

Remaining before this is a usable product (none of these are council loops):
- **Loop 2, the real remaining part** (adaptive follow-up QUESTIONS, a genuine
  back-and-forth conversation, pause/resume across multiple turns) — a single
  LLM-assisted extraction call from one batch of answers (done above) is not
  the same thing as a conversational interview. The API shape is still
  single-shot (`POST /sessions` takes all answers at once); a real multi-turn
  flow needs new session state handling and API surface. Explicitly
  hand-build/judged-by-eye per spec §6, not a council ACCEPT gate.
- **Real operator auth** — replace the API layer's shared-bearer-token stopgap
  with a real login (Brian's team are the only operators — not self-serve
  multi-tenant client accounts; they select `tenant` per request, same as
  today). This is the next piece of work.
- **Audit log** for approval-state changes, and a **delete-by-business**
  endpoint (both required by the original spec §9, never built).
- **Frontend** — plain HTML/JS served by FastAPI (Jinja2 + vanilla JS,
  decided over a React/NEXUS-style split frontend to avoid a second
  toolchain for what's an internal operator tool) — once the backend above
  is complete.

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

See `.env.example`. `PROCESSFORGE_DB_PATH` for the KB SQLite file, `PROCESSFORGE_MODEL_{EXTRACT,REASON,ARBITER}` + `PROCESSFORGE_LLM_API_KEY` for `llm/client.py`, `BUILD_LOG_URL`/`BUILD_LOG_TOKEN` for build-session logging.

## Keeping the user manual current

`USER_MANUAL.md` (repo root) is the non-technical, plain-language counterpart to this
file. It must be updated in the **same change** as any future work that alters
user-facing behavior, setup steps, or what's possible today — not filed as a separate
afterthought — mirroring the discipline already used for this file's own Status
section above. `USER_MANUAL.md` must stay written for a non-technical reader: no
jargon creep over time. If a term needs explaining (API, endpoint, env var, CLI,
repo, dependency, etc.), explain it in plain words there rather than assuming the
reader already knows it.
