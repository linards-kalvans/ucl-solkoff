# Opponent season record release — 2026-10-15

## Outcome

Each opponent's own league-phase **season W/D/L** is now shown beside its season PPG
in the Solkoff detail panel, with the head-to-head match count labelled explicitly
(`H2H`) and match scores kept in the selected club's perspective. The count comes
from the opponent's own standings row (never a tally of the head-to-head results),
and a missing count renders as `unavailable` rather than an invented `0W 0D 0L`.
Both the live current-season path and the reconstructed historical path are covered.

Deployed and verified in production at https://ucl.mplytics.eu via the full
phase-gated cutover procedure. This is an **app-only, image-only Hetzner rollout —
no Caddy change, no reload.** No git push was performed.

- Release: `20261015T133605Z-54564e7-5e0f273a1d7f`
- Code commit: `54564e76d8a9a0774dec3a4cb8de7d65b2a12619` (54564e7)
  (`fix: show opponent season record in details`; parent `10599d6`)
- Plan: `docs/superpowers/plans/2026-10-15-opponent-record.md`
- Previous live release: `20261015T065341Z-4f6383f-81e592ac3ff0`
- Image: `ucl-solkoff:20261015T133605Z-54564e7-5e0f273a1d7f`, ID
  `sha256:ba9d26a54f0427fd354afaee4828acd975b5880343f71f0515de8e480acd9497`
- Previous live image ID: `sha256:4eb37f0d61a8b5b9ae1f1b1ed10373c7452955078c6350d86c81e90cfe0d10a0`
- Postdeploy image exact / healthy / restarts 0
- Compose project/service `ucl-solkoff` / `ucl-solkoff` unchanged
- Base manifest, env, bind `/opt/ucl-solkoff/data` → `/app/data`, and loopback
  `127.0.0.1:8003` → container `8000` unchanged
- No Caddy reload required (app-only redeploy); env/Caddy/Compose hashes unchanged
- 14 neighboring containers unchanged; old images retained, no pruning
- TLS verified; public method restriction preserved (harmless probe GET → 404,
  POST → 403; no admin `POST`)

## Source bundle

Source snapshot is the `git archive HEAD` whitelist only (`Dockerfile`,
`pyproject.toml`, `uv.lock`, `backend/`, `frontend/`). `Dockerfile`,
`pyproject.toml`, and `uv.lock` are **byte-identical to the previous live**
release — the runtime diff vs prior live is **exactly the three named files below**:

| File | Change |
|---|---|
| `backend/main.py` | modified — appended `won`/`drawn`/`lost` to both home and away opponent queries and to both opponent objects, preserving existing score column indexes. Counts come from the opponent standings row, not head-to-head results. |
| `backend/season_service.py` | modified — `build_breakdown` populates `won`/`draw`/`lost` from the reconstructed opponent standing; preserves season/competition scope. |
| `frontend/app.js` | modified — `renderDetailContent` renders the supplied season record (`Season: …`) instead of counting `matches[].outcome`; labels the head-to-head count `H2H`; adds a "Scores shown from <club>'s perspective" tooltip; renders `unavailable` when a count is missing while real zeroes still render. |

| Item | Value |
|---|---|
| Source bundle SHA256 | `5e0f273a1d7fd7421402293ac8b0e56b6e4e7775a9e7aadee8998bb3ef0b6d2f` |
| Image tag | `ucl-solkoff:20261015T133605Z-54564e7-5e0f273a1d7f` |
| New image ID | `sha256:ba9d26a54f0427fd354afaee4828acd975b5880343f71f0515de8e480acd9497` |
| Retained prior live image | `sha256:4eb37f0d61a8b5b9ae1f1b1ed10373c7452955078c6350d86c81e90cfe0d10a0` (`ucl-solkoff:20261015T065341Z-4f6383f-81e592ac3ff0`) |

## Consistent backup

UCL alone was gracefully stopped using the pinned live active override before
cutover; no other DuckDB writers active. The complete bind was archived while all
writers were stopped. Server and off-host checksums match; the live data directory
was not replaced, deleted, or restored.

| Item | Value |
|---|---|
| Protected server snapshot | `/opt/ucl-solkoff/releases/20261015T133605Z-54564e7-5e0f273a1d7f/data-before-apply.tar.gz` (root, mode 0600) |
| Private off-host | `/home/linards/.local/share/ucl-solkoff/backups/data-before-apply-20261015T133605Z-54564e7-5e0f273a1d7f.tar.gz` (mode 0600) |
| SHA256 | `0b3c4ea78f0d337cdc8b1dde90c80d56d8db02ec297c3d3d1641645b5fbc6ffa` |

## DuckDB read-only validation (via candidate image)

Extracted backup validated read-only with the candidate image's `uv` runtime; the
remote extracted copy and a local extraction both matched the stopped-source row
counts.

| Table | Rows |
|---|---:|
| teams | 509 |
| matches | 3245 |
| standings | 36 |
| solkoff_coefficients | 509 |
| elo_ratings | 509 |
| team_mappings | 833 |
| api_cache | 4 |

## Isolated candidate smoke (before downtime)

- `--network none` dependency check: `duckdb`, `fastapi`, `httpx`, `apscheduler`
  present via the uv project venv; **Playwright absent** (dev-only, not installed);
  scheduler disabled without an API key (no sync attempted).
- Health smoke on a separate loopback port `18005`, temporary empty DB, no API key:
  health `{"status":"healthy","database":true}`; root, `/app.js`, `/styles.css`
  served; served `app.js` contained both the new `seasonRecordPresent` marker and
  the preserved `pairResultsHtml` marker.
