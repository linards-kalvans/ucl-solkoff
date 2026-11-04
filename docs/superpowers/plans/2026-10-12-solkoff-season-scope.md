# Current-season Solkoff Implementation Plan

**Goal:** Restore league-stage Solkoff values without deleting or modifying historical matches.

**Approved scope:** Calculator, opponent breakdown, scheduler competition wiring, and focused regression tests. No parser cleanup, Elo changes, standings ingestion redesign, git commit/push, or production replacement. Production deployment requires separate explicit rollout authorization under DEPLOYMENT.md.

## Design

- Restrict opponent selection to finished matches in the configured competition, stage `LEAGUE_STAGE`, and current season date window. Use inclusive August 1 / exclusive following August 1, matching existing historical-ingestion convention. Resolve the year using UTC at call time, not initialization, so long-running schedulers cross the season boundary correctly.
- Extend `SolkoffCalculator(db, competition_id="CL")`; provide a small shared season-bounds helper in its module, with an optional date argument for deterministic tests. Use that same helper and predicates in the breakdown endpoint. Parameterize SQL values.
- Keep current PPG arithmetic, unique-opponent averaging, stored coefficients, and public JSON shape unchanged. Keep historical matches available for Elo and historical analysis. No database migration or historical-data writes.
- Pass configured competition from scheduler to calculator; breakdown reads `COMPETITION_ID`, default `CL`, consistently with application startup.
- Do not infer season from latest finished match: absent current matches must yield no opponents, not silently select a previous season. Metadata-driven season tracking would require broader ingestion/schema work and is outside this surgical fix.

## Files and acceptance

1. `backend/solkoff_calculator.py`: shared season bounds; season/competition/stage predicates on home and away opponent queries. Winners 0, losers 3, draws 1 after one round. No matches returns 0. Bounds handle July/August and ISO date/timestamp rows.
2. `backend/main.py`: identical scope on both breakdown queries. Historical matches cannot appear in counts or outcomes; stored coefficient and average breakdown PPG agree after recalculation.
3. `backend/scheduler.py`: calculator receives scheduler competition.
4. `tests/backend/test_solkoff_scope.py`: real temporary DuckDB fixture, current one-round win/loss and draw matches, contaminating prior-season, other-competition, knockout, future-season, unfinished, and null-stage matches. Verify stored values, API standings and breakdown, no-match behavior, boundary behavior, and unchanged historical match rows. Patch clock deterministically and avoid lifespan/network calls.

## Verification — TDD

- Test engineer writes focused tests first, runs `uv run pytest tests/backend/test_solkoff_scope.py -q`, and records red-phase evidence.
- Software engineer implements and reruns focused tests plus `tests/backend/test_scheduler.py`.
- Run existing backend suite and compare pre-existing failures where needed. Existing mock tests using old sum-of-points semantics are known stale; do not rewrite unrelated tests to conceal backlog.
- Independent code review checks scope agreement, rollover, preservation, parameterized SQL, and API compatibility.
- Publish diff limited to files edited in this task. No live administrative requests or production recreation during implementation.

## Documentation

This plan records scope, rationale, season convention, checks, and production boundary. Update only this task's verification notes if necessary; leave existing deployment records intact.

## Verification record

- TDD red phase: 32 failed, 5 passed in the new focused suite before implementation; failures demonstrated historical/competition/stage leakage and missing scope interfaces.
- Final focused check: `uv run rtk pytest tests/backend/test_solkoff_scope.py tests/backend/test_scheduler.py -q` — 44 passed (38 scope regressions and 6 existing scheduler tests).
- Backend baseline excluding new tests: 57 passed, 18 failed. Final backend suite: `uv run rtk pytest tests/backend -q --tb=line` — 95 passed, the same 18 pre-existing failures (6 live-server connection errors, 4 data-service mock expectations, 3 Elo mock errors, 1 stale breakdown mock, 4 stale sum-of-points calculator mocks). No skipped tests.
- Independent review accepted implementation; its minor request for historical-only/empty API breakdown coverage was added and passed.
- Historical row preservation is verified against real temporary DuckDB fixtures. Production database and container were not modified during implementation (initial local verification only).
- Production rollout (separate approval, executed 2026-10-12): deployed as release `20261012T143625Z-cd69ffa-45a5aee75836`; stored production coefficients were recalculated offline under exclusive access before cutover, then recalculated by the existing scheduler startup update. Live standings show 16 winners coeff 0.0, 4 draws coeff 1.0, 16 losers coeff 3.0; all 36 details have 1 match/1 opponent with no live-vs-offline drift. See [`docs/releases/2026-10-12-solkoff-scope-release.md`](../../releases/2026-10-12-solkoff-scope-release.md) for full rollback evidence.
