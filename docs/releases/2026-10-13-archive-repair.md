# Historical archive repair release — 2026-10-13

## Outcome

The 2024/25 and 2025/26 Champions League archives were repaired in production and the app was redeployed on the fixed image. Both archives now contain exactly 36 clubs playing eight league games each, from source-canonical fixtures, with correct knockout stages. Deployed and verified at https://ucl.mplytics.eu. No git push.

- Release: `20261013T102059Z-29a6b2e-d857dba26b13`
- Code commits: `29a6b2e4c67b3d21658e84915d6e27715b6f796d` (knockout stage-filter isolation) and `232b70fdbabde193e612888eecb48f1f6768fcf0` (historical identity/date repair)
- Previous live release: `20261012T180125Z-19d9bbb-c813918c85cd` (base `19d9bbb`)
- Image: `ucl-solkoff:20261013T102059Z-29a6b2e-d857dba26b13`, ID `sha256:b616dabb295d361c4f8fb42161e1df1dfa0c8841dfef44d711de2c09f4c470ea`
- Container started `2026-10-13T10:23:00Z`; healthy, restart count 0
- Compose project/service `ucl-solkoff` / `ucl-solkoff` (unchanged); base manifest, env, bind `/opt/ucl-solkoff/data → /app/data`, and loopback `127.0.0.1:8003` unchanged

## Source bundle

Whitelist only (`Dockerfile`, `pyproject.toml`, `uv.lock`, `backend/`, `frontend/`). `Dockerfile`, `pyproject.toml`, `uv.lock` are byte-identical to the previous live release. Runtime diff vs previous live baseline:

| File | Change |
|---|---|
| `backend/football_txt_parser.py` | modified (parser fix) |
| `backend/team_matcher.py` | modified (kickoff stripping) |
| `backend/data_service.py` | modified (deterministic fixture IDs) |
| `backend/playoff_analyzer.py` | modified (knockout stage-filter isolation) |
| `backend/fixture_ids.py` | added |
| `backend/historical_repair.py` | added (offline repair tool) |

Bundle SHA256: `d857dba26b13e676016694e4c91f808f392038e346c5b2e6763c1f9b3111f0a4`.

## Pinned repair sources

Repository `openfootball/champions-league`, pinned commit `abfaeddc2ee3d14f99ecc163c9ddb46cb4d67cef` (fetch-only at pin time; apply inputs immutable, no network).

