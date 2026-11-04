# Hetzner UCL-Solkoff — Read-Only Preflight

**Status:** Historical pre-deployment evidence, collected read-only. During that preflight no host changes, snapshots, release artifacts, mutating API calls or infrastructure edits were performed. **A separately authorized live deployment later completed on 2026-10-12**; see the Post-cutover status section below and `docs/releases/2026-10-12-hetzner-release.md` for current state. Statements such as "not deployed" in the original evidence describe preparation, not current production.

**Method:** Connected over Tailscale SSH to the production VPS and inspected the running container, its Compose manifest, the data mount, the Caddy configuration, the host resources, and the public HTTP/JSON baseline. All values below are **observed during the current session**. Where a precise clock value was not captured, it is labeled "this session" rather than an invented timestamp/timezone.

**Purpose:** Establish the *actual* live topology (Compose project/service/manifest, data mount identity, image, environment source, Caddy ownership, and port mapping) so that the rollout plan and `DEPLOYMENT.md` reflect verified facts, not repository assumptions. The repository assumptions were **not** live evidence before this preflight.

---

## 1. Access and host

| Item | Observed value |
|------|----------------|
| SSH target | `deploy@openclaw.tail45ce0a.ts.net` (Tailscale; from vps-monitoring inventory) |
| Hostname | `openclaw` |
| Docker | `29.8.1` |
| Docker Compose | `v5.5.1` |
| Kernel | `6.8.0-142-generic` |
| Disk | `38G` total, `29G` used, `6.7G` free (~82% used) |
| Memory | `3819MiB` total, `1989MiB` AVAILABLE, `859MiB` FREE |
| Swap | `4095MiB` total, `3503MiB` used |

> **Resource caveat:** The available headroom (especially disk at 82% and heavy swap usage) is **not** enough basis to promise a risk-free host-side image build. The rollout must preserve a free-space gate and must not `prune` or delete shared images without separate approval.

## 2. UCL container

| Item | Observed value |
|------|----------------|
| Container name | `ucl-solkoff-ucl-solkoff-1` |
| Compose project | `ucl-solkoff` |
| Compose service | `ucl-solkoff` |
| Manifest (only config file) | `/opt/ucl-solkoff/compose.yml` |
| Working directory | `/opt/ucl-solkoff` |
| Image | `ucl-solkoff:latest` |
| Image ID | `sha256:205cc94106b80da5442da86c8d5ecd7ba3b21d277d5cf6356cdf7c147286a71a` |
| State | healthy, up ~2 weeks, restart_count `0` |
| Restart policy | `unless-stopped` |
| Live CMD | `uv run uvicorn backend.main:app --host 0.0.0.0 --port 8000` (pre-`--no-sync`) |
| Port mapping | `127.0.0.1:8003` → container `8000` |
| User | root (`Config.User` empty) |
| Logging driver | `json-file`, `max-file=3`, `max-size=50m` |
| Healthcheck | `python3` urllib `GET /api/health`; timeout `5s`, interval `30s`, retries `3`, start_period `2m`, healthcheck timeout `10s` |

**Key correction vs. repository/old docs:** the data is a **bind mount**, not the `ucl_data` named volume that the old `deploy.sh` assumed. The manifest is a **standalone** `/opt/ucl-solkoff/compose.yml`, not a service injected into an OpenClaw `docker-compose.yml`.

## 3. Data mount and secrets

| Item | Observed value |
|------|----------------|
| Data mount | bind `/opt/ucl-solkoff/data` → `/app/data` (rw) |
| DuckDB file | `/opt/ucl-solkoff/data/ucl.db`, ~39,596,032 bytes, mode `644`, owner `root:root` |
| Data directory | `/opt/ucl-solkoff/data`, ~48M, mode `775`, owner `deploy:deploy` |
| `DB_PATH` | `./data/ucl.db` (relative to `/app`), resolves into the bind mount |
| Secrets file | `/opt/ucl-solkoff/.env`, mode `600`, owner `deploy:deploy` |
| Environment (non-secret) | `UPDATE_INTERVAL=7200`, `API_CACHE_TTL=7200`, `COMPETITION_ID=CL`; `EXTERNAL_API_KEY` present (value never printed) |

> **No `releases/` directory was detected** on the host (read-only absence check only; nothing was created).

