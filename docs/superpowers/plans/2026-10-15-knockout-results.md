# Knockout Results Implementation Plan

**Goal:** Show stored knockout leg scores and correctly oriented aggregate scores on pair cards, including archived seasons. Keep archived probabilities unavailable.

**Scope:** Backend pair payloads, knockout cards and overview previews, focused regression tests, and season documentation. No ingestion changes, probability model changes, database writes/migrations, deployment, commits, or worktrees.

## Design and contract

- Extend existing pairs additively, preserving team1/team2 identity and existing metadata. Both pair-building paths must expose the same results contract.
- Add `legs`: chronological fixtures containing `matchId`, `homeTeamId`, `awayTeamId`, `homeScore`, `awayScore`, `status`, and `date`. Nullable scores remain null; never turn missing scores into zero.
- Add `aggregateScore`: `{team1: int, team2: int}` only for a completed two-leg tie with both finished legs and both scores available; otherwise null. Orient every score using stable pair team IDs, not latest-leg home/away order.
- Single-match finals display their match result, not an aggregate. Do not infer advancement, shootout outcomes, or winners from aggregate draws; stored goals do not encode penalty winners.
- Preserve existing grouping and season/stage constraints. Use one small shared helper to attach leg results and aggregates across the two pair-building paths, avoiding duplicated orientation rules.
- Frontend uses one escaping-safe results formatter for full cards and overview previews. Full cards show leg scores with home/away names and aggregate when available; previews show compact aggregate or single-match score. Scheduled/unscored fixtures never show fabricated scores. Explicitly label aggregates and leg results so partial ties are not represented as complete.

## Files and units

1. `backend/playoff_analyzer.py`: append score columns to both pair queries, retain each selected fixture, and attach the additive results contract before return. Preserve historic probability guard and public method signatures. Inspect immediate callers and existing mock row fixtures before changing positional selections.
2. `frontend/app.js`: add shared results formatter near pair rendering utilities; consume it in `displayPlayoffPairs` (or actual card renderer) and `miniMatchHtml`. Follow existing DOM/escaping/accessibility patterns.
3. `frontend/styles.css`: add only minimal score layout styles if required; first inspect actual stylesheet used by the app.
4. `tests/backend/test_knockout_results.py`: temporary real DuckDB regression cases for both query paths, home/away reversal, chronological legs, final, missing scores, incomplete ties, and season isolation.
5. `tests/frontend/test_knockout_results.py`: follow existing browser fixture/test style; cover rendered leg scores, aggregate, final, unavailable probabilities, and absence of fabricated scheduled scores. Exercise actual JS rendering with deterministic mocked data rather than source-string assertions where feasible.
6. `docs/seasons.md`: document result/aggregate semantics and retain archived probability limitation.

## Verification mode: tested

- First add focused regression coverage; a red run is useful but not mandatory in tested mode.
- Run `uv run pytest tests/backend/test_knockout_results.py tests/backend/test_seasons.py tests/backend/test_knockout_stage_scope.py -q`.
- Run `uv run pytest tests/frontend/test_knockout_results.py tests/frontend/test_seasons.py -q` plus relevant existing knockout tests if compatible with their server fixtures.
- Inspect JS syntax and filtered diff. Review by a separate read-only code-reviewer; address critical issues and rerun relevant tests.
- Report skipped/unavailable checks explicitly. Do not claim deployment or live-site changes.

## Acceptance criteria

- Archived two-leg fixtures expose both real scores, retain chronological order, and total into stable team1/team2 aggregate.
- Two-leg aggregates only appear when both legs are finished and scored; null/missing scores and unfinished legs never become invented results.
- Single-match final shows its score without aggregate label. Drawn aggregates do not imply a winner.
- Both pair APIs and frontend card/preview consumers handle additive fields while preserving existing dates, metadata, season isolation, and historical probability unavailability.
- Existing focused season and stage regressions remain green; unrelated working-tree changes remain untouched.
