# Hetzner Redeployment Implementation Plan

> For agentic workers: use subagent-driven-development, tests first and independent review. Preparation approval was followed by explicit authorization for the combined live rollout and skuby7 backup. Deployment completed on 2026-10-12; future production changes require their own authorization. No commits, pushes, worktrees or unrelated infrastructure changes.

**Goal:** Redeploy UCL Strength Lab to the existing Hetzner service/domain while preserving data/secrets and neighboring services; brief restart downtime is acceptable.

**Architecture:** Existing Caddy proxies ucl.mplytics.eu to the UCL container on loopback port 8003. Preserve existing Compose project/service/environment/data identities; build an immutable image before stopping only UCL. Verify an offline data snapshot before replacing the service, with old-image and snapshot rollback paths.

**Tech stack:** Docker Compose, Caddy, uv, FastAPI, DuckDB, existing Tailscale SSH and monitoring.

## Status (as of this session)

- **Task 1 (local artifacts + TDD): complete.** Dockerfile `CMD` is now `["uv","run","--no-sync","uvicorn","backend.main:app","--host","0.0.0.0","--port","8000"]`; build install remains `uv sync --frozen --no-dev`. The 10 container-contract tests in `tests/deploy/test_container_contract.py` are green; `tests/frontend` (105) green; the 18 pre-existing backend failures remain tracked in `docs/backend-test-backlog.md` and are unrelated to this change.
- **Task 2 (read-only live preflight): complete.** Evidence is in [`docs/hetzner-preflight.md`](docs/hetzner-preflight.md). Verified: standalone manifest `/opt/ucl-solkoff/compose.yml` (project `ucl-solkoff`, service `ucl-solkoff`); **bind mount** `/opt/ucl-solkoff/data` → `/app/data` (NOT the `ucl_data` named volume); **systemd** Caddy (NOT containerized) proxying `ucl.mplytics.eu` → `127.0.0.1:8003`; live image `ucl-solkoff:latest` (pre-`--no-sync`); unauthenticated admin routes exposed.
- **Tasks 3–5: completed after separate authorization on 2026-10-12.** Release `20261012T100847Z-cd69ffa-255820ac74d6` was built/smoked, a stopped-app snapshot was verified off-host on skuby7, UCL alone was replaced, and the separately approved UCL method policy was validated/installed/reloaded. Production browser, health, harmless denial probes and neighbor checks passed. See `docs/releases/2026-10-12-hetzner-release.md`; no rollback was required.

## Confirmed scope and boundaries

- Target: existing Hetzner UCL service and https://ucl.mplytics.eu.
- SSH candidate: deploy@openclaw.tail45ce0a.ts.net, from vps-monitoring inventory.
- Repository assumptions are not live evidence. Read-only preflight must determine actual Compose manifests, project name, service, mount, image, environment source and Caddy owner.
- Preserve data and secrets. No database migrations, backend-test repairs, authentication feature work, infrastructure rebuild, Caddy ownership change or unrelated service restart.
- Do not run old deploy.sh: overwrites environment configuration and assumes containerized Caddy in the OpenClaw stack, whereas vps-monitoring describes systemd Caddy.
- Existing 18 backend test failures remain recorded in docs/backend-test-backlog.md.

## Design rationale

1. Preserve Compose identities to avoid accidentally creating an empty volume by changing the project name.
2. Build release image before downtime; failures leave the running service intact.
3. One application worker/replica: embedded scheduler and DuckDB writer are not scaled horizontally.
4. Keep existing proxy route; no Caddy reload if upstream is unchanged.
5. Snapshot DuckDB only while all UCL writers are stopped. Do not copy a live file and call it consistent.
6. Verify a deployment-specific snapshot and off-host copy; vps-monitoring documents unresolved deployed backup restore issues.
7. Use uv run --no-sync at startup so the preinstalled production environment does not gain default dev dependencies such as Playwright.

## Task 1: Local artifacts and TDD