## 4. Caddy (reverse proxy)

- Caddy is a **host systemd service**, not a container: unit `/usr/lib/systemd/system/caddy.service`, command `/usr/bin/caddy run --environ --config /etc/caddy/Caddyfile`, state **active**.
- The UCL route uses handlers `subroute`, `encode`, and `reverse_proxy` to upstream `127.0.0.1:8003`.
- **No UCL access logger is configured** (other domains on the host do have access logging).
- Caddy ownership is in the vps-monitoring Ansible inventory. The route observed during live preflight contained only `encode` + `reverse_proxy`; local inventory has since gained the selected method restriction, which is not yet deployed.
- **App-image-only redeploy:** an unchanged route/upstream needs no Caddy reload or Ansible apply. **Combined protection cutover:** the new method restriction requires a separately authorized UCL-only Caddy configuration change, full candidate validation and graceful reload; never a full VPS playbook apply.

## 5. Monitoring and backup

- **Alloy** and **CrowdSec** are active. Alloy config has `discovery.docker` and `loki.source.docker` via `docker.sock` and the `compose_project` label.
- **Actual UCL Loki delivery was NOT verified.** The presence of Alloy config is not evidence that UCL logs are being delivered. This is recorded as an open gate, not a completed check.
- `server-backup.timer` is **inactive** on this VPS. The backup coordinator lives on `skuby7`; inactivity here is **not** proof that global backup is disabled.
- The vps-monitoring backup runbook states that **production restore tools remain broken** pending the repaired deployment. An own, verified snapshot is therefore required regardless of any global backup.

## 6. Public read-only baseline (HTTPS, GET only)

No administrative `POST` calls were issued. Read-only probes:

| Probe | Observed result |
|-------|-----------------|
| `GET /` | Old UEFA Champions League Solkoff UI (not the new branding) |
| `GET /styles.css`, `GET /app.js` | `200` |
| `GET /api/health` | `{"status":"healthy","database":true}` |
| `GET /api/standings` | 36 teams; all played 1; first row Paris Saint-Germain FC, points 3, Solkoff/Strength `1.722000002861023` |
| `GET /api/tournament/current-stage` | `FINAL` |
| `GET /api/knockout-pairs/FINAL` | 0 pairs |
| `GET /api/knockout-pairs/ROUND_OF_16` | 0 pairs |

> **Data-freshness contradiction:** `current-stage` reports `FINAL` while both the Final and Round-of-16 return 0 pairs, and standings show only 1 game played per team. This is a data-freshness inconsistency requiring a user decision. The frontend rollout should preserve this baseline but **cannot validate actual knockout analysis until data exists**.

---

## 7. What was NOT done

- No host, container, Caddy, or Ansible changes.
- No snapshot, archive, or off-host copy created.
- No `releases/` directory or release image built.
- No mutating API calls (`/api/refresh`, `/api/refresh-historical`, `/api/cache/clear`) executed.
- No credentials, DNS, firewall, or known-host policy changes.
- The selected method-restriction policy was **not** applied to the live VPS (local only; not yet deployed).
- The old `deploy.sh` was **not** run.

---

## 8. Unresolved rollout gates

These gates are explicit and must be resolved before production replacement. None is claimed complete.

1. **Production replacement approval** — required as a separate, explicit action; not yet granted.
2. **Security gate** — policy selected and locally tested: public `GET`/`HEAD`/`OPTIONS` only, other methods 403, loopback admin access. Local evidence is recorded in section 9; **live enforcement remains pending** until the separately authorized policy cutover.
3. **Data-freshness / stage contradiction** — `FINAL` stage with 0 knockout pairs and 1 game played per team. User decision required on treatment; knockout analysis cannot be validated until data exists.
4. **Off-host backup destination** — must be chosen and confirmed private/writable before downtime; not decided.
5. **Host resource headroom** — disk ~82% used, heavy swap use. Preserve the free-space gate; no shared-image `prune`/delete without approval.
6. **Monitoring coverage** — UCL Loki delivery unverified; record as an open gate.
7. **Backup/restore tooling** — production restore tools broken per vps-monitoring runbook; own verified snapshot required regardless.

---

## 9. Security policy (selected — local test evidence, not live proof)

