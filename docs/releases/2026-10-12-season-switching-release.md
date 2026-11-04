# Season switching release — 2026-10-12

## Outcome

The season switcher (new-format Champions League seasons 2024/25, 2025/26, current 2026/27) is deployed and verified in production at https://ucl.mplytics.eu. **App-only, image-only rollout — no Caddy change, no reload.** Application source was committed before deployment (`d6a6e3c`, `06e57c4`, `82c3612`, `19d9bbb`); no push; the deployment itself made no runtime edits.

- Release: `20261012T180125Z-19d9bbb-c813918c85cd`
- Base HEAD: `19d9bbb70b8b534ad20a59c993c07ff109d7062c` (`feat/ucl-strength-lab`)
- Container started: `2026-10-12T18:05:24Z`; healthy, restart count 0
- Compose project/service: `ucl-solkoff` / `ucl-solkoff` (unchanged)
- Base manifest: `/opt/ucl-solkoff/compose.yml` (unchanged)
- Data bind: `/opt/ucl-solkoff/data` → `/app/data` (rw, unchanged)
- Loopback: `127.0.0.1:8003` → container `8000` (unchanged)
- `.env` unchanged, mode 600 `deploy:deploy`; no secrets copied or printed

## Immutable source and images

Source snapshot is the byte-identical HEAD runtime whitelist (`Dockerfile`, `pyproject.toml`, `uv.lock`, `backend/`, `frontend/`). `Dockerfile`, `pyproject.toml` and `uv.lock` are **byte-identical to the live baseline** release `20261012T143625Z-cd69ffa-45a5aee75836` (same `--no-sync` launch, same dependency manifest). Only the season feature files differ from the baseline:

| File | Change vs live baseline |
|---|---|
| `backend/football_txt_parser.py` | modified |
| `backend/playoff_analyzer.py` | modified |
| `backend/main.py` | modified |
| `backend/season_service.py` | added |
| `frontend/app.js` | modified |
| `frontend/index.html` | modified |
| `frontend/styles.css` | modified |

| Item | Value |
|---|---|
| Source bundle SHA256 | `c813918c85cd8b63f9c4f3d395d14e07d5f17305f7eb0406edea56e698190176` |
| Image tag | `ucl-solkoff:20261012T180125Z-19d9bbb-c813918c85cd` |
| New image ID | `sha256:a51b58a2399e9f39a43f3534013c71e6183aabf3b93ad682e449866e1901ac5e` |
| Retained prior live image | `sha256:07d632934edca2b0ee0e6d88b1c9b99d9356c887a51f78e2c4c0901c01d00951` (`ucl-solkoff:20261012T143625Z-cd69ffa-45a5aee75836`) |

Build labels: `org.opencontainers.image.revision=19d9bbb…`, `io.ucl.source.sha256=c813918c…`.

## Isolated smoke (before downtime)

- `--network none` dependency check: `production dependencies present; Playwright absent`; `uv run --no-sync`, no runtime dependency sync.
- Separate loopback port `18003`, temporary DB `/tmp/ucl-smoke/ucl.db`, no production key:
  - `/api/health` `{"status":"healthy","database":true}`; root serves `UCL Strength Lab`; `app.js` and `styles.css` served.
  - No `Downloading` / historical-sync markers in logs.

## Historical validation (candidate against disposable copy of prior protected backup)

The candidate ran with `EXTERNAL_API_BASE_URL=http://127.0.0.1:9` (loopback-configured API, no production key) on a disposable copy of the prior protected backup, separate port `18005`.

- `/api/seasons` → `currentSeason: 2026`; seasons `2026/27`, `2025/26`, `2024/25` (newest first).
- `season=2024` and `season=2025` return **distinct** standings; current `season=2026` = 36 teams.
- Breakdown matches its standings coefficient (Liverpool FC 2024: solkoff `1.281`, 8 opponents / 8 matches).
- `season=2019` (unsupported) → `400`.
- Checked stage/knockout endpoints return 200: `current-stage?season=2024` = `FINAL`; `knockout-pairs/ROUND_OF_16?season=2025` = 17 pairs.

**Data-quality caveat (pre-existing, surfaced, not caused by this release):** archived 2024/25 and 2025/26 league-phase reconstruction yields **44** and **49** distinct teams rather than the new-format 36, and one stored team name is malformed (`21:00 Tottenham Hotspur FC`). This is the known archived-parser / historical team-mapping issue (malformed names, duplicate team identities) that the feature reads as-is. It does not affect the current-season standings; it is out of scope for this app-only rollout and continues the previously recorded "data freshness / malformed historical parser names" investigation.

## Consistent backup

UCL alone was gracefully stopped with the exact manifest plus the pinned prior active override (`stop -t 120`); container exit code 143 (SIGTERM), no forced kill. No other container uses the data bind; no host file holders on `ucl.db`. The complete data directory was archived while all writers were stopped (the graceful shutdown checkpointed the DB, so no WAL sidecar remained).

| Item | Value |
|---|---|
| Protected server snapshot | `/opt/ucl-solkoff/releases/20261012T180125Z-19d9bbb-c813918c85cd/data-before.tar.gz` (root, mode 0600) |
| Private off-host root | `/home/linards/.local/share/ucl-solkoff/backups` on skuby7, mode 0700 |
| Release snapshot | `/home/linards/.local/share/ucl-solkoff/backups/20261012T180125Z-19d9bbb-c813918c85cd/data-before.tar.gz` (mode 0600) |
| SHA256 | `a7f878143b22423f0aef3f517fac3b0843f5cf361524fe1b85459f3dfcadb7d6` |

