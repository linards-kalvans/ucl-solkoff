# Deployment

## Overview

UCL Solkoff (UEFA Champions League standings, Solkoff coefficients, and knockout common-opponent analysis) runs as a **single Docker container** on a Hetzner server.

- **Standalone Compose project** `ucl-solkoff` with its own manifest at `/opt/ucl-solkoff/compose.yml`. It is **not** part of any OpenClaw stack.
- **Caddy is a host systemd service** (`caddy.service`, `/etc/caddy/Caddyfile`) that terminates TLS and reverse-proxies `ucl.mplytics.eu` to loopback `127.0.0.1:8003` → container `8000`. It is **not** a containerized Caddy.
- **Data persists in a bind mount** `/opt/ucl-solkoff/data` → `/app/data`. It is **not** a Docker named volume.
- **SSH access is over Tailscale**: `deploy@openclaw.tail45ce0a.ts.net` (host hostname `openclaw`).

| URL | Service |
|-----|---------|
| https://ucl.mplytics.eu | UCL Solkoff (this app) |

**Access:** SSH as `deploy@openclaw.tail45ce0a.ts.net` (Tailscale).

> **Do not use `deploy.sh`.** The repository's `deploy.sh` targets the old topology: it syncs over the public IP, overwrites `/opt/ucl-solkoff/.env` via a heredoc, patches a containerized Caddy in `/opt/openclaw/Caddyfile`, and injects a `ucl_data` named volume into `/opt/openclaw/docker-compose.yml`. None of that matches the live server. Running it would corrupt the live environment. All current operations use the standalone manifest and systemd Caddy described below.

## Verified topology (read-only preflight)

