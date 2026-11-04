# UCL Strength Lab Implementation Plan

> For agentic workers: use subagent-driven-development. Tests must be written and verified red before implementation. No commits, pushes, deployments, or worktrees without explicit user request.

**Goal:** Integrate the approved redesign into the production vanilla-JavaScript frontend without losing existing analytical features or changing backend calculations.

**Architecture:** Reuse existing APIs and detailed rendering. Share raw standings and per-stage pair caches between Overview, League, and Knockout. Preserve existing detail caches, guard stale asynchronous responses, and replace the analysis modal with a responsive sidebar.

**Tech stack:** Vanilla JavaScript/HTML/CSS, existing FastAPI backend, pytest with development-only Python Playwright tests.

## Approved design and scope

The user approved the requirements and this plan in Discord. Implement functional integration rather than copying the mock prototype. Adopt UCL Strength Lab branding and Overview as default. Keep redesign/ unchanged as reference. No framework migration, backend/model changes, database migration, or deployment. Work in the current checkout on a feature branch; do not create a worktree.

### Routing and state

- Support #overview, #league, #knockout. No recognized hash opens Overview.
- Preserve #knockout-playoff, #round-of-16, #quarter-final, #semi-final, #final. Each opens Knockout with its stage selected.
- Stage API only sets initial selected stage; it must never replace the primary view or override an explicit hash/user stage choice.
- Use ROUND_OF_16 if current-stage returns LEAGUE or fails.
- Retain standingsData, solkoffDetailsCache, playoffAnalysisCache, currentPairsList, currentPairIndex where useful. Add activeView, selectedStage, pairsByStage, selectedPairKey and request-generation counters.
- Ignore stale stage, selection, or refresh responses. Share loaded data across views without redundant requests.

### Data truth and Overview

- Use the existing strengthScore numeric scale. Only bars are normalized to the largest value in the full standings dataset. Display actual numeric values, never mock 0-100 scores.
- Qualification badges use official position, never current sort order. No qualification divider rows when sorting alternate metrics.
- Label normalized two-team predictions 'Win probability — excluding draws', not advance probabilities. Preserve method and insufficient-data explanations. Missing probabilities are unavailable, not invented 50/50.
- Remove fake trends, season and backend freshness claims. Show 'Standings loaded at' with successful browser fetch time.
- Three Overview summary cards: maximum strengthScore team, maximum solkoffCoefficient team, and most balanced selected-stage pair with meaningful predictions. Exclude explicit no-data/default probability methods from balanced tie selection.
- Top 10 strength ranking, top 10 schedule ranking, up to three selected-stage pairs in API order. Call previews 'Knockout ties', not upcoming. Empty and unavailable states must be explicit.
- Methodology states Solkoff is average opponent PPG and Strength=(Points% × Solkoff)/100.

## Files and interfaces

### frontend/index.html

- Adapt redesign shell; brand/title/description UCL Strength Lab. Three semantic primary view panels, Overview containers and league controls.
- Preserve standingsTable, standingsBody, refreshBtn, intro-banner, skip link and creator footer. Keep stage-specific pair container IDs and place stage tabs inside Knockout.
- Replace playoffAnalysisModal with persistent analysis sidebar. Use existing same-origin /styles.css and /playoff-styles.css assets.
- Acceptance: unique IDs, associated accessible tabs/headings, Overview default, no hardcoded prototype data.

### frontend/styles.css

- Adapt supplied dark-theme tokens, gradients, cards, responsive grids. Preserve skeletons, sort indicators, expanded opponent rows.
- Visible focus and reduced-motion styles. Refresh and browser load time remain available at every viewport.
- Table/stage strip can scroll internally; no page-wide horizontal overflow. Checkbox controls advanced columns; mobile CSS cannot contradict it.
- Acceptance: usable at 375, 768 and 1440 pixels, controls accessible, complete existing detail styling retained.

### frontend/playoff-styles.css

- Desktop cards and sidebar; narrow screens stack analysis below cards. Internal scrolling for historical tables.
- Preserve all existing analysis sections, Elo/method explanations, historical/common-opponent content. No modal overlay or body-scroll lock.
- Acceptance: loading/error/empty/selected sidebar states, detailed content readable on mobile.

### frontend/app.js

Routing interfaces:

```js
resolveRoute(hash) // => {view, stage: null|string}
activateView(view, {updateHash = true} = {})
activateTab(tabId) // stage permalink compatibility
```

Derived data interfaces:

```js
normalizeWinProbability(probability) // => {team1, team2}|null
getVisibleStandings(data, query, orders) // => sorted copy
buildOverviewSummary(standings, pairs) // => summary card data
renderOverview()
```

- Keep raw standings distinct from filtered/sorted rows; case-insensitive search.
- Preserve multicolumn header sorting with descending/ascending/third-click removal. Sort selector resets primary order to selected metric descending; header customization sets selector to Custom.
- Preserve team/Solkoff expansion, calculation content and valid expanded state across filter/toggle rendering. Use official-position qualification badges.
- Navigation supports ARIA selected state, roving tab focus, browser back/forward.

Knockout interfaces:

```js
loadKnockoutPairs(stage, tabId)
showPlayoffAnalysis(team1Id, team2Id)
displayPlayoffAnalysis(analysis, team1Id, team2Id)
closePlayoffAnalysis()
navigateToPair(index)
```

