# Historical Archive Repair Implementation Plan

**Goal:** Repair 2024/25 and 2025/26 historical Champions League imports so each league table contains 36 clubs playing eight games, with accurate points, opponent PPG and dates.

**Authorization:** User approved production repair. Includes necessary parser fixes, offline staging, scoped data replacement, backup, app-only immutable redeployment and verification. No git push, broad refresh, global historical rewrite, Caddy change or unrelated cleanup. Commit only if needed to extend the already-authorized release workflow; preserve all unrelated working-tree changes.

**Architecture:** Correct import parsing at source, not presentation. Rebuild from checksum-pinned local source files using exact canonical club identities. Validate complete staged seasons before a single transaction replaces only CL fixtures inside the two approved August-to-August windows. Never fuzzy-merge clubs during repair. Preserve current-season and other-competition fixtures byte-for-byte, retain old team rows, and avoid rewriting shared clean team metadata.

**Verification mode:** TDD for parser/matcher regression cases, tested for repair pipeline and operational gates. Python commands use `uv run`. Work in current checkout; no worktrees or reset.

## 1. Parser regression tests and fixes
- Modify `backend/football_txt_parser.py`; tests in `tests/backend/test_historical_parser_repair.py`.
- `parse(content, competition, season)` resets active date and matchday. Recognize date headers sequentially; every match inherits nearest active date without a fixed lookback. Explicit date/year and season-relative headers work. Stage transitions must not accidentally inherit an invalid date from a different block.
- Parse colon and dot kickoff formats, including lines inheriting kickoff time. No kickoff prefix reaches returned `teams`, home/away names or matching. Preserve country-suffix handling.
- Emit actual league matchdays 1..8 and playoff leg matchdays separately from stage. Distinguish league, playoff, R16, QF, SF and final using real source headers.
- Repair must use match scores, not shootout tally; inspect penalty/AET lines before claiming whole-season knockout correctness. Regression coverage required for real representative penalty line if parser currently misreads it.
- Tests include two adjacent date headers, 18 fixtures under one date, season rollover, parser reuse, kickoff formats, exact cleaned names, real stage headers.

## 2. Identity and repeat-import safety
- Modify `backend/team_matcher.py` only enough to strip kickoff artifacts before normalization and storing new names. Test polluted/clean variants map consistently. Do not globally rewrite existing team rows or expand fuzzy matching.
- Modify `backend/data_service.py` to use deterministic negative INTEGER-range fixture IDs based on competition, season, canonical participants, actual date and stage/leg. Include collision detection that refuses conflicting identity instead of overwriting another fixture. Keep old unrelated import behavior unchanged; do not automatically reimport every season.
- Preserve explicit parsed matchday. Date-less repaired source must fail validation; do not fabricate dates in repair. Test repeated import across different Python hash seeds yields identical IDs/no extra fixtures.

## 3. Offline scoped repair tool
- Create `backend/historical_repair.py` with dry-run default and explicit apply mode. Accept DB path, local source paths/checksums and approved years (2024, 2025 only). No network inside apply, no API client or scheduler startup.
- Build canonical map from exact cleaned source names and reviewed existing clean team identities. Prefer trusted positive IDs where unambiguous; otherwise reuse clean synthetic IDs or create one stable clean node if only polluted nodes exist. Ambiguity is an error requiring explicit reviewed mapping, not a fuzzy guess. Never delete old team nodes or mutate current-team metadata.
- Stage all 189 fixtures per completed season: 144 league, 16 playoff, 16 R16, 8 QF, 4 SF, 1 final; names/date/round/matchday explicitly parsed. If source differs from these expected complete-season invariants, stop.
- Validate 36 league identities, eight matches each, 144 unique league fixtures, correct points/goals and dates, no time prefixes, source totals, no self-matches/collision, and correct stage totals. Independently compute table from source names and compare reconstructed API schema standings and breakdowns.
- Dry-run reports planned ID remaps, fixture replacements, team additions, source hashes, before/after invariants and protected-table fingerprints. Apply begins transaction only after all validation, deletes only matching CL/year windows, inserts staged rows and necessary clean team additions, verifies fingerprints and commits. On failure rollback transaction; no partial seasons. Idempotent second apply must yield identical table contents.
- Tests in `tests/backend/test_historical_repair.py`: faithful reproduction, scoped replacement, ambiguous mapping refusal, failed validation leaves DB unchanged, no current/other-comp mutations, stable IDs, repeated application.

## 4. Production gates
- Repair acceptance exposed a required integration fix in `backend/playoff_analyzer.py::get_pairs_by_stage`: explicit round/stage metadata must override matchday fallback, and final must not match quarter/semi-final substrings. Add TDD regression tests in `tests/backend/test_knockout_stage_scope.py` using repaired historical shapes; verify stage tie counts 8/8/4/2/1 for both pinned seasons. Review this focused fix and rebuild candidate before retrying repair; prior failed attempt was safely restored from its fresh snapshot.
- Use fresh protected snapshot and offline candidate-copy rehearsal. Store diagnostics/backups under private directories, never commit DB/source secrets. Source fixtures can be checked in if public and proportional; source URLs, commit and SHA256 must be recorded.
- Independent code review must approve repair isolation and rollback before production writes.
- Follow `DEPLOYMENT.md` with latest authoritative record `docs/releases/2026-10-12-season-switching-release.md`: preserve protected active override and existing data bind, secrets, loopback and Caddy method restriction. Use immutable source bundle whitelist and candidate image; smoke on separate temporary DB/port before downtime.
- Gracefully stop UCL alone, confirm no writers, snapshot complete data directory, copy to existing private off-host backup root and verify checksum/extraction/read-only DB. Rehearse candidate repair on a fresh snapshot copy and compare all protected fingerprints before touching production.
- Apply already-reviewed repair offline with no network while UCL stopped, verify all invariants and protected fingerprints, then restart candidate via pinned image-only override. Preserve rollback image and untouched backup. On any failure preserve failed data, restore verified backup safely and restart pinned prior release; independent recovery actions must not suppress each other.
- Live acceptance: both archives 36 teams/eight played, points/goals/source consistent, breakdowns agree, knockout stages correct; current standings preserved, selectors/reload/details desktop/mobile correct; initialized health/restarts zero, Caddy hash/security and neighboring services unchanged. No administrative POST or broad historical sync.

## 5. Evidence and documentation
- Focused checks: `uv run pytest tests/backend/test_historical_parser_repair.py tests/backend/test_historical_repair.py tests/backend/test_seasons.py tests/backend/test_solkoff_scope.py tests/frontend -q`. Run touched matcher/import tests where relevant; separate known 18 baseline backend failures from new failures.
- Update `docs/seasons.md` for repaired archive semantics; create immutable release/repair record in `docs/releases/2026-10-13-archive-repair.md` with source hashes, checks, snapshots, protected fingerprints, new image and rollback paths. Do not silently claim skipped UI/monitoring checks.

## Acceptance
Both archived league tables have exactly 36 clubs, eight games each; points and Solkoff derive from complete canonical fixtures, dates match pinned source, no penalty-score misuse in repaired ties. Current-season and non-target fixtures and protected tables remain unchanged at offline repair boundary. Repair is transactional, repeatable and backed by verified restoration data. Import fixes prevent recurrence.
