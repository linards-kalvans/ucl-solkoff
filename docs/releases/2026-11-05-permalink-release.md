# November 5 permalink release

## Authorization and source

The user authorized committing/deploying the permalink change, recovering missing
deployment state, and keeping only the active release plus one rollback release.

- Commit: `2a5fb04` — competition/season permalinks and selection preservation.
- Release: `20261105T080558Z-2a5fb04-cc393c1ff185`.
- Source bundle SHA256: `cc393c1ff185920e7a7c3b7eee86789f87f3ef12480304ee1444a8ba0ad7e65c`.
- Active image: `sha256:a438b08a507da701fea314967dbe4560d576e0bad5baf0c193dbcf55d937b9dc`.
- Immediate rollback: `ucl-solkoff:20261104T113416Z-0105334-14cb0d760c77`,
  image `sha256:e70c3f496e60b3f6b2fb19e157724683db86840f59217b8c417f8d7667ff4ec9`.

Only `frontend/app.js` differs from the preceding live application source.
Backend file contents/inventory were compared against the running image.

## Recovery

Preflight found the entire release directory, `compose.yml`,
`active-release.json`, and installed image-retention helper missing. The healthy
container and both historical image IDs were still present.

- Verified the protected off-host November 4 receipt, source archive checksum,
  backup checksum, and actual running image identity.
- Reconstructed the standalone Compose manifest from the healthy container:
  same project/service, command, loopback port, data bind, logging, restart policy,
  healthcheck, and existing network. No Caddy change or reload.
- The existing `.env` did not match the running container's `EXTERNAL_API_KEY`
  and `DB_PATH`. Values were never printed. Preserved that overwritten file;
  recovered the actual runtime environment into protected
  `/opt/ucl-solkoff/runtime.env` and verified resolved Compose values against the
  live container. This durable file is outside rotating release directories.
- Restored protected release metadata, pinned overrides, prior receipt/backup,
  and the tracked retention helper. Active metadata remains authoritative.

## Rollout and verification

- Held `/opt/ucl-solkoff/.release.lock` throughout rollout and cleanup.
- Built with a dedicated disposable Buildx builder and removed that builder.
- Candidate passed isolated no-network health/dependency checks on a disposable
  restored database, without the production API key or live data mount.
- The first stop guard rejected exit code 143 and restarted the baseline without
  activating the candidate. Investigation confirmed `uv` exits 143 after SIGTERM
  despite completed ASGI shutdown. Resumed the already built/smoked candidate;
  required completed shutdown, no OOM, and no forced-kill exit status.
- Took a stopped full-data snapshot, copied it to private off-host storage,
  verified its checksum and archive readability, extracted it, and opened restored
  DuckDB read-only: **3,839 matches**.
- Snapshot paths: release directory `data-before.tar.gz` and
  `/home/linards/.local/share/ucl-solkoff/backups/20261105T080558Z/data-before.tar.gz`.
  Its checksum is in protected active metadata and the on/off-host receipt.
- Recreated only UCL with the image-only override; same data bind and identities.
- Public health and exact served `app.js` bytes verified. All nine competition /
  season combinations (CL/EL/UCL, 2024/2025/2026) returned 36 distinct teams and
  valid stage responses; archived points totals matched the pre-cutover baseline.
- Live browser verified Conference League 2025/26 deep linking, competition
  switching without season reset, reload restoration, and browser Back.
- Container healthy, restart count 0, one scheduler initialization, all three
  competition startup syncs succeeded. Neighboring container identities/states
  unchanged; Compose, original `.env`, and Caddy hashes unchanged during cutover.
- Local verification: **151 frontend tests passed**, `git diff --check` passed.
  Backend suite not rerun because backend source was unchanged. Existing Caddy
  UCL access-logging coverage remains outside this frontend release.

## Retention and rollback

After acceptance/metadata publication, ran the guarded image-retention dry-run
and apply. Removed the obsolete November 3 image's immutable alias and unused
`latest` alias after checking neither image was protected or container-referenced.
Exactly **two UCL images and two release directories** remain: active and the
November 4 rollback. Disposable smoke/snapshot extraction directories were
removed; verified backup archives, receipts and source bundles were preserved.

The daily retention timer is active; an explicit service run returned
`Result=success`, `ExecMainStatus=0`. No host-wide Docker/cache pruning occurred.

For rollback, resolve pinned paths from protected active metadata, stop UCL,
preserve failed data, and restore this release's full verified stopped snapshot
before starting the November 4 image. Preserve the runtime environment and public
method restriction. Do not use the obsolete `deploy.sh` or delete the deployment
manifest, protected metadata, retention helper, runtime environment, or rollback
artifacts as part of source synchronization/cleanup.