- Modify existing detail renderer's target, not its analytical meaning. Selected-card state, previous/next controls, scoped keyboard navigation, labeled close action and focus return.
- Overview preview opens matching stage and pair. Old requests cannot overwrite newer selection.
- Use real crest images with fallback initials. Escape/render API text safely, restrict crest URLs to HTTP(S), add no new inline handlers.

Refresh interfaces:

```js
refreshData()
updateLastUpdated()
```

- One POST while disabled. Invalidate standings/pair/opponent/analysis caches on success, reload standings and selected-stage pairs, preserve route/search/sort/toggle, guard old responses.
- Failures are inline; button restored in every outcome; show partial reload failures honestly. Browser load timestamp updates only on successful standings fetch.

### Tests/dependencies

Create tests/frontend/conftest.py and test_navigation.py, test_overview.py, test_league.py, test_knockout.py, test_refresh.py, test_accessibility_layout.py.

Add Python Playwright to development dependencies in pyproject.toml and resolve uv.lock only after tests are accepted. Test engineer may only write test files; PM may establish dependency setup before test execution. No production browser dependency.

Serve static frontend through a test fixture, not FastAPI lifespan. Intercept all /api/ calls deterministically. No external requests, scheduler startup, live scraping, or database writes.

Fixtures: differing strength/Solkoff leaders, positions differing from strength order, missing/broken crests/probabilities, default probability methods, empty stages, HTTP failures, delayed out-of-order responses, full real-schema Elo/common-opponent/history analysis.

Test acceptance:

1. Navigation defaults, old hashes, back/forward, stage resolution race and keyboard selection.
2. Overview leaders/top lists, true scores, stable bar normalization, previews and missing-data states.
3. League search, selector/header multi-sort, advanced toggle, badges, opponent expansion and preserved state.
4. Knockout stage fetch/cache/selection, full analysis sections, prev/next, close focus, stale response rejection.
5. Refresh one POST/disabled UI/cache invalidation/preserved state/failure/partial failure.
6. Layout at 375/768/1440, no body overflow, visible refresh, labels, keyboard focus, reduced motion.
7. Markup-like names are inert text; unsafe image URLs are rejected; broken images show initials.

Do not duplicate backend math tests, hit live services, or use brittle exact CSS/DOM snapshots. Manual visual comparisons against redesign supplement browser assertions.

Verification commands:

```sh
uv run playwright install chromium
uv run pytest tests/frontend -q
uv run pytest
```

Record backend baseline before changes. Existing test_main.py contains legacy assertions; report pre-existing failures separately, do not fix unrelated tests.

## Unchanged API contracts

- GET /api/standings: teamId, teamName, teamCode, teamCrest, position, played, won, drawn, lost, gf, ga, gd, points, solkoffCoefficient, strengthScore.
- GET /api/tournament/current-stage: {stage}.
- GET /api/knockout-pairs/{stage}: pair team1/team2/date/status/winProbability fields; inspect backend/playoff_analyzer.py for nested schema.
- GET /api/teams/{teamId}/solkoff-details: existing opponent breakdown schema.
- GET /api/playoff-pairs/{team1Id}/{team2Id}/analysis: reuse existing nested analysis schema and detail renderer.
- POST /api/refresh: existing refresh operation.

## Execution gates

- [x] Save plan, create feature branch without commit/worktree, capture baseline.
- [x] Test engineer writes tests. Verify coverage/spec compliance and genuine red phase, then quality. No implementation before test acceptance.
- [x] Software engineer implements shell/routing, Overview/League, Knockout/sidebar, refresh and accessibility. Run corresponding tests for each step.
- [x] Verify acceptance output, then independent code reviewer. Address CRITICAL findings, assess MINOR findings within scope, reject scope creep. Verify fixes.
- [x] Docs writer updates README after accepted implementation: branding/features, corrected PPG definition/formula, probability semantics, browser fetch time meaning, test setup.
- [x] Run final frontend/full suite, inspect mobile/desktop against redesign, publish critique diff filtered to all edited files.

Completion: scoped tests green, backend baseline regressions assessed, complete existing analytics accessible, visual checks done, no mock values/misleading labels, critical review findings resolved, documentation complete. Explicitly report failures/skips/limitations. No commit/push/deploy without request.

## Verification record

- Delivered in bounded increments after the first implementation agent hit its output limit without edits.
- Browser coverage expanded from the initial 90 to 105 tests, including test-harness corrections and review regressions. Frontend suite: 105 passed, no skipped cases.
- Full suite: 162 passed, 18 failed. All 18 failures match the pre-change backend baseline (live-server integration requirements and legacy unit assertions); backend files remain untouched.
- Independent review approved after fixing routing, cross-view errors, focus return, probability fallbacks and CSS declarations.
- Desktop and mobile screenshots use deterministic mocked fixtures, not current live tournament results. No page-wide overflow at 375/768/1440 pixels; detailed tables scroll internally.
- Runtime locked package versions unchanged; only development Playwright, greenlet and pyee added. README updated with metrics, navigation and test setup.
- No commit, push, deployment or worktree created. Branch: feat/ucl-strength-lab. Supplied redesign assets unchanged.
