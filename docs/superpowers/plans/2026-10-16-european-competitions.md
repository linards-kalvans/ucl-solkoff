# European Competitions Implementation Plan

**Goal:** Add Europa League and Conference League to the existing app with a competition selector and existing season, Solkoff and knockout views.

**Architecture:** One application and DuckDB database. Competition is explicit request/UI state; existing requests still default to COMPETITION_ID (CL when unset). Live tables remain live-only snapshots, now keyed by competition and team; historical seasons continue reconstruction from competition/season-filtered matches. This avoids introducing a second historical standings source.

**Verification:** tested (focused regression tests, existing backend and frontend suites; no mandatory red phase). Preserve unrelated dirty files. No commits, worktrees, authenticated feed requests, deployment, or production database writes.

## 1. Contracts and design decisions

- Shared registry: CL = Champions League (8 league games), EL = Europa League (8), UCL = Conference League (6). UCL is the provider code for Conference League, not the app's CL brand. Source: https://docs.football-data.org/general/v4/lookup_tables.html
- Add optional case-insensitive `competition` query parameter to every season-dependent data GET. Unknown explicit codes return 400, never silently fall back. Omitted code preserves configured default. Preserve existing response schemas.
- Add `/api/competitions`: configured default plus code, label and league-match count for each supported competition. Empty competitions remain selectable and show honest empty states.
- Extend live standings and coefficient primary keys to `(competition_id, team_id)` because clubs can appear in different competitions across stored data. Retain DEFAULT 'CL' for legacy SQL inserts/tests. Migrate legacy snapshot rows to validated configured default, transactionally, idempotently, preserving values and foreign keys. Do not assign unknown NULL match rows to CL without provenance.
- Keep historical tables reconstructed from matches, as today. Supported season window remains 2024/25 onward. Elo remains shared across European competitions; update misleading CL-only explanatory copy.
- Scheduler in application startup refreshes all three supported competitions; direct single-competition scheduler construction remains compatible. Each refresh has independent failure handling; never erase stored data on provider failure. Recalculate Elo once after refresh loop.
- Strict historical source selection: never load a different competition's file as fallback. If requested historical file is absent, bounded API-season fallback is allowed through existing rate-limited client. Missing/denied data stays missing, logged, never fabricated. Do not expand CL-only historical-repair authorization.
- Conference league calendar ends in December. Existing February boundary can remain an inclusive guard for explicitly tagged league data, not a reason to infer eight matches. Audit stage/matchday assumptions and scope standings/knockout/analysis to selected competition.

## 2. File-by-file implementation

1. Create `backend/competitions.py`: registry, configured/default validation and reusable code resolver. Invalid request code raises ValueError for API conversion to 400. Keep small; no plugin framework.
2. Update `backend/database.py`: fresh competition-keyed snapshot schemas and atomic legacy rebuild migration. Remove/merge old coefficient REAL migration so it cannot recreate old primary keys. Default legacy inserts to CL. Test reopen/idempotence/rollback and preserve REAL conversion.
3. Update `backend/data_service.py`: competition-aware standings upsert and knockout match insert/update (currently misses competition_id). Historical sync must distinguish absent source from successful fetch; request API season only as fallback when needed. Preserve data on failures.
4. Update `backend/solkoff_calculator.py`: scope opponent standings query and coefficient upsert to instance competition; restrict calculate_all to selected standings rather than overwriting every team across competitions.
5. Update `backend/scheduler.py` and startup in `backend/main.py`: optional explicit competitions list with single-competition backwards compatibility; startup supplies supported list; isolated failures and one Elo replay.
6. Update `backend/main.py`: competition parameter and validation on seasons, standings, team details, current stage, playoff pairs, knockout pairs and pair analysis. Scope live joins and coefficient reads. Leave health/admin/debug contracts unchanged; no admin expansion.
7. Update `backend/playoff_analyzer.py`: competition-scope live strength joins and audit pair/history queries. Preserve intended pooled Elo; never pool league standings. Preserve archived-season future-data protections.
8. Update `backend/github_data_fetcher.py` and only necessary parser portions: exact competition filename/confirmed header selection; no cross-competition generic fallback. Missing source produces no matches. Unknown header must not become CL silently; retain explicitly supplied context where appropriate.
9. Update `frontend/index.html`, `frontend/app.js`, minimal `frontend/styles.css` if needed: labelled competition selector next to season; fetch metadata before seasons; include competition on every data request. Reuse generation guards, clearing every competition-dependent cache on switch. Guard metadata/season discovery too, so late responses cannot restore previous selection. On switching, choose valid current/default season of new competition. Dynamic competition copy; shared Elo explanation. Keep mobile layout and accessible labels.
10. Tests: new `tests/backend/test_competitions.py` and focused migration/sync/scheduler tests; new `tests/frontend/test_competitions.py`. Extend existing frontend router fixture minimally for competition metadata and per-competition data without changing default fixture semantics. Do not modify unrelated pre-existing dirty tests.

