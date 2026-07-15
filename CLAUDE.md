# ProcessForge

AI business-process discovery & automation platform (CWI AI brand). Full spec: the handoff doc this repo was scaffolded from — keep it as the source of truth for contracts (§3), KB schema (§4), stage signature (§5), build sequence (§6), and security baseline (§9). Don't re-litigate decisions already made there.

## Status

Loop 0 (manual scaffold) complete: contracts, KB schema + migration, empty stage files, tenant-safe repository, KBSink, build-session logger, red `tests/test_skeleton.py`. Loop 1 (walking skeleton) not started.

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
