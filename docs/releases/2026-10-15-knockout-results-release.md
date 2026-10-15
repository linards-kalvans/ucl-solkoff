# Knockout results release — 2026-10-15

## Outcome

Stored knockout leg scores and correctly oriented aggregate scores are now shown on
pair cards (full cards and overview previews), including archived seasons, and archived
probabilities remain unavailable. Deployed and verified in production at
https://ucl.mplytics.eu via the full phase-gated cutover procedure. This is an
**app-only, image-only Hetzner rollout — no Caddy change, no reload.** No git push was
performed.

- Release: `20261015T065341Z-4f6383f-81e592ac3ff0`
- Code commit: `4f6383fe8c2704556f7d559d2e52bcbbd19dcee5` (4f6383f)
  (`feat: show knockout leg and aggregate scores`)
- Plan: `docs/superpowers/plans/2026-10-15-knockout-results.md`
- Previous live release: `20261013T140544Z-9b7466b-cf5d21eef94a`
- Image: `ucl-solkoff:20261015T065341Z-4f6383f-81e592ac3ff0`, ID
  `sha256:4eb37f0d61a8b5b9ae1f1b1ed10373c7452955078c6350d86c81e90cfe0d10a0`
- Previous live image ID: `sha256:f98384fe33b9cde9e0954475f9b7f1d9cbd74f4faa84fbd9ce75956ac05eca9a`
- Postdeploy image exact / healthy / restarts 0
- Compose project/service `ucl-solkoff` / `ucl-solkoff` unchanged
- Base manifest, env, bind `/opt/ucl-solkoff/data` → `/app/data`, and loopback
  `127.0.0.1:8003` → container `8000` unchanged
- No Caddy reload required (app-only redeploy); env/Caddy/Compose hashes unchanged
- 14 neighboring containers unchanged; old images retained, no pruning
- TLS verified; public method restriction preserved (harmless probe GET → 404, POST → 403; no admin `POST`)

## Source bundle

Source snapshot is the `git archive HEAD` whitelist only (`Dockerfile`,
`pyproject.toml`, `uv.lock`, `backend/`, `frontend/`). `Dockerfile`,
`pyproject.toml`, and `uv.lock` are **byte-identical to the
previous live** release — the runtime diff vs prior live is **exactly the four named
files below**:

| File | Change |
|---|---|
| `backend/playoff_analyzer.py` | modified — appended `home_score`/`away_score` to both pair queries, retained each selected fixture tagged with its determined stage, and attached the additive results contract (`legs` + `aggregateScore`) via shared helpers (`_fixture_from_match`, `_attach_results`, `_retained_fixtures`). Preserved the historic probability guard and public method signatures. |
| `frontend/app.js` | modified — added an escaping-safe shared results formatter (`pairResultsHtml`, `miniResultsHtml` plus `isScoreValue`, `hasBothScores`, `normalizeAggregate`, `legTeamScores`, `pairTeamName`); consumed in full-card rendering and the compact preview. |
| `frontend/playoff-styles.css` | modified — added minimal full-card score layout (`.pair-results`, `.pair-leg`, `.leg-side`, `.leg-tag`, `.leg-score`, `.pair-aggregate`, `.aggregate-label`, `.aggregate-score`). |
| `frontend/styles.css` | modified — added compact preview styles (`.mini-result`, `.mini-result .aggregate-label`). |

| Item | Value |
|---|---|
| Source bundle SHA256 | `81e592ac3ff0dab2353576b8a606ac4fc9878e1faea6ca402749a699cd3a3ecc` |
| Image tag | `ucl-solkoff:20261015T065341Z-4f6383f-81e592ac3ff0` |
| New image ID | `sha256:4eb37f0d61a8b5b9ae1f1b1ed10373c7452955078c6350d86c81e90cfe0d10a0` |
| Retained prior live image | `sha256:f98384fe33b9cde9e0954475f9b7f1d9cbd74f4faa84fbd9ce75956ac05eca9a` (`ucl-solkoff:20261013T140544Z-9b7466b-cf5d21eef94a`) |

## Consistent backup

UCL alone was gracefully stopped using the pinned live active override before cutover;
no other DuckDB writers active. The complete bind was archived while all writers were
stopped.