Read-only evidence collected over Tailscale during the current session. Full detail and unresolved gates are in [`docs/hetzner-preflight.md`](docs/hetzner-preflight.md). This table is the **pre-cutover** snapshot; post-cutover values are in the [Current running state](#current-running-state) section and the release record [`docs/releases/2026-10-12-solkoff-scope-release.md`](docs/releases/2026-10-12-solkoff-scope-release.md). The initial rollout record remains at [`docs/releases/2026-10-12-hetzner-release.md`](docs/releases/2026-10-12-hetzner-release.md) (immutable).

| Aspect | Verified value |
|--------|----------------|
| SSH target | `deploy@openclaw.tail45ce0a.ts.net`; host hostname `openclaw` |
| Host | Hetzner (nbg1); kernel `6.8.0-142-generic` |
| Docker / Compose | `29.8.1` / `v5.5.1` |
| Compose project / service | `ucl-solkoff` / `ucl-solkoff` |
| Container name | `ucl-solkoff-ucl-solkoff-1` |
| Manifest | `/opt/ucl-solkoff/compose.yml` (the only config file) |
| Working dir | `/opt/ucl-solkoff` |
| Image | `ucl-solkoff:latest` (sha256 `205cc94106b80da5442da86c8d5ecd7ba3b21d277d5cf6356cdf7c147286a71a`) |
| Container state | healthy, up ~2 weeks, restart_count 0, `restart: unless-stopped` |
| Live CMD | `uv run uvicorn backend.main:app --host 0.0.0.0 --port 8000` (pre-`--no-sync`) |
| Port mapping | `127.0.0.1:8003` → container `8000` |
| Data mount | bind `/opt/ucl-solkoff/data` → `/app/data` (rw) — **not** a named volume |
| DuckDB | `/opt/ucl-solkoff/data/ucl.db` (~39.6 MB); `DB_PATH=./data/ucl.db` resolves into the bind |
| Secrets | `/opt/ucl-solkoff/.env`, mode `600`, owner `deploy:deploy` |
| Caddy | systemd `caddy.service` (`/usr/bin/caddy run --environ --config /etc/caddy/Caddyfile`); UCL route uses `subroute`, `encode`, `reverse_proxy 127.0.0.1:8003`; **no** UCL access logger configured |
| Healthcheck | `python3` urllib `GET /api/health`; timeout 5s, interval 30s, retries 3, start_period 2m, healthcheck timeout 10s |
| Logging | `json-file`, `max-file=3`, `max-size=50m` |
| User | container runs as root (`Config.User` empty) |

## Current running state

**LIVE — verified 2026-11-04:** release
`20261104T113416Z-0105334-14cb0d760c77`, image
`sha256:e70c3f496e60b3f6b2fb19e157724683db86840f59217b8c417f8d7667ff4ec9`,
phase `production_verified_with_limitations`. See the
[November 4 release record](docs/releases/2026-11-04-hetzner-release.md).
CL uses football-data.org; EL and Conference League use ESPN. The November 3
legacy image was pinned as the immediate rollback baseline under explicit user
authorization; stale October metadata was archived. Full stopped data backups
are retained on and off host. **Rollback must restore that release's complete
verified data backup before starting the old image.** Resolve the active override
from protected `/opt/ucl-solkoff/active-release.json`, not a historical release path.

### Historical running-state snapshot — 2026-10-12

The following snapshot and dated rollout gates are historical, not the current
release or its unresolved-data assessment.

**LIVE — verified 2026-10-12.** The second production rollout is complete. The live container runs the release image `ucl-solkoff:20261012T143625Z-cd69ffa-45a5aee75836` (CMD with `--no-sync`), is healthy, and has restart count 0. This release carries the current-season Solkoff scope fix (see [Current-season Solkoff Implementation Plan](docs/superpowers/plans/2026-10-12-solkoff-season-scope.md)).

- Release: `20261012T143625Z-cd69ffa-45a5aee75836`
- Source bundle SHA256: `45a5aee75836cf6bbb2c6fd69b950e3cd0ddbe7cadfbb859680ae1addd1f8cc0`; base HEAD `cd69ffa267af5ee07869cfce850f991702d2ae76`. Inherits the `20261012T100847Z-cd69ffa-255820ac74d6` snapshot plus only local fixes to `backend/solkoff_calculator.py`, `backend/main.py`, `backend/scheduler.py`.
- Image ID: `sha256:07d632934edca2b0ee0e6d88b1c9b99d9356c887a51f78e2c4c0901c01d00951`; previous image `sha256:6e0c7c1e672cfa077635638c75668ff6e7e6a6f256964abb4290346ebd962409` and old `latest` `sha256:205cc94106b80da5442da86c8d5ecd7ba3b21d277d5cf6356cdf7c147286a71a` retained
- Container started `2026-10-12T14:57:00Z`; healthy, restart_count 0, independently reverified by parent
- Active override: `/opt/ucl-solkoff/releases/20261012T143625Z-cd69ffa-45a5aee75836/compose.release.yml`; rollback override: same folder/compose.rollback.yml pointing at the prior image; both recorded in `/opt/ucl-solkoff/active-release.json` (mode 600, `deploy:deploy`, `production_verified` phase, prior release identity and `caddy_after_sha256` recorded)
- Pre-cutover snapshot (private, skuby7, verified): `/home/linards/.local/share/ucl-solkoff/backups/20261012T143625Z-cd69ffa-45a5aee75836/data-before.tar.gz` (mode 0600, SHA256 `b92f3cb8c6e71ca099769737c101671153181a53ac942a779a113079f640972b`); server copy at `/opt/ucl-solkoff/releases/20261012T143625Z-cd69ffa-45a5aee75836/data-before.tar.gz` (root, mode 0600); extraction and read-only DuckDB 1.4.4 validation succeeded
- Data preserved: 36 teams, 1 game played, stage `FINAL`, 0 knockout pairs; Solkoff coefficients now reflect current-season opponents (16 winners 0.0, 4 draws 1.0, 16 losers 3.0; all 36 details 1 match/1 opponent). Stage/freshness issues remain open (see [Rollout gates](#rollout-gates--2026-10-12-cutover-initial-release))
- Loki delivery verified over Tailscale (initial rollout, log `2026-10-12T10:32:17.882459+00:00`); no new Loki verification performed in this rollout. UCL Caddy access logging still absent (not fixed in this rollout)

Authoritative record: [`docs/releases/2026-10-12-solkoff-scope-release.md`](docs/releases/2026-10-12-solkoff-scope-release.md). Initial rollout record (immutable): [`docs/releases/2026-10-12-hetzner-release.md`](docs/releases/2026-10-12-hetzner-release.md).

## Release model

Rollouts are immutable and staged, so a failure leaves the running service intact:

1. **Stage source** to a unique release directory `/opt/ucl-solkoff/releases/<release-id>/`.
   - Whitelist (stage exactly these): `Dockerfile`, `pyproject.toml`, `uv.lock`, `backend/`, `frontend/`.
   - Exclude: `.env`, `data/`, `.git/`, `tests/`, `redesign/`.
   - Never `rsync --delete` into the live source directory.
2. **Release label.** Because the redesign is uncommitted, `HEAD` alone does **not** identify the deployed source. The label must be:
   `<UTC-timestamp>-<git HEAD short sha>-<sha256 of staged source snapshot>`.
   The snapshot checksum is the authoritative identifier.
3. **Build the candidate image** on the VPS while the current service stays up. Preserve the old image tag/ID for rollback.
   Use a dedicated disposable builder so this app's builds do not accumulate cache
   in the shared host builder. Never select it as the global default:

   ```sh
   BUILDER="ucl-release-<unique-release-id>"
   docker buildx create --name "$BUILDER" --driver docker-container
   # Always remove this builder on success or failure; do not pass --keep-state.
   trap 'docker buildx rm "$BUILDER"' EXIT
   docker buildx build --builder "$BUILDER" --load \
     --tag "ucl-solkoff:<release-id>" /opt/ucl-solkoff/releases/<release-id>/source
   ```

   This removes the dedicated builder container and cache, not other apps' caches.
4. **Image-only override.** Under the release directory, create a Compose override that sets only the image:

   ```yaml
   services:
     ucl-solkoff:
       image: ucl-solkoff:<release-id>
   ```

   Keep durable rollback metadata (release-id, base commit, snapshot checksum, old image ID) alongside it.

## Rollout procedure (requires separate approval)

> This section is the approved runbook for **future** rollouts. The 2026-10-12 rollout was executed per this procedure and is recorded in the [Current running state](#current-running-state) section; no further change is implied by these instructions. Each future rollout requires a separate, explicit approval. Each gate must be satisfied before proceeding. Stop and report if any gate fails.
>
> **Order:** frontend first, then the UCL-only validated Caddy change (method restriction). Not a full monitoring Ansible apply. A Caddy reload is required **only** at the separately authorized policy cutover (combined gate: method restriction + image); an app-only (image-only) redeploy needs no reload.

### Gate 0 — Authorization
- Production replacement requires a separate, explicit approval.
- Confirm the off-host backup destination is chosen and confirmed private/writable before downtime. Do not guess it.
- Confirm the security decision (see Security gate) is made or explicitly accepted.

### Step 1 — Isolated candidate smoke
- Use a **temporary database** and a **separate loopback port**; never the live data mount, never port `8003`, never the live DB.
- Check the candidate port is free before binding.
- Run without the production API key so no external sync side effects occur. Use `--network none` for the dependency check; run a separate isolated health smoke.
- Verify: startup/stability, root branding/assets, database-initialized health, no Playwright in the installed environment, no runtime dependency sync.
- If startup attempts unexpected outbound work, stop — do not silently modify the backend.
- On failure, stop. Live service untouched.

### Step 2 — Consistent backup
- Snapshot stop the current precutover service:
  ```sh
  docker compose -p ucl-solkoff -f /opt/ucl-solkoff/compose.yml stop -t 120 ucl-solkoff
  ```
  Postcutover, include the pinned `RELEASE_OVERRIDE` in the same command.
- Confirm stopped and no other consumers of the bind mount / DuckDB writers remain active. **If any writer is unknown, stop the plan.**
- If shutdown required a forced kill or was unclean, do not treat the archive as a verified rollback snapshot until extraction and offline restored-DB validation succeed.
- Archive the complete data mount (`/opt/ucl-solkoff/data`, including sidecar files) while all writers are stopped. Preserve permissions. Store the snapshot in a protected unique path; **no deletions or volume removal**.
- Copy the snapshot to protected off-host storage. Verify checksum and extraction.
- Validate the extracted DuckDB **read-only** via the candidate image (`uv run --no-sync python`), and record representative table row counts.
- On snapshot verification failure: restart old UCL and stop the rollout.
- Failed backup: restart the old service, preserving its configuration.

### Step 3 — Targeted replacement
- Start the candidate with **exactly the same** Compose project / service / environment / mount identities.
- Preserve the full original `-f` manifest list in original order and add the single image-only override.
- **Recreate rule:** every recreate must use the trusted pinned `RELEASE_OVERRIDE` from protected active-release metadata (`/opt/ucl-solkoff/active-release.json`, mode 600, `deploy:deploy`), which now exists for release `20261012T143625Z-cd69ffa-45a5aee75836`. Operators set `RELEASE_OVERRIDE` via trusted `jq` metadata and validate the path and image ID. If that override is missing, stop; never fall back to `latest`. Inspect the actual running image with `docker inspect --format '{{.Image}}' ucl-solkoff-ucl-solkoff-1`, not the `latest` tag.
- Command shape (identities are preflight outputs, not guesses):

  ```sh
  docker compose -p ucl-solkoff \
    -f /opt/ucl-solkoff/compose.yml \
    -f "$RELEASE_OVERRIDE" \
    up -d --no-deps --no-build --pull never ucl-solkoff
  ```

- Verify supported Compose flags against the installed version (`v5.5.1`).
- **Never** run stack-wide `down`, `down -v`, volume deletion, or a broad restart. **Never** recreate a differently named project (that would attach an empty volume).

### Step 4 — Acceptance
- HTTPS/certificate valid; branding and asset responses present at `ucl.mplytics.eu`.
- Health reports an initialized database; standings retain expected teams/data.
- Stage hashes and League search/sort/opponent details work; knockout cards and full sidebar work.
- Mobile layout and refresh/error display validated without unnecessary mutating requests; any manual refresh is explicitly approved and rate limits are considered.
- Scheduler initialized once; no restart loop; no duplicate application workers.
- Data mount identity unchanged; neighboring services unchanged.
- UCL logs visible; monitoring delivery checked, or missing coverage recorded as an open gate (not silently completed).

### Step 5 — Post-acceptance Docker image retention

Run cleanup only **after acceptance and protected active-release metadata have
been published**, never during a rollout or concurrently with another build,
rollout, or rollback. Hold `/opt/ucl-solkoff/.release.lock` with `flock` throughout
future deployments and rollbacks; the installed retention service uses that same
lock and exits without cleanup if it is held. Retain the active image, its immediate rollback image, and
every image referenced by any running or stopped container. Keep backups,
release evidence, source bundles and failed data independently of image retention.

Copy `scripts/prune_release_images.py` to
`/opt/ucl-solkoff/prune_release_images.py` and run its dry-run first. The host
helper uses only the system Python standard library (no host uv installation):

```sh
python3 /opt/ucl-solkoff/prune_release_images.py
flock -n /opt/ucl-solkoff/.release.lock \
  python3 /opt/ucl-solkoff/prune_release_images.py --apply
```

The helper removes only obsolete immutable `ucl-solkoff:<release-id>` tags,
without force. It refuses cleanup if acceptance is incomplete, protected images
or tags are missing, or the running image differs from active-release metadata.
It never deletes containers, volumes, databases, other repositories, `latest`,
or unidentified dangling images. Never substitute a host-wide `docker system
prune`, `image prune -a`, or shared-builder cache prune on this multi-app server.
The image helper does not prune shared build cache; use the disposable UCL
builder described above for all new builds. Existing shared cache is left intact.

**Installed 2026-11-04:** `ucl-image-retention.timer` runs daily with up to 30
minutes of randomized delay and catches up after downtime. Its oneshot service
runs as `deploy`, takes the deployment lock, and applies the guarded helper.
Tracked unit definitions are under `scripts/systemd/`; install them in
`/etc/systemd/system/`, validate with `systemd-analyze verify`, then enable the
timer. The first service execution succeeded with no obsolete release tags to
remove: only the active and immediate rollback UCL image IDs remain.

```sh
systemctl status ucl-image-retention.timer
systemctl show ucl-image-retention.service -p Result -p ExecMainStatus
journalctl -u ucl-image-retention.service
```

## Rollback

- **Application failure:** same Compose identity, old-image override, existing data preserved. **The public method restriction is retained** (it lives in the Caddy configuration, not the app image): the old Refresh button may be unavailable, and admin `POST` routes remain restricted. The admin routes are **never** reopened automatically.
- **Data corruption:** stop UCL, verify paths and no writers, preserve the failed data as a unique protected sibling, restore the verified extracted snapshot into the exact bind path preserving root-owned files/permissions. Never delete/overwrite failed data.
- **Caddy:** the live Caddyfile contains the public method restriction (installed at cutover). Before any authorized Caddy reload, validate `/etc/caddy/Caddyfile`; an app-only redeploy needs no reload. An image rollback must retain the public method restriction; do not reopen administration automatically.
- After rollback, repeat the health/data/UI checks. **Never** delete failed data or old images during a rollout.

## Operational commands

All run over Tailscale SSH. The standalone manifest is `/opt/ucl-solkoff/compose.yml`; the project is `ucl-solkoff`.

```sh
# Logs (follow)
ssh deploy@openclaw.tail45ce0a.ts.net "cd /opt/ucl-solkoff && docker compose -p ucl-solkoff -f /opt/ucl-solkoff/compose.yml logs -f ucl-solkoff"

# Container status and health
ssh deploy@openclaw.tail45ce0a.ts.net "cd /opt/ucl-solkoff && docker compose -p ucl-solkoff -f /opt/ucl-solkoff/compose.yml ps ucl-solkoff"

# Open a shell inside the container
ssh deploy@openclaw.tail45ce0a.ts.net "cd /opt/ucl-solkoff && docker compose -p ucl-solkoff -f /opt/ucl-solkoff/compose.yml exec ucl-solkoff bash"

# Restart without rebuilding
ssh deploy@openclaw.tail45ce0a.ts.net "cd /opt/ucl-solkoff && docker compose -p ucl-solkoff -f /opt/ucl-solkoff/compose.yml restart ucl-solkoff"

# Current image ID
ssh deploy@openclaw.tail45ce0a.ts.net "docker inspect --format '{{.Image}}' ucl-solkoff-ucl-solkoff-1"

# Caddy (systemd, not a container)
ssh deploy@openclaw.tail45ce0a.ts.net "systemctl status caddy.service"
# Caddy reload is required ONLY at the separately authorized policy cutover (combined gate: method restriction + image). An app-only (image-only) redeploy needs no reload.
ssh deploy@openclaw.tail45ce0a.ts.net "sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile && sudo systemctl reload caddy.service"
```

**Admin maintenance (SSH only).** The backend has no authentication: admin `POST` routes are protected by the VPS method restriction and must be called from the VPS loopback over Tailscale SSH only — never from a public `curl`.

```sh
# Force a re-sync (maintenance)
ssh deploy@openclaw.tail45ce0a.ts.net "curl -X POST http://127.0.0.1:8003/api/refresh"

# Clear the API response cache (maintenance)
ssh deploy@openclaw.tail45ce0a.ts.net "curl -X POST http://127.0.0.1:8003/api/cache/clear"
```

> **Impact warning:** `/api/refresh-historical` (sync historical matches) and `/api/cache/clear` mutate state and are **not to be executed** outside an explicit maintenance window.

## Environment variables

Managed in `/opt/ucl-solkoff/.env` on the server (mode `600`, `deploy:deploy`). Never print resolved values.

| Variable | Description |
|----------|-------------|
| `EXTERNAL_API_KEY` | football-data.org API key (present; value never printed) |
| `EXTERNAL_API_BASE_URL` | API base URL |
| `COMPETITION_ID` | `CL` (Champions League) |
| `DB_PATH` | `./data/ucl.db` (resolves into the bind at `/app/data/ucl.db`) |
| `UPDATE_INTERVAL` | Scheduler interval in seconds (live: 7200) |
| `API_CACHE_TTL` | API cache TTL in seconds (live: 7200) |
| `API_MIN_REQUEST_INTERVAL` | Minimum delay between API requests in seconds |

After an approved release cutover has established the active-release metadata, change a value by editing `/opt/ucl-solkoff/.env` on the server and recreating the service (a restart does not pick up env changes):
```sh
docker compose -p ucl-solkoff -f /opt/ucl-solkoff/compose.yml -f "$RELEASE_OVERRIDE" up -d --no-deps --no-build --pull never ucl-solkoff
```
Do not use the old `deploy.sh` heredoc.
This command is a post-cutover procedure. The active-release override does not exist before the first approved cutover; do not substitute an empty path or silently fall back to `latest`.

## Security policy — live enforced since 2026-10-12

The public OpenAPI has **no global security scheme**, and the admin endpoints `POST /api/refresh`, `POST /api/refresh-historical`, and `POST /api/cache/clear` have **no security constraint**. The UCL route contains no authentication or denial handler in the backend; protection is enforced at the proxy.

The selected and approved policy is a **VPS-level method restriction** as a **separate, scoped change**:

- The UCL route continues to proxy to `127.0.0.1:8003` (unchanged).
- Public requests are allowed **`GET`, `HEAD`, `OPTIONS`** only; all other methods are rejected with **403** (admin/maintenance, SSH loopback only).
- The backend remains unauthenticated; trust is enforced at the proxy. The admin routes are reachable only from the VPS loopback (and, through it, the SSH operator). The loopback bind must remain.
- A blanket `POST` deny **no longer breaks** the new frontend: the browser Refresh uses `GET` only (zero `POST`).

**Status (2026-10-12 cutover):** enforced. The UCL-only Caddy method restriction was installed from a candidate built off the live Caddyfile (all other bytes/routes preserved), validated with Caddy v2.11.4, and applied via graceful reload. Public POST/PUT/PATCH/DELETE/TRACE against a harmless probe path each returned 403; no real admin/mutating POST was executed. SSH private admin access over Tailscale loopback is unchanged. UCL Caddy access logging remains unconfigured (not fixed in this rollout).

Do **not** silently implement authentication as part of this deployment. There is no source-freshness/season API currently; this is not the scope of the deployment fix.

## Rollout gates — 2026-10-12 cutover (initial release)

Gates from the preflight, with post-cutover status as of the **initial** rollout. No gate is claimed complete beyond what is recorded below.

1. **Production replacement approval** — granted and executed 2026-10-12 (combined rollout + UCL-only Caddy method restriction + private snapshot); no git commit or push. A second rollout (`20261012T143625Z-cd69ffa-45a5aee75836`) followed the same procedure under separate approval; see [Current running state](#current-running-state).
2. **Security gate** — cleared for the approved release: public `GET`/`HEAD`/`OPTIONS` only, other methods 403, loopback admin access. Live enforcement verified (see Security policy); Caddyfile unchanged and method restriction preserved in the second rollout.
3. **Data freshness / stage contradiction** — **not fixed, accepted as baseline**: 36 teams, 1 game played, stage `FINAL`, 0 Final/Round-of-16 pairs. Live populated knockout analysis could not be exercised; coverage remains in the deterministic browser suite. Separate investigation required. The second rollout deployed the Solkoff scope fix (current-season coefficients correct); stage/freshness issues remain unresolved.
4. **Off-host backup destination** — chosen and verified: private path on `skuby7` (`/home/linards/.local/share/ucl-solkoff/backups`, mode 0700); snapshot `data-before.tar.gz` (mode 0600, SHA256 recorded in the release doc), extraction and read-only DuckDB verification succeeded.
5. **Host resource headroom** — post-cutover disk 6.5G free (83% used). Free-space gate preserved; no shared images, backups, or failed state pruned/deleted.
6. **Monitoring coverage** — Loki delivery **verified** over its configured Tailscale endpoint (not loopback); latest UCL log `2026-10-12T10:32:17.882459+00:00`. UCL Caddy access logging still absent — remaining operational follow-up.
7. **Backup/restore tooling** — nightly backup/restore repairs remain separate; this release used an independently verified manual snapshot.

**Remaining operational follow-ups:** stage/freshness investigation (Solkoff coefficients fixed by the second rollout; stage `FINAL` stale-stage detection and malformed historical parser names remain unfixed); backend test backlog (18 pre-existing failures); monitoring route-cleanup tests (30 pre-existing failures); UCL Caddy access logging; nightly backup/restore tooling repairs.

## Troubleshooting

**Container not starting**
```sh
ssh deploy@openclaw.tail45ce0a.ts.net "cd /opt/ucl-solkoff && docker compose -p ucl-solkoff -f /opt/ucl-solkoff/compose.yml logs ucl-solkoff --tail=50"
```

**Caddy not routing `ucl.mplytics.eu`**
```sh
ssh deploy@openclaw.tail45ce0a.ts.net "sudo systemctl status caddy.service"
# Only after separate authorization; validation failure must prevent reload.
ssh deploy@openclaw.tail45ce0a.ts.net "sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile && sudo systemctl reload caddy.service"
```
The UCL route must `reverse_proxy 127.0.0.1:8003`; the container must publish `127.0.0.1:8003→8000`.

**Empty standings / data not loading**
The app fetches live data on startup and then every `UPDATE_INTERVAL` seconds. Check container logs for `HTTP 403` (invalid API key) or `HTTP 429` (rate limit exceeded).

**TLS certificate not issued**
Caddy obtains Let's Encrypt certificates automatically on first request. Requires ports 80 and 443 open and DNS resolving to the server. Issuance takes up to ~30 seconds on first hit.
