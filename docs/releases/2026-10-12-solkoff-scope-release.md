# Solkoff scope release — 2026-10-12

## Outcome

The user explicitly approved the rollout. Production deployment of the current-season Solkoff scope fix (see [Current-season Solkoff Implementation Plan](../superpowers/plans/2026-10-12-solkoff-season-scope.md)) completed and was verified at https://ucl.mplytics.eu. No git commit or push was made.

- Release: `20261012T143625Z-cd69ffa-45a5aee75836`
- Container started: `2026-10-12T14:57:00Z`
- Independently reverified by parent: healthy, restart count 0, correct image
- Compose project/service: `ucl-solkoff` / `ucl-solkoff` (unchanged)
- Base manifest: `/opt/ucl-solkoff/compose.yml` (unchanged)
- Existing bind: `/opt/ucl-solkoff/data` → `/app/data` (unchanged)
- Existing `.env` (mode 600, `deploy:deploy`) unchanged; no secrets copied into the image or printed
- Loopback: `127.0.0.1:8003` → container `8000` (unchanged)

## Immutable source and images

The source snapshot inherits the active release snapshot of `20261012T100847Z-cd69ffa-255820ac74d6` plus **only** local fixes to `backend/solkoff_calculator.py`, `backend/main.py`, and `backend/scheduler.py` (current-season Solkoff scope fix). The snapshot checksum is the authoritative identifier; base commit alone does not identify the release.

| Item | Value |
|---|---|
| Source bundle SHA256 | `45a5aee75836cf6bbb2c6fd69b950e3cd0ddbe7cadfbb859680ae1addd1f8cc0` |
| Base HEAD | `cd69ffa267af5ee07869cfce850f991702d2ae76` |
| Image tag | `ucl-solkoff:20261012T143625Z-cd69ffa-45a5aee75836` |
| New image ID | `sha256:07d632934edca2b0ee0e6d88b1c9b99d9356c887a51f78e2c4c0901c01d00951` |
| Retained previous image ID | `sha256:6e0c7c1e672cfa077635638c75668ff6e7e6a6f256964abb4290346ebd962409` |
| Retained old `latest` image ID | `sha256:205cc94106b80da5442da86c8d5ecd7ba3b21d277d5cf6356cdf7c147286a71a` |

Only Dockerfile, pyproject.toml, uv.lock, backend, and frontend were staged. Secrets, live data, git metadata, tests, and redesign were excluded.

## Snapshot and validation

UCL alone was gracefully stopped (exit 143, SIGTERM; stop command 0, no forced kill). Container mount consumers and host file holders were checked; no holders remained. The complete data directory was archived while all writers were stopped.

- Protected server snapshot: `/opt/ucl-solkoff/releases/20261012T143625Z-cd69ffa-45a5aee75836/data-before.tar.gz` (root, mode 0600).
- Private off-host root: `/home/linards/.local/share/ucl-solkoff/backups` on skuby7, mode 0700.
- Release snapshot: `/home/linards/.local/share/ucl-solkoff/backups/20261012T143625Z-cd69ffa-45a5aee75836/data-before.tar.gz`, mode 0600.
- SHA256: `b92f3cb8c6e71ca099769737c101671153181a53ac942a779a113079f640972b`.
- Server and local checksums match; extraction and read-only DuckDB verification (DuckDB 1.4.4) succeeded on both sides; counts and row fingerprints agree.

| Table | Snapshot rows |
|---|---:|
| api_cache | 4 |
| elo_ratings | 498 |
| matches | 3245 |
| solkoff_coefficients | 498 |
| standings | 36 |
| team_mappings | 833 |
| teams | 498 |

The live data directory was not replaced, deleted, or restored during the successful rollout. Its original bind identity and existing dataset were preserved.

## Caddy

No Caddy change in this rollout.

- Caddyfile SHA256 unchanged at `912002664f9de42b10edb268c2cb6a9ee74bf900ea3388c368d8f48f52c02b23`; no reload.
- Public GET/HEAD/OPTIONS continue to the backend; POST/PUT/PATCH/DELETE/TRACE against a harmless probe path each return 403 (preserved).
- 14 non-UCL container IDs unchanged; Caddy, Alloy, and CrowdSec remained active.

## Production acceptance

Read-only verification against the live site (all 36 breakdowns checked during rollout; independent parent fetches of standings and details for Bayern 5, Roma 100, Bodø/Glimt 5721; SSH image/health metadata confirmed):

- Standings: 36 teams, 1 game played each; 16 winners coeff 0.0, 4 draws coeff 1.0, 16 losers coeff 3.0.
- All 36 solkoff-details rows: 1 match, 1 opponent, average matches stored.
- No live-vs-offline drift: offline no-network exclusive recalculation wrote only the solkoff_coefficients table; non-coeff table counts and full row fingerprints IDENTICAL; historical matches intact.
- Candidate offline-copy recalculation tested independently before the actual live recalculation.
- Health: `{"status":"healthy","database":true}`.
- No manual admin POST/sync; startup autosync ran unchanged upstream data.
- No data/image prune; no rollback needed. Post-deploy disk: 6.1 GiB.

## Durable release and rollback identity

Protected metadata: `/opt/ucl-solkoff/active-release.json`, mode 0600, `deploy:deploy`. Published with the complete `production_verified` phase, recording the prior release identity and `caddy_after_sha256`.

- Active: `/opt/ucl-solkoff/releases/20261012T143625Z-cd69ffa-45a5aee75836/compose.release.yml`.
- Rollback: `/opt/ucl-solkoff/releases/20261012T143625Z-cd69ffa-45a5aee75836/compose.rollback.yml` (points to the prior image `sha256:6e0c7c1e672cfa077635638c75668ff6e7e6a6f256964abb4290346ebd962409`).

Every future recreate must include the active override; the base manifest alone still names the old `latest` image. The old images, protected Caddyfile, data archive, and rollback override remain available. An image rollback must retain the public method restriction; do not reopen administration automatically.

## Operational deviation (resolved)

One cutover guard aborted on an empty-stdout `grep -q` assertion (script bug, before any image change; the service remained stopped). The guard was corrected and cutover resumed only; all checks passed. No rollback, no data loss. Phase script safety fixes reviewed: stop-before-restore, protected failed-data sibling clean restoration, independent recovery actions.

## Remaining work

- Data freshness/stage inconsistency remains a separate investigation (stage `FINAL` stale-stage detection and malformed historical parser names remain NOT fixed).
- Existing backend test backlog remains: 18 pre-existing failures, unchanged from baseline (95 passed overall). Local focused suite: 44 passed.
- No new Loki verification in this rollout; the prior record (log `2026-10-12T10:32:17.882459+00:00`) stands.
- UCL Caddy access logging still absent; nightly backup/restore tooling repairs remain separate; monitoring route-cleanup backlog unchanged.
