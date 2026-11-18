# Next Opponent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the next confirmed league-phase opponent and its current PPG in live League standings.

**Architecture:** Enrich the existing live standings response with one batched, read-only fixture query, using the same standings snapshot for opponent PPG. Render supplemental text inside the existing team cell, avoiding any effect on ranking, search, or requests.

**Tech Stack:** FastAPI, DuckDB, vanilla JS/CSS, pytest, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-18-next-opponent-design.md`

## Global Constraints

- Use stored schedule and standings only. No schema changes, extra browser requests, probability model, inferred opponents, schedule aggregation, or ranking changes.
- Archived tables and Knockout stay unchanged.

## Review Focus

- Stale scheduled status after kickoff must not win over a true future match.
- Invalid dates and missing team identities must not break standings or produce an opponent.
- Postponed matches, qualifiers, knockouts, and other competitions must not leak into the result.
- No opponent stats is distinct from zero PPG; keep the fixture in either case.
- Opponent text is untrusted and must be escaped; missing fields from old/archived responses render nothing.

### Task 1: Attach next fixtures to live standings

**Files:** `backend/season_service.py` (batched lookup), `backend/main.py` (live response), `tests/backend/test_next_opponent.py` (API integration).

**Interfaces:** `next_fixtures(db, year: int, competition_id: str, now: datetime) -> Dict[int, Dict[str, Any]]` in `season_service.py` returns mapping to `{opponentId: int, opponentName: str, venue: 'home' | 'away'}`. Current `/api/standings` adds `nextFixture: null | {opponentId, opponentName, venue, opponentPpg: number | null, opponentPlayed: integer | null}`. Archived response remains unchanged.

- [ ] Add API tests using real test DB and a fixed UTC clock or relative UTC fixtures: assert first eligible fixture, both venue directions, PPG `2.0` from `5` matches (`10` points), zero PPG for a played winless opponent, null PPG for missing/zero-game standings, and `nextFixture is None` for no eligible fixture. Include stale, invalid/missing date, unresolved identity, postponed, cross-season, cross-competition and non-league decoys; assert archived response has no field and rank/strength values are unchanged.
- [ ] Run `uv run pytest tests/backend/test_next_opponent.py -q`; verify expected failure from missing `nextFixture`.
- [ ] Implement selection and response. Use explicit `LEAGUE_STAGE`, `SCHEDULED` or `TIMED`, two distinct teams with nonblank names via joins, season bounds, and strictly future UTC kickoff. Compare actual times (not lexicographic text), treating naive stored times as UTC; date-only values are eligible only on later UTC days, and malformed values are skipped. Order by UTC kickoff then match ID. Use current standings rows only for opponent points/played; publish no PPG for zero/missing games, or missing points. Zero points with games is valid PPG. Avoid per-team DB queries.
- [ ] Re-run focused tests and existing competition/season API checks.

### Task 2: Show fixture context in League rows

**Files:** `frontend/app.js`, `frontend/styles.css`, `tests/frontend/test_league.py`.

**Interfaces:** Consume optional `team.nextFixture` as defined above in `renderTable`. Render inside `.team-cell`, below `.team-name`, with `.next-opponent` class, without new columns or handlers.

- [ ] Add Playwright coverage for `Next: Club X · Away · 2.00 PPG from 5 matches`, no-PPG `Next: Club Y · Home`, singular `1 match`, valid `0.00 PPG`, no text for absent/null fixture, and escaped opponent text. Assert search still looks only at team names and sort behavior remains covered by existing tests.
- [ ] Run `uv run pytest tests/frontend/test_league.py -q`; verify expected failures for missing context.
- [ ] Render escaped opponent name, venue label, two decimal PPG and singular/plural sample, omit unavailable stat. Keep text readable and wrapping on mobile without changing existing crest, team name, click-to-expand, or column count.
- [ ] Run `uv run pytest tests/frontend/test_league.py -q`, then `uv run pytest` and `git diff --check`. No commits or deployment requested.
