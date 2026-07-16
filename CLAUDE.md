# ProcessForge

AI business-process discovery & automation platform (CWI AI brand). Full spec: the handoff doc this repo was scaffolded from — keep it as the source of truth for contracts (§3), KB schema (§4), stage signature (§5), build sequence (§6), and security baseline (§9). Don't re-litigate decisions already made there.

## Status

All council-eligible loops in §6 are complete: Loop 1 (walking skeleton, `pipeline.py`),
Loop 3 (mapper + 2→3 seam), Loop 4 (analyzer + real ROI/cross-check), Loop 5 (architect,
list-in/out), Loop 6 (builder + un-bypassable approval gate), Loop 7 (QA revision stage).
Every stage has a passing seam test in `tests/seams/`; `tests/test_skeleton.py` and
`run-tests.ps1` (pip-audit + full suite) are green.

Remaining before this is a usable product (none of these are council loops):
- **Loop 2** (thicken interviewer: adaptive follow-ups, full field extraction,
  pause/resume) — explicitly hand-build only per spec §6, judged by eye, not by a
  council ACCEPT gate.
- **API layer** — `api/main.py` doesn't exist yet, only an empty `api/__init__.py`.
  Nothing is reachable over HTTP.
- **LLM wiring** — `llm/client.py`'s `complete()` is still `raise NotImplementedError`;
  no `PROCESSFORGE_LLM_API_KEY` set. Every stage today is deliberately deterministic to
  avoid it.

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
