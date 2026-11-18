# Next Opponent Release Implementation Plan

> **For agentic workers:** Use the existing feature checkout; preserve unrelated edits. Steps use checkbox syntax for tracking.

**Goal:** Restore green default tests, integrate the feature, and verify production.

**Architecture:** Repair stale test inputs and run HTTP integration against FastAPI with a seeded temporary DuckDB. No production behavior changes for test repair. Release the verified exact source using the current Hetzner procedure.

**Tech Stack:** pytest, FastAPI, DuckDB, Git, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-11-18-next-opponent-design.md`; backend contracts and known failures in `docs/backend-test-backlog.md`.

## Checks

- [ ] Repair integration prerequisites in `tests/backend/test_backend_data_integration.py`: use an in-process client with the real app, synthetic league and knockout rows, and no scheduler or external provider. Keep standings shape, team count, sort, current stage, health and knockout checks meaningful. Assert errors instead of printing and passing. Verify all six cases.
- [ ] Repair Elo/Solkoff fixture inputs and assertions in `tests/backend/test_elo_calculator.py`, `tests/backend/test_main.py`, and `tests/backend/test_solkoff_calculator.py`. Verify correct live response contract and point-per-game calculations; preserve all eight formerly failing cases. No skips, xfails, or runtime logic edits.
- [ ] Record evidence in `docs/backend-test-backlog.md`, review, and run `uv run pytest` with zero failures or skips.
- [ ] Compare `origin/main` with the candidate and review branch history and source scope. Integrate only intended changes, without rewriting history. Verify GitHub main matches the tested tree.
- [ ] Follow `DEPLOYMENT.md` and November 5 release precedent: validate current production identity and safeguards, smoke the immutable candidate on a disposable DB, verify backup/rollback, switch only the UCL app, and confirm public health, API/UI, archived standings, and rollback availability. Stop if prerequisites fail.
