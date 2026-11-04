# Opponent Record Implementation Plan

**Goal:** Show each opponent's league-phase season W/D/L beside its season PPG, while retaining match scores from the selected club's perspective.

**Architecture:** Add `won`, `draw`, and `lost` to opponent objects in both live and historical detail responses. Read live counts from the same standings row as points/played and historical counts from reconstructed standings, so record and PPG have identical scope. Do not change match outcome semantics or Solkoff calculations.

**Verification mode:** tested. Focused backend and frontend regressions cover both response paths and rendering; no mandatory tests-first ordering.

## Files and acceptance criteria

- `backend/main.py`, `get_solkoff_details`: extend both home/away opponent queries and opponent objects with `won`, `draw`, `lost`. Preserve existing score indexes by appending columns. Counts must come from opponent standings, not head-to-head results.
- `backend/season_service.py`, `build_breakdown`: populate identical fields from reconstructed opponent standings. Preserve season/competition scope.
- `frontend/app.js`, `renderDetailContent`: render supplied opponent season counts instead of counting `matches[].outcome`. Label counts `Season:` and match count explicitly `H2H`; label score perspective using selected club name, without changing score orientation. Missing counts must show unavailable rather than invented zeroes; real zeroes must render.
- Focused tests under `tests/backend/` and `tests/frontend/`: use deliberately different season and head-to-head records; check live home/away paths, historical records, season W/D/L display, unchanged selected-club scores, explicit H2H label, zero and unavailable records. Follow existing fixtures and avoid unrelated changes.

## Execution

1. Inspect immediate callers, standings field names, existing fixtures and checks.
2. Implement only listed response/render changes and focused regressions.
3. Run focused tests using `uv run pytest`; run JS syntax check if available. Report exact output and any blocked check.
4. Read-only code review; fix critical defects and re-run affected checks.
5. Share critique URL filtered to files edited in this task. Do not commit, push, deploy, or create worktrees. Preserve all pre-existing working changes.

## Documentation scope

This plan only; no API documentation exists requiring a separate update for additive response fields.