| Item | Value |
|---|---|
| Protected server snapshot | `/opt/ucl-solkoff/releases/20261015T065341Z-4f6383f-81e592ac3ff0/data-before-apply.tar.gz` (root, mode 0600) |
| Private off-host | `/home/linards/.local/share/ucl-solkoff/backups/data-before-apply-20261015T065341Z-4f6383f-81e592ac3ff0.tar.gz` (mode 0600) |
| SHA256 | `6ab7572eb2a63ead28deababdfa46560536018ed4b20db10cd4da2fdf8fe6498` |

Server and off-host checksums match. The live data directory was not replaced, deleted,
or restored.

## DuckDB read-only validation (via candidate image)

Extracted backup validated read-only with the candidate image's `uv` runtime:

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

- `--network none` dependency check: `duckdb`, `fastapi`, `httpx`,
  `apscheduler` present via the uv project venv; **Playwright absent** (dev-only, not
  installed); scheduler disabled without an API key (no sync attempted).
- Health smoke on a separate loopback port `18004`, temporary empty DB, no API key:
  health `{"status":"healthy","database":true}`; root, `/app.js`, `/styles.css` served.
- No outbound sync/download; no production database used.

## Production acceptance (verified)

- Health `{"status":"healthy","database":true}`; image ID exact; restart count 0;
  release metadata `deploy:deploy` mode 600, phase `production_verified`; Caddy
  SHA256 unchanged; no reload.
- Stage APIs for archived seasons `2024` and `2025` each return counts
  **8 / 8 / 4 / 2 / 1** (Knockout Round Play-off, Round of 16, Quarter-finals,
  Semi-finals, Final); all-stage aggregate **23**.
- All finished non-final pairs expose **2 legs** with a stable, correctly oriented
  aggregate; finals expose **1 leg** with `aggregateScore` null.
- All archived-season probabilities are `historical_no_data` (present-day inputs
  omitted — no later-information leak).
- Baseline archive standings exactly preserved: **36** each; current standings
  **36** with identical team IDs.
- Parent independently verified the new image + metadata + health via SSH.
- Live final API for 2024 returned **PSG 5–0 Inter**.
- Actual headless public-browser `#final` card after selecting season 2024 shows
  **5–0** plus the unavailable-probability label.

## Caddy and neighbors

- No Caddy change; `/etc/caddy/Caddyfile` SHA256 unchanged; no reload.
- Existing public method policy remains unchanged. A harmless probe returned
  GET 404 and POST 403; no admin endpoint was invoked.
- 14 neighboring containers unchanged (only the UCL container ID changed, as expected on
  recreate).

## Monitoring limits (recorded, not hidden)

- No Loki delivery verification in this rollout; no external log sink was exercised.
- UCL Caddy access logging remains absent (preexisting, unchanged).
- No git push performed (commit is local only).
- Current-season standings comparison is by team ID / count only, because the
  startup autosync legitimately updates current-season rows.

## Durable release and rollback identity

Protected metadata: `/opt/ucl-solkoff/active-release.json`, mode 0600,
`deploy:deploy`, phase `production_verified`. It records the new release, image/source
hashes, and the full backup. The prior metadata is preserved unchanged at
`/opt/ucl-solkoff/releases/20261015T065341Z-4f6383f-81e592ac3ff0/active-release.previous.json`.

- Active override: `/opt/ucl-solkoff/releases/20261015T065341Z-4f6383f-81e592ac3ff0/compose.release.yml`
  (image `ucl-solkoff:20261015T065341Z-4f6383f-81e592ac3ff0`).
- Rollback override: `/opt/ucl-solkoff/releases/20261015T065341Z-4f6383f-81e592ac3ff0/compose.rollback.yml`
  (points at the prior image `ucl-solkoff:20261013T140544Z-9b7466b-cf5d21eef94a`).

Every future recreate must include the pinned active override; the base manifest alone
still names the old `latest` image. An image rollback must retain the public method
restriction; do not reopen administration automatically. Data rollback uses the fresh
snapshot `data-before-apply.tar.gz`. No secrets were copied into the image or printed.
**No rollback was required.**

## Local verification

- Fresh focused checks: **51** backend + **132** frontend = **183 passed**.
- Node syntax check and `git diff` check clean.
- Full backend prior check: **186 passed**, 18 baseline failures (documented backlog,
  unchanged by this release).

## Remaining work

- None within the approved knockout-results scope.
- Backend test backlog (18 pre-existing failures) and monitoring route-cleanup tests
  unchanged.
- UCL Caddy access logging; Loki delivery re-verification; nightly backup/restore tooling
  repairs remain open.