Server and off-host checksums match. Extraction and read-only DuckDB 1.4.4 validation succeeded on both sides (rows: `api_cache` 4, `elo_ratings` 498, `matches` 3245, `solkoff_coefficients` 498, `standings` 36, `team_mappings` 833, `teams` 498). The live data directory was not replaced, deleted, or restored.

## Production acceptance (verified)

- Health: `{"status":"healthy","database":true}`; image ID `sha256:a51b58a2399e9f39a43f3534013c71e6183aabf3b93ad682e449866e1901ac5e`; restart count 0; release metadata `deploy:deploy` mode 600; Caddy SHA256 unchanged `91200266…`; `production_verified`.
- Assets: `/`, `/app.js`, `/styles.css` served; header exposes the season `<select id="seasonSelect">`.
- Current standings: 36 teams retained; team ID / points / played identical to the pre-stop baseline snapshot.
- Historical live: `/api/seasons` lists 2026/27, 2025/26, 2024/25 with 36/49/44 distinct teams; stages `LEAGUE`/`FINAL`/`FINAL`; top details `PSG` 2026 coeff `0.000`, `Arsenal FC` 2025 coeff `1.155`, `Liverpool FC` 2024 coeff `1.281`; `season=2019` → 400.
- Frontend is GET-only: 7 `fetch()` calls, every season-dependent one appends `?season=`; browser refresh never issues `POST` or `/api/refresh`.
- Startup: scheduler initialized once (`interval 7200s`), current-season autosync ran unchanged (36 teams / 144 matches / 36 standings), Elo 498, historical data present (1932 matches) → sync skipped. No restart loop.
- No manual `POST`/refresh/historical/cache calls were issued.

## Caddy and neighbors

- No Caddy change; `/etc/caddy/Caddyfile` SHA256 unchanged at `912002664f9de42b10edb268c2cb6a9ee74bf900ea3388c368d8f48f52c02b23`; no reload.
- Public `GET`/`HEAD`/`OPTIONS` continue to the backend; `POST`/`PUT`/`PATCH`/`DELETE`/`TRACE` against a harmless probe path each return `403` (method restriction preserved).
- 15 neighbor containers unchanged (only the UCL container ID changed, as expected on recreate).

## Monitoring limits (recorded, not hidden)

- No new Loki delivery verification in this rollout; no external log sink was exercised.
- UCL Caddy access logging remains absent (unchanged).
- Independent local Chromium 153 / Playwright 1.63 browser smoke: SUCCESS. Desktop 1440x950: default 2026; switch 2024 → 2025 and reload retains 2025; year-scoped APIs/details reloaded. Mobile 390x844: native labeled select focusable; root/switch no overflow. Zero console/page errors. No production `POST`s. Full keyboard-only traversal not covered; no new Loki check.

## Durable release and rollback identity

Protected metadata: `/opt/ucl-solkoff/active-release.json`, mode 600, `deploy:deploy`. The prior metadata is preserved unchanged on the server at `/opt/ucl-solkoff/releases/20261012T180125Z-19d9bbb-c813918c85cd/active-release.previous.json` (SHA256 `32fa1c9b1762741ecc41cba296d4123c7970a7e00f5d95117dd2bc3d56ae73a0`) and off-host in the release backup folder.

- Active override: `/opt/ucl-solkoff/releases/20261012T180125Z-19d9bbb-c813918c85cd/compose.release.yml` (image `ucl-solkoff:20261012T180125Z-19d9bbb-c813918c85cd`).
- Rollback override: `/opt/ucl-solkoff/releases/20261012T180125Z-19d9bbb-c813918c85cd/compose.rollback.yml` (image `ucl-solkoff:20261012T143625Z-cd69ffa-45a5aee75836`).
- Metadata records `release_override`, `rollback_override`, `candidate_image`, `candidate_image_id`, `old_image`, `old_image_tag`, `old_release`, `prior_release`, `snapshot_sha256`, `offhost_snapshot`, and the Caddy lock as `caddy_before_sha256` (the current key name; prior records used `caddy_locked_sha256`).

Every future recreate must include the pinned active override; the base manifest alone still names the old `latest` image. An image rollback must retain the public method restriction; do not reopen administration automatically.

## Operational deviation (resolved)

The isolated smoke container was first launched with `--network none`, which suppresses `-p` port publishing, so the health probe could not connect and the phase aborted **before any production change**. The smoke and historical-validation containers were relaunched without `--network none` (no/placeholder key, egress pinned to loopback `127.0.0.1:9`); only the separate dependency check uses `--network none`. The orphan staging directory and image from the aborted attempt (never activated) were removed; no shared images were pruned.

## Remaining work

- Archived historical data quality (2024/25 → 44 teams, 2025/26 → 49 teams; malformed identities such as `21:00 Tottenham Hotspur FC`) remains pre-existing and is **not fixed** by this release.
- Backend test backlog (18 pre-existing failures) and monitoring route-cleanup tests (30 pre-existing failures) unchanged.
- UCL Caddy access logging; nightly backup/restore tooling repairs; Loki delivery re-verification remain open.
