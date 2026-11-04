# Hetzner release — 2026-10-12

## Outcome

The user explicitly authorized the combined UCL rollout, UCL-only Caddy method protection, and a private off-host snapshot on skuby7. Production deployment completed and was verified at https://ucl.mplytics.eu. No git commit or push was made.

- Release: `20261012T100847Z-cd69ffa-255820ac74d6`.
- Container started: `2026-10-12T10:25:23.373068737Z`.
- Subsequent verification at `2026-10-12T10:31:59Z`: healthy, restart count 0.
- Compose project/service: `ucl-solkoff` / `ucl-solkoff`.
- Base manifest: `/opt/ucl-solkoff/compose.yml` (unchanged).
- Existing bind: `/opt/ucl-solkoff/data` to `/app/data` (unchanged).
- Existing `.env` checksum unchanged; mode remains 600, deploy:deploy. No secrets copied into the image or printed.

## Immutable source and images

The source snapshot includes uncommitted approved work. Base commit alone does not identify the release; the bundle checksum is authoritative.

| Item | Value |
|---|---|
| Source bundle SHA256 | `255820ac74d6aa23fd7998de716f0fe9bf704f6ecc150d26502a0c95e629a567` |
| Image tag | `ucl-solkoff:20261012T100847Z-cd69ffa-255820ac74d6` |
| New image ID | `sha256:6e0c7c1e672cfa077635638c75668ff6e7e6a6f256964abb4290346ebd962409` |
| Retained previous image ID | `sha256:205cc94106b80da5442da86c8d5ecd7ba3b21d277d5cf6356cdf7c147286a71a` |

Only Dockerfile, pyproject.toml, uv.lock, backend and frontend were staged. Secrets, live data, git metadata, tests, redesign and bytecode caches were excluded. No symlinks were allowed in the source bundle.

Candidate verification occurred before downtime: preinstalled dependencies work with network disabled, Playwright is absent, and an isolated temporary database without an API key serves the new HTML/JS/CSS and initialized health. No live database was used in that smoke test.

## Snapshot and validation

UCL alone was gracefully stopped with a 120-second limit. Container mount consumers and host file holders were checked. The complete data directory, including sidecars, was archived while all writers were stopped.

- Private off-host root: `/home/linards/.local/share/ucl-solkoff/backups` on skuby7, mode 0700.
- Release snapshot: `/home/linards/.local/share/ucl-solkoff/backups/20261012T100847Z-cd69ffa-255820ac74d6/data-before.tar.gz`, mode 0600.
- SHA256: `4aeaa99a1afd3788dc23193466cbab2e0732f5f26974f4bc26457ed33b51c78f`.
- The transferred archive checksum matched the protected server archive.
- Archive extraction and read-only DuckDB verification succeeded on both the candidate-image side and skuby7; row counts matched the stopped source.
- Validation engine reported DuckDB 1.4.4.

| Table | Snapshot rows |
|---|---:|
| api_cache | 4 |
| elo_ratings | 498 |
| matches | 3245 |
| solkoff_coefficients | 498 |
| standings | 36 |
| team_mappings | 833 |
| teams | 498 |

The live data directory was not replaced, deleted or restored during the successful rollout. Its original bind identity and existing dataset were preserved.

## Caddy cutover

Only the UCL site body changed. The complete candidate was built from the current live Caddyfile, preserving all other bytes/routes, and validated using installed Caddy v2.11.4. Source checksum was rechecked before atomic installation and graceful reload. No full Ansible apply, route retirement or other-service restart occurred.

- Original Caddyfile SHA256: `68ccda0a84a7becc0be6d03c5b82f135e1f42de0776a173764fa4f7722e94e22`.
- Installed candidate SHA256: `912002664f9de42b10edb268c2cb6a9ee74bf900ea3388c368d8f48f52c02b23`.
- Public GET/HEAD/OPTIONS continue to the backend; forbidden methods terminate before upstream.
- Public POST/PUT/PATCH/DELETE/TRACE against harmless `/api/__protection_probe__` each returned 403.
- No real historical-refresh/cache-clearing/administrative POST was executed.
- Previously active neighboring container IDs remained unchanged. Caddy, Alloy and CrowdSec remained active.

## Production acceptance

Read-only browser acceptance against the real site verified:
- New UCL Strength Lab branding and default Overview, with ten-row rankings.
- League's 36 teams, search, sort, advanced-stat toggle and opponent detail expansion.
- Browser Reload data issued GET only, preserved the filter and completed without page errors.
- Legacy Round-of-16 hash selected the intended stage and displayed the honest empty-data state.
- At viewport width 375, document width was 375 (no page-wide overflow).
- Production desktop/mobile screenshots were captured; no test fixture teams were substituted.

Public health returned `{"status":"healthy","database":true}`. Existing data baseline remained 36 teams with one game each, current stage FINAL and zero Final/Round-of-16 pairs. That known data issue was explicitly accepted for this rollout, not repaired or hidden. Live populated knockout analysis therefore could not be exercised; its interaction coverage remains in the deterministic browser suite.

Loki was queried over its configured Tailscale address, not loopback. A successful query returned the UCL Docker stream labeled host=hetzner-vps-1 and compose_project=ucl-solkoff, with a fresh log timestamp `2026-10-12T10:32:17.882459+00:00`. This verifies current container log delivery. UCL-specific Caddy access logging is still not configured; no log-policy change was part of deployment.

## Durable release and rollback identity

Protected metadata: `/opt/ucl-solkoff/active-release.json`, mode 0600, deploy:deploy. It records image IDs, source/snapshot checksums and these overrides:

- Active: `/opt/ucl-solkoff/releases/20261012T100847Z-cd69ffa-255820ac74d6/compose.release.yml`.
- Rollback: `/opt/ucl-solkoff/releases/20261012T100847Z-cd69ffa-255820ac74d6/compose.rollback.yml`.

Every future recreate must include the active override; the base manifest alone still names the old `latest` image. Logs/exec/restart address the existing container, but recreate must not silently fall back to latest. The old image, protected original Caddyfile, data archive and rollback override remain available. An image rollback must retain the public method restriction; do not reopen administration automatically.

## Remaining work

- Data freshness/stage inconsistency remains a separate investigation.
- Existing backend test backlog remains: 18 pre-existing failures. Local preparation suites had 120 frontend/container tests passing and 22 isolated Caddy tests passing.
- Other monitoring route-cleanup tests still have 30 pre-existing failures for missing artifacts; that work was neither applied nor repaired here.
- After build, host disk reported 6.5G available (83% used). No shared images, backups or failed state were pruned/deleted.
- Nightly backup/restore tooling repairs remain separate; this release used an independently verified manual snapshot.

One staging-only Compose inspection failed because jq quoting stripped a literal key; it was corrected and only the failed validation was resumed. UCL was not stopped until all candidate configuration checks passed. No rollback was required during the actual replacement.