| Season | Source | SHA256 |
|---|---|---|
| 2024/25 | [`cl-2024-25.txt`](https://github.com/openfootball/champions-league/blob/abfaeddc2ee3d14f99ecc163c9ddb46cb4d67cef/2024-25/cl.txt) | `c2692b22af02c5df4587b69c5f430aebf7d936845b5337aee462ce8e649eb1b5` |
| 2025/26 | [`cl-2025-26.txt`](https://github.com/openfootball/champions-league/blob/abfaeddc2ee3d14f99ecc163c9ddb46cb4d67cef/2025-26/cl.txt) | `2b1877308587ff1004c21b8e961b9050b8754f968244e0635eb005040fd9d5cb` |

Uploaded to `/opt/ucl-solkoff/releases/20261013T102059Z-29a6b2e-d857dba26b13/sources/` and mounted read-only for the offline repair.

## Fresh consistent backup

UCL alone was gracefully stopped (`stop -t 120`, SIGTERM, exit 143, no forced kill) before the repair; no other data-bind user and no host file holders. The complete bind was archived, copied off-host, checksum-verified, extracted, and validated read-only (DuckDB 1.4.4).

| Item | Value |
|---|---|
| Server snapshot | `/opt/ucl-solkoff/releases/20261013T102059Z-29a6b2e-d857dba26b13/data-before-apply.tar.gz` (root, mode 0600) |
| Private off-host | `/home/linards/.local/share/ucl-solkoff/backups/20261013T102059Z-29a6b2e-d857dba26b13/data-before-apply.tar.gz` (mode 0600) |
| SHA256 | `28aedd01ca3e2ab0291f7f85eb7110242af84367617c5dac59ff32f12164bffb` |
| Row counts | api_cache 4, elo_ratings 498, matches 3245, solkoff_coefficients 498, standings 36, team_mappings 833, teams 498 |

## Offline repair and protected fingerprints

Rehearsed first on a disposable copy (`--network none`, separate port), then applied to the stopped live DB (`--network none`, bind `/data`, pinned sources read-only, no scheduler/API). Transaction scoped to CL in `2024-08-01..2025-08-01` and `2025-08-01..2026-08-01`: deleted 189 + 189, inserted 378 (144 league + 45 knockout per season).

- Before: 2024/25 league table had **44** clubs; 2025/26 had **49**. After: **36 clubs × 8 games** each season.
- 11 clean team nodes added (BSC Young Boys, Bayer 04 Leverkusen, Bologna FC 1909, Eintracht Frankfurt, Olympique de Marseille, Qarabağ Ağdam FK, Royale Union Saint-Gilloise, SK Sturm Graz, Sport Lisboa e Benfica, Stade Brestois 29, Tottenham Hotspur FC).
- 11 canonical mapping keys remapped: `bayer 04 leverkusen, bologna fc 1909, bsc young boys, eintracht frankfurt, olympique de marseille, qarabağ ağdam fk, royale union saint gilloise, sk sturm graz, sport lisboa e benfica, stade brestois 29, tottenham hotspur`.
- Protected tables (repair boundary): `standings 1f597ba2…`, `elo_ratings 87f1f6d0…`, `solkoff_coefficients aaaee181…`, `api_cache c12ff151…` — **before == after**; `team_mappings` changed only at the 11 remapped canonical keys. The repair-tool fingerprint excludes all 54 scoped canonical keys; the remaining 779 rows fingerprint `9b142bbc…` before == after.
- Proof boundary: these fingerprints were captured at the **offline repair boundary, before normal startup autosync**. The startup autosync legitimately updates current-season rows, so the byte-unchanged claim holds for the protected tables at the repair boundary only — not for all tables after startup.
- Independent read-only live-vs-backup comparison: non-target matches `f17021cc…` equal; current-season CL matches `072019da…` equal; competition counts equal; existing team rows `87af655d…` equal; unrelated mapping keys `b618f96f…` equal; matches 3245→3245; teams 498→509; mappings 833→833; repaired-window names contain 0 kickoff prefixes.
- Mapping fingerprint methods differ intentionally: the repair tool hashes `repr((column_names, rows))` (`9b142bbc…`), while the independent verifier hashes `repr(rows)` (`b618f96f…`). Both cover the same 779 out-of-scope rows and match their respective before/after values.
- Idempotent second apply on the copy: no new teams, no mapping changes.

Repair report: `/opt/ucl-solkoff/releases/20261013T102059Z-29a6b2e-d857dba26b13/livework/live-apply.json`.

## Live acceptance (verified)

- Health `{"status":"healthy","database":true}`; image ID exact; restart count 0; root/app.js/styles served.
- Seasons listed: 2026/27, 2025/26, 2024/25.
- `season=2024` and `season=2025`: **36 teams, all played 8**.
- Liverpool 2024: position 1, played 8, points 21, GF 17, GA 5, Solkoff `1.375`.
- All **72** archived breakdowns: 8 opponents / 8 matches each, coefficient equals standings.
- Knockout pairs per stage (both seasons): KNOCKOUT_PLAYOFF 8, ROUND_OF_16 8, QUARTER_FINAL 4, SEMI_FINAL 2, FINAL 1; aggregate `/api/playoff-pairs` 23 with labels 8/8/4/2/1.
- Current-season standings: 36 teams, `(teamId, points, played)` identical to the pre-repair baseline snapshot.
- Public method restriction preserved: POST/PUT/PATCH/DELETE/TRACE on a harmless probe each return 403; frontend remains GET-only.
- Caddyfile SHA256 unchanged `912002664f9de42b10edb268c2cb6a9ee74bf900ea3388c368d8f48f52c02b23`; no reload. Neighbor containers unchanged.
- Startup: scheduler initialized once (interval 7200s), normal current-season autosync (36 teams / 144 matches / 36 standings); historical sync skipped (already present). No restart loop. No hidden external fetch on archive endpoints (GET only in logs).

## Protected metadata and rollback

`/opt/ucl-solkoff/active-release.json` (mode 600, `deploy:deploy`, phase `production_verified`) now records the new release, image/source hashes, repaired years, and the full backup. The prior metadata is preserved unchanged at `.../releases/20261013T102059Z-29a6b2e-d857dba26b13/active-release.previous.json` (SHA256 `9785494ad67e2a904d82333fff0fe394007fd0646f3d74d7d90b85bdbacc9487`) and off-host.

- Active override: `.../20261013T102059Z-29a6b2e-d857dba26b13/compose.release.yml`
- Rollback override: `.../compose.rollback.yml` → prior image `ucl-solkoff:20261012T180125Z-19d9bbb-c813918c85cd`
- Image rollback retains the public method restriction. Data rollback uses the fresh snapshot `data-before-apply.tar.gz` (not the stale phase-1 snapshot).

## Previous rolled-back attempt (recorded)

An earlier attempt on candidate `20261013T065418Z-232b70f-76f07c706234` (image `sha256:f6c99c6b…`, before the stage-filter fix) passed backup, rehearsal, live apply, and 72 breakdowns, but failed acceptance: `/api/knockout-pairs/ROUND_OF_16` returned 11 and `/api/knockout-pairs/FINAL` returned 7 per season (pre-existing analyzer filters leaking SF/Final into R16 and substring `%FINAL%` matching Quarter/Semi-finals). The candidate container was replaced (rolled back), not pruned; the failed candidate image and its data remain retained. The repaired live data was preserved at `/opt/ucl-solkoff/failed-data-20261013T071429Z`, the verified backup was restored, and the prior pinned image was restarted. Commit `29a6b2e` fixed the filters; the retry above completed cleanly.

## Monitoring / checks not run

- No Loki delivery verification in this rollout.
- Parent browser smoke (Chromium 153 / Playwright 1.63, local `uv`): PASS. Desktop: 2026 default (36 teams, 1 played); 2024 and 2025 each 36 teams, all played 8. Mobile 390x844: renders, no overflow. No admin `POST`s. Full keyboard-only traversal not covered.
- UCL Caddy access logging remains absent.

## Remaining work

- None within the approved repair scope. Unrelated pre-existing data (e.g. kickoff-prefixed names in other competitions' historical rows) was left untouched.
