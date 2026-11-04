# Season Switching Implementation Plan

**Goal:** Switch all app views between stored Champions League seasons from 2024/25 onward.

**Architecture:** Add an optional season-start-year query parameter to read APIs, defaulting to the current season. Reconstruct historical league tables and Solkoff from stored league-stage matches without overwriting current standings. Carry season through knockout queries and frontend request/cache state.

**Verification mode:** tested. Focused backend isolation tests and browser regression tests; no production changes, commits, or worktrees.

## 1. Backend season data
- Create `backend/season_service.py`: current-year resolution, validation, August-inclusive/August-exclusive bounds, stored-season discovery, league-table reconstruction, opponent breakdowns. Only configured CL competition; only 2024 onward through current season. Parameterized SQL.
- Modify `backend/main.py`: `GET /api/seasons` returns `{currentSeason, seasons: [{year, label}]}` newest first, includes current season even when empty. Optional `season: int` on standings, details, tournament stage, playoff pairs, stage pairs and pair analysis. Omitted season preserves existing current-season contracts. Reject unsupported year with 400; valid empty seasons return empty data rather than another season.
- Historical standings use FINISHED league-stage results, normal 3/1/0 points, goal difference and goals for tie-breaks, existing JSON fields, unique-opponent mean PPG for Solkoff and existing strength formula. Preserve current scheduled standings for current season.
- Modify `backend/playoff_analyzer.py`: optional season constructor context; constrain ties to selected competition and date window; do not reject historical match dates due to age. Historical common-opponent windows and Elo inputs must not use later matches; if historical probability cannot be computed faithfully, omit it explicitly rather than use present-day ratings. Never fetch current draws for archived seasons.
- No database migration or data collection on season selection. Existing historical import remains unchanged unless stage metadata prevents identifying new-format historical league matches; fix only required mapping if confirmed by inspection.

## 2. Frontend integration
- Modify `frontend/index.html` and `frontend/styles.css`: labeled native select in header controls, matching existing layout, usable on mobile and keyboard.
- Modify `frontend/app.js`: initialize from seasons endpoint, default current season; append selected year to every season-dependent GET. Season switch clears old standings, expanded details, pair analysis and knockout caches, then reloads all views. Generation/request identity guard prevents old responses overwriting new selection, including errors/loading cleanup. Reload retains selection. Loading and failures must not display data belonging to a different season.
- Avoid unrelated sort/navigation changes. Existing tests mocking APIs may need explicit seasons fixtures.

## 3. Verification and docs
- Add `tests/backend/test_seasons.py`: discovery excludes older format/other competitions; bounds/validation; two seasons with shared teams produce distinct standings, coefficients and details; historical knockout results survive age checks and exclude other seasons; omitted year defaults current; selection performs no writes/network fetches.
- Add `tests/frontend/test_seasons.py` and minimally update `tests/frontend/conftest.py` fixtures: default selection, switch all relevant requests, clear stale expansions/caches, retained selection on reload, slow response race, empty/error state, keyboard/mobile accessibility.
- Run `uv run pytest tests/backend/test_seasons.py tests/backend/test_solkoff_scope.py tests/frontend -q`; run additional touched backend tests as appropriate, report pre-existing failures separately. Use `uv run` for Python.
- Document season semantics in README or dedicated feature doc, not deployment files. Independent read-only code review follows implementation; fix blocking findings and re-run focused checks.

## Acceptance
Every visible view and detail belongs to selected year. Only new-format seasons offered. No current standings mutations, later-season leakage, stale-response mixing, public administrative POSTs, deployment, or unrelated cleanup. Existing uncommitted work remains intact.
