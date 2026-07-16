# ProcessForge

AI business-process discovery & automation platform (CWI AI brand). Full spec: the handoff doc this repo was scaffolded from — keep it as the source of truth for contracts (§3), KB schema (§4), stage signature (§5), build sequence (§6), and security baseline (§9). Don't re-litigate decisions already made there.

## Status

All council-eligible loops in §6 are complete: Loop 1 (walking skeleton, `pipeline.py`),
Loop 3 (mapper + 2→3 seam), Loop 4 (analyzer + real ROI/cross-check), Loop 5 (architect,
list-in/out), Loop 6 (builder + un-bypassable approval gate), Loop 7 (QA revision stage).
Every stage has a passing seam test in `tests/seams/`; `tests/test_skeleton.py` and
`run-tests.ps1` (pip-audit + full suite) are green.

A minimal API layer also now exists beyond §6's original scope: `api/main.py`
(`GET /health`, `POST /sessions`), with an explicit **stopgap auth model** —
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
`.env.example`, so the feature stays inert until Brian actually configures a
provider + key. `complete()` is NOT wired into any stage yet — every stage
(`interviewer`/`mapper`/`analyzer`/`architect`/`builder`/`qa`) remains
deliberately deterministic and never calls it.

`PROCESSFORGE_LLM_API_KEY` now has a secure local fallback: if the env var is
unset, `complete()` (for `anthropic`/`openrouter` only — `ollama` needs no key)
checks the Windows Credential Manager via the `keyring` package before failing.
Manage stored keys with `python -m llm.secrets set|status|delete <provider>`
(`set` prompts via `getpass`, never accepts the key as a CLI arg; `status`
reports presence only, never the value). Env var still wins if set, so a future
server/container deployment needs no change. All keyring interactions in
`tests/test_llm_client.py`/`tests/test_llm_secrets_cli.py` are mocked — no test
ever touches the real Credential Manager.

Remaining before this is a usable product (none of these are council loops):
- **Choose + configure a provider** — set `PROCESSFORGE_LLM_PROVIDER`, then
  either set `PROCESSFORGE_LLM_API_KEY` or run
  `python -m llm.secrets set anthropic`/`openrouter` (or just run Ollama
  locally, no key needed) to actually activate LLM calls. Purely a config
  decision now — no code work required to switch providers.
- **Loop 2** (thicken interviewer: adaptive follow-ups, full field extraction,
  pause/resume) — explicitly hand-build only per spec §6, judged by eye, not by a
  council ACCEPT gate. Needs a provider configured above before it can do
  anything LLM-based; still deferred.
- **Real multi-tenant auth** — replace the API layer's bearer-token stopgap
  (see above).

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