- No outbound sync/download; no production database used.

## Production acceptance (verified)

- Health `{"status":"healthy","database":true}`; image ID exact; restart count 0;
  release metadata `deploy:deploy` mode 600, phase `production_verified`.
- Identity hashes unchanged (no Caddy change, no reload): Caddy
  `912002664f9de42b10edb268c2cb6a9ee74bf900ea3388c368d8f48f52c02b23`, base compose
  `6f789ae189a52c0020771617a2552b91f31ad4d12bb54c0cb529e2831fa2646f`, env
  `7552d39c8d60fad5ca7afd6648bd06cd68f0bd27fdb6850de6f8391a2300daf4`.
- Existing public method policy preserved: harmless probe GET → 404, POST → 403;
  no admin endpoint invoked. 14 neighboring containers unchanged.
- **Opponent season record** across live + reconstructed historical detail payloads,
  every team checked: current season 36 teams / 72 opponents / **72 matched**;
  2024 36 teams / 288 opponents / **288 matched**; 2025 36 teams / 288 opponents /
  **288 matched**. For every opponent whose standings row exists, the panel's
  `won`/`draw`/`lost` equals that opponent's own `won`/`drawn`/`lost` standings
  values; `matchesPlayed` equals the listed head-to-head matches; each match score
  keeps the selected club's perspective (`outcome` consistent with
  `teamScore`/`opponentScore`).
- **Liverpool FC present and cross-checked**: 2024 `7W 0D 1L`, 2025 `6W 0D 2L`,
  current `2W 0D 0L`, with all opponent records and selected-club H2H scores
  matching their standings.
- Knockout results/archive preserved: historical stage counts for 2024 and 2025 each
  **8 / 8 / 4 / 2 / 1** (Knockout Round Play-off, Round of 16, Quarter-finals,
  Semi-finals, Final); all-stage aggregate **23** each; every finished non-final
  pair exposes 2 correctly oriented legs, finals expose 1 leg with a null aggregate;
  archived probabilities remain `historical_no_data`.
- Archived standings exactly preserved (36 teams each; identical ids, points,
  played vs the pre-cutover baseline); current-season team set and count unchanged.
- Parent independently verified the new image + metadata + health + backup hashes
  via SSH after the cutover.

## Live browser acceptance (public GET-only)

Headless Chromium against https://ucl.mplytics.eu (no dev server, no refresh click,
no admin `POST`): selected season 2024, expanded the league table detail panel and
confirmed the rendered record and perspective directly.

- Detail panel showed `Season: 5W 1D 2L` for the first opponent, `1 H2H match`, and
  the match-results tooltip `Scores shown from Liverpool FC's perspective` with the
  first result `4-0`.
- No page errors. All requests to `ucl.mplytics.eu` were `GET` only. (The app's own
  analytics beacon issues a `POST` to `analytics.mplytics.eu`; that is unchanged
  pre-existing app behaviour and not a UCL route.)
- Screenshot: `/tmp/opencode/ucl-opprecord-detail-1440.png`.
- Overview and knockout views still render after the cutover.

## Caddy and neighbors

- No Caddy change; `/etc/caddy/Caddyfile` SHA256 unchanged; no reload.
- Public method restriction unchanged. Harmless probe GET 404 / POST 403; no admin
  route invoked.
- 14 neighboring containers unchanged (only the UCL container ID changed, as
  expected on recreate).

## Monitoring limits (recorded, not hidden)

- No Loki delivery verification in this rollout; no external log sink was exercised.
- UCL Caddy access logging remains absent (preexisting, unchanged).
- No git push performed (commit is local only).
- Current-season standings are compared by team id/count and per-opponent record
  only, because the startup autosync legitimately updates current-season rows.

## Durable release and rollback identity

Protected metadata `/opt/ucl-solkoff/active-release.json` (mode 0600,
`deploy:deploy`, phase `production_verified`) now records this release, its image
and source hashes, and the full backup. The prior metadata is preserved unchanged at
`/opt/ucl-solkoff/releases/20261015T133605Z-54564e7-5e0f273a1d7f/active-release.previous.json`.

- Active override: `/opt/ucl-solkoff/releases/20261015T133605Z-54564e7-5e0f273a1d7f/compose.release.yml`
  (image `ucl-solkoff:20261015T133605Z-54564e7-5e0f273a1d7f`).
- Rollback override: `/opt/ucl-solkoff/releases/20261015T133605Z-54564e7-5e0f273a1d7f/compose.rollback.yml`
  (points at `ucl-solkoff:20261015T065341Z-4f6383f-81e592ac3ff0`).

Every future recreate must include the pinned active override; the base manifest
alone still names the old `latest` image. An image rollback must retain the public
method restriction; do not reopen administration automatically. Data rollback uses
the fresh snapshot `data-before-apply.tar.gz`. No secrets were copied into the image
or printed. **No rollback was required.**

## Local verification

- Fresh focused checks: `uv run pytest tests/backend/test_opponent_record.py
  tests/frontend/test_opponent_record.py -q` → **9 passed** (4 backend + 5 frontend).
- `node --check frontend/app.js` clean; `git diff --check` clean.
- Commit staged path-restricted to the six session files only; unrelated staged
  files were not included.
- The full backend suite was **not** re-run for this scoped change; only the focused
  opponent-record tests and the JS/diff checks above were executed.

## Remaining work

- None within the approved opponent-record scope.
- Backend test backlog and monitoring route-cleanup tests unchanged.
- UCL Caddy access logging; Loki delivery re-verification; nightly backup/restore
  tooling repairs remain open.