Selected and approved policy (separate, scoped change, **local only — not yet deployed**):

- The UCL route continues to proxy to `127.0.0.1:8003` (unchanged).
- Public requests are allowed **`GET`, `HEAD`, `OPTIONS`** only; all other methods are rejected with **403** (admin/maintenance, SSH loopback only).
- The backend remains unauthenticated; trust is enforced at the proxy. Admin routes are reachable only from the VPS loopback (and, through it, the SSH operator); the loopback bind must remain.
- The current live exposure remains until the separately authorized policy cutover.

Local test evidence (run before cutover; **not** live proof of the public route):

| Check | Result |
|-------|--------|
| Real Caddy method-restriction tests (public `GET`/`HEAD`/`OPTIONS` proxy; tested forbidden methods return 403 without reaching upstream) | 22 passed using an isolated local stub, not the live backend |
| UCL frontend deploy pass | 120 passed (110 browser, 10 deploy) |

These local results validate the policy and the new GET-only frontend. They are **not** evidence of the live public route: the live VPS route still has no method restriction until the policy cutover. Read-only evidence in sections 1–8 stands as-is.

---

## Post-cutover status (2026-10-12)

Everything above remains the **historical preflight record** — the observed pre-deployment values (old image, pre-`--no-sync` CMD, Caddy without method restriction, unverified Loki delivery, unresolved gates). The rollout was then **executed and verified on 2026-10-12** under explicit authorization. Authoritative record: [`docs/releases/2026-10-12-hetzner-release.md`](docs/releases/2026-10-12-hetzner-release.md).

- **Deployment:** LIVE and verified at `https://ucl.mplytics.eu`. Release `20261012T100847Z-cd69ffa-255820ac74d6`; new image `sha256:6e0c7c1e672cfa077635638c75668ff6e7e6a6f256964abb4290346ebd962409` (previous image ID retained). Container started `2026-10-12T10:25:23Z`, verified healthy with restart count 0 at `2026-10-12T10:31:59Z`. No git commit or push.
- **Active override:** `/opt/ucl-solkoff/active-release.json` (mode 600, `deploy:deploy`) now exists and pins the active/rollback Compose overrides; every future recreate must include the pinned override (never `latest`).
- **Security gate — cleared:** public `GET`/`HEAD`/`OPTIONS` allowed, other methods 403 (probed on a nonexistent path only; no real admin POST executed). UCL-only Caddy change, graceful reload, all other bytes/routes and neighboring container IDs unchanged. SSH private admin over Tailscale loopback unchanged. UCL Caddy access logging still absent (not fixed).
- **Off-host backup — cleared:** verified snapshot on `skuby7`, private path, snapshot record checksum recorded in the release doc. No backups deleted or pruned.
- **Monitoring — cleared:** Loki delivery verified over the configured Tailscale endpoint (not loopback); latest UCL log `2026-10-12T10:32:17.882459+00:00`.
- **Data baseline — accepted, not fixed:** 36 teams, 1 game played, stage `FINAL`, 0 knockout pairs preserved. Live populated knockout analysis could not be exercised; coverage remains in the deterministic browser suite (110 browser tests passing, no page errors, document width 375 at 375 viewport).
- **Acceptance:** healthy, 0 restarts, started `10:25:23Z`; pre-snapshot metrics ~48M; post-cutover disk 6.5G free (83% used). Memory values not re-recorded.

**Former gates:** 1 (approval), 2 (security), 4 (backup destination), 6 (monitoring) are **cleared for the approved release**. Remaining operational follow-ups (not deployment blockers): data freshness/stage investigation (gate 3), UCL Caddy access logging, nightly backup/restore tooling repairs, backend test backlog (18 pre-existing failures), monitoring route-cleanup tests (30 pre-existing failures).

---

## References

- `DEPLOYMENT.md` — deployment runbook reflecting this verified topology.
- `docs/superpowers/plans/2026-10-11-hetzner-redeployment.md` — rollout implementation plan.
- `docs/backend-test-backlog.md` — the 18 pre-existing backend test failures (unrelated to this deployment).
- vps-monitoring: `docs/hetzner-log-monitoring-spec.md`, `ansible/inventories/production/{hosts.yml,group_vars/vps.yml}`, `docs/runbooks/05-server-backup.md`.