## 3. Acceptance criteria

- CL, EL, UCL can coexist; same team ID has distinct standings/coefficient values per competition. Endpoint details use correct opponents and PPG, not another competition's table.
- No-parameter API behavior and configured default remain compatible. Invalid codes return 400 on all data routes. Empty selected competition shows no default-competition rows.
- Existing CL database migrates without data loss; reopening is a no-op; migration failure rolls back. No speculative NULL-match attribution.
- Knockout ingestion persists competition ID. Current and archived pairs, seasons and analysis remain scoped. Shared Elo stays intentional.
- One feed failure does not prevent other competitions updating or remove existing data. Historical fallback cannot import CL as EL/UCL.
- UI switching reloads all relevant requests, removes old caches, and rejects out-of-order results (including seasons). Conference six-game format is metadata, not hardcoded eight-game copy.
- Backend and frontend regressions cover default, all three selections, invalid/empty selection, failure isolation, migration and rapid switching. Mobile selector causes no horizontal overflow.

## 4. Verification and documentation

- Baseline reported by inspection agent: `uv run pytest tests/backend/test_seasons.py tests/backend/test_solkoff_scope.py -q`: 64 passed. Backend baseline: 18 failed, 190 passed (existing documented backlog). Preserve evidence and compare actual failures, not only counts.
- Run new backend tests plus season/Solkoff/database/scheduler regressions, then `uv run pytest tests/backend -q`. Run `uv run pytest tests/frontend -q` using repository browser setup. Report every failure or unavailable browser explicitly. Use temporary databases only.
- Update `README.md`, `docs/seasons.md`, and new `docs/competitions.md` with query contract, live snapshot semantics, migration, scheduling and feed subscription caveat. Do not edit already-dirty DEPLOYMENT.md.
- Obtain independent code review after spec compliance inspection; fix critical correctness issues, rerun affected checks.
- Publish critique URL filtered only to session-edited files. No commit/push/deploy without explicit request.

## 5. Scope boundary and known dependency

No new provider, cross-competition ranking, pre-2024 group-stage UI, model changes, redesign, or production rollout. Actual EL/UCL feed entitlement and completeness cannot be proven without authenticated calls; implementation must work with recorded/mock data and expose unavailable data honestly. Provider coverage: https://www.football-data.org/coverage

## 6. Execution checkpoint

Implementation and independent review completed in the existing checkout; no worktree, commit, push or deployment. Added strict no-fallback Solkoff coverage, legacy snapshot/REAL migration preservation and rollback checks, per-competition history recovery, plain league-phase parsing, and delayed-response browser regressions. One existing competition-scope fixture now explicitly seeds EL opponent standings; no unscoped production fallback was retained to satisfy its old CL-only setup. Unrelated pre-existing edits were preserved.

Fresh checks: targeted backend plus full frontend/deploy selection initially passed 256 tests; an additional competition-keyed INTEGER-to-REAL migration regression passed subsequently. Full backend run after that addition: 215 passed, 18 failed, matching original documented failure groups (live server prerequisites and stale mocks/fixtures). No tests hidden or marked skipped. Feed entitlement and real EL/UCL data completeness remain unverified without authenticated provider access.