Files:
- Create tests/deploy/test_container_contract.py.
- Modify Dockerfile CMD only: retain RUN uv sync --frozen --no-dev; change CMD to ["uv", "run", "--no-sync", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"].
- Update DEPLOYMENT.md after implementation/review and read-only preflight.
- Keep this approved plan updated with factual verification results.

Test engineer writes tests before Dockerfile edit. Verify correct build-time exclusion of dev dependencies, startup without sync, entrypoint/port, absence of reload or multiple workers. Use meaningful Dockerfile command parsing rather than whitespace-sensitive snapshots. At least the no-sync test must fail against the old Dockerfile; existing correct invariants may pass in red phase.

Commands: uv run pytest tests/deploy -q; uv run pytest tests/frontend -q; uv run pytest. Record all output including the known backend failures. Independent reviewer checks minimal scope and configuration semantics. No Docker-dependent tests silently skipped in the normal suite; actual container smoke checks belong to the later rollout stage.

## Task 2: Read-only live preflight

Connect over Tailscale with batch mode and bounded connection timeout. Do not alter known-host policy, SSH/firewall settings, credentials or bot services.

Record only non-secret evidence:
- Running UCL container, image ID, Compose project/service labels, exact configuration files and working directory.
- Actual data mounts/volume identities, DuckDB path, permissions and size.
- Secret source path and permissions; never print resolved environment values or full docker inspect/docker compose config output.
- Published port: expected loopback 8003 to container 8000; record deviations.
- Caddy process owner (systemd/container), active configuration source and matching UCL upstream.
- Disk/memory, Docker/Compose versions and neighboring service identities/statuses.
- Public TLS/HTML/asset and read-only health/standings/current-stage/knockout API baseline. Query the discovered current stage, with ROUND_OF_16 as documented fallback; do not issue administrative POSTs.
- Existing UCL log/monitoring configuration. Do not claim centralized delivery merely because configuration exists.

Acceptance: target topology, ownership, data and rollback inputs identified. If assumptions differ, stop and revise exact commands before rollout. If access fails, report the actual blocker, without modifying credentials or falling back to public SSH automatically.

Security gate: preflight identified unauthenticated administrative endpoints (refresh-historical, cache/clear and refresh), without executing them. The user subsequently approved GET-only public Reload data and a UCL-only Caddy GET/HEAD/OPTIONS allow policy. Implementation and isolated verification are tracked in 2026-10-12-public-admin-protection.md. This resolves the policy decision, not live enforcement; the gate stays open until separately authorized cutover and post-cutover verification.

## Task 3: Release staging and isolated validation (requires rollout authorization)

Create unique release identifier from base commit ID plus timestamp and record source snapshot checksum, because current redesign is uncommitted. Do not label uncommitted content solely with HEAD SHA.

Stage exactly Dockerfile, pyproject.toml, uv.lock, backend/ and frontend/. Exclude .env, data, .git, tests and redesign. Do not rsync --delete into the live source directory. Build tagged candidate image on VPS while current service remains running; preserve old image tag/ID.

Candidate smoke uses temporary database and separate loopback port, never live data mount. Avoid API/history synchronization side effects in isolated smoke (no production key; explicit controlled test environment). Check startup/stability, root branding/assets, database-initialized health, lack of Playwright in installed environment and absence of runtime dependency synchronization. Stop if candidate fails; live service untouched.

## Task 4: Consistent backup and targeted replacement (requires rollout authorization)

1. Preserve previous image identity, original Compose manifests, release metadata and protected secret-file references. Do not put secrets in user-visible artifacts.
2. Gracefully stop only UCL, allowing bounded shutdown time for scheduler and DuckDB.
3. Archive complete data mount including sidecar files while all writers stopped. Preserve permissions. Snapshot stored in a protected unique path; no deletions or volume removal.
4. Copy snapshot to protected off-host storage, verify checksum and extraction; open extracted DuckDB read-only and record representative table row counts.
5. On snapshot verification failure, restart old UCL and stop rollout.
6. Start candidate with exactly the same Compose project/service/environment/mount identities. Preserve the full original -f manifest list in original order and add one image-only release override.

Release override contract:

```yaml
services:
  ucl-solkoff:
    image: ucl-solkoff:<release-id>
```

Command shape after identifiers are discovered:

```sh
docker compose -p "$PROJECT" -f "$BASE_MANIFEST" -f "$RELEASE_OVERRIDE" up -d --no-deps --no-build ucl-solkoff
```

If multiple original manifests exist, include all of them before the override. The placeholder identities here are preflight outputs, not guessed values. Verify supported Compose flags against the installed version.

Never run stack-wide down, down -v, volume deletion or broad restart. Never recreate a differently named project that attaches an empty volume.

## Task 5: Production acceptance and rollback

Acceptance checks:
- HTTPS/certificate valid; new branding and asset responses present at ucl.mplytics.eu.
- Health reports initialized database; standings retain expected teams/data.
- Existing stage hashes and League search/sort/opponent details work; Knockout cards and full sidebar work.
- Mobile layout, refresh/error display behavior validated without unnecessary mutating requests; any manual refresh is explicitly approved and rate limits considered.
- Scheduler initialized once, no restart loop, no duplicate application workers.
- Data mount identity unchanged; neighboring services unchanged.
- UCL logs visible and monitoring delivery checked, or missing coverage recorded as an open gate rather than silently completed.

Rollback:
- Application failure: same Compose identity, old-image override, existing data preserved.
- Data corruption: stop UCL, preserve failed data for investigation, restore verified snapshot, restart old image.
- Caddy rollback unnecessary when proxy unchanged.
- Repeat health/data/UI checks after rollback. Never delete failed data or old images during rollout.

## Documentation scope

DEPLOYMENT.md must reflect verified Tailscale access, actual Compose/Caddy ownership, immutable release staging, snapshot/restore steps, secrets preservation, smoke tests and explicit rollout/rollback gates. Do not change vps-monitoring artifacts under this task. If live infrastructure needs changes, propose separately.

## Execution checklist

- [x] Save approved plan.
- [x] Write deployment tests and confirm red phase.
- [x] Inspect live topology/security/baseline read-only; report blockers.
- [x] Implement minimal CMD change and pass deployment tests.
- [x] Independent review; verify fixes if any.
- [x] Update deployment runbook and verify frontend/deployment tests; report full-suite baseline failures.
- [x] Share scoped critique diff and request separate production-replacement authorization.
- [x] After separate authorization only: stage/build/smoke, snapshot/verify, targeted replace, acceptance/rollback.

## References

Historical preparation verification: 115 deployment/frontend tests passed before the later GET-only reload change. Protection preparation subsequently passed 120 frontend/container tests and 22 isolated Caddy tests; full UCL suite had 177 passed and 18 pre-existing backend failures. Live preflight was refreshed, rollout helpers independently reviewed, and the authorized combined deployment completed on 2026-10-12. Release evidence, snapshot checksums and active/rollback identities are recorded in `docs/releases/2026-10-12-hetzner-release.md`.

- docs/hetzner-preflight.md — read-only preflight evidence and unresolved rollout gates.
- vps-monitoring: docs/hetzner-log-monitoring-spec.md, ansible/inventories/production/{hosts.yml,group_vars/vps.yml}, docs/runbooks/05-server-backup.md.
- https://docs.docker.com/compose/how-tos/production/
- https://docs.astral.sh/uv/guides/integration/docker/
- https://caddyserver.com/docs/caddyfile/patterns
