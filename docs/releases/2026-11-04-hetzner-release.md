# Hetzner release and image retention — 2026-11-04

## Outcome

Deployed the current working-tree application at https://ucl.mplytics.eu under
the user's explicit authorization to overwrite the remote container and install
retention. No commit or push was performed. The source bundle, not HEAD alone,
identifies the uncommitted application deployed.

| Identity | Value |
| --- | --- |
| Release | `20261104T113416Z-0105334-14cb0d760c77` |
| Source bundle SHA-256 | `14cb0d760c773cb738d20ca5a6022d700daf12fbf444d34293eeb1e8854d3c0a` |
| Running image | `sha256:e70c3f496e60b3f6b2fb19e157724683db86840f59217b8c417f8d7667ff4ec9` |
| Rollback tag | `ucl-solkoff:20261104T113416Z-0105334-0553949a5b07` |
| Rollback image | `sha256:0553949a5b079b90b006f8642e7790f57627623a7718a23ac78b1f6991c0774f` |

The initial blocker was genuine: protected metadata referred to the missing
October 26 image while the November 3 legacy `latest` image was running from the
base manifest alone. After user approval, the actual live image was preserved
under an immutable rollback tag. Prior metadata was archived, not overwritten
without evidence. That legacy baseline returned 108 mixed-competition standings
rows and a stage HTTP 500; it is not claimed to be functionally correct.

## Safeguards and acceptance

- Only `Dockerfile`, `pyproject.toml`, `uv.lock`, backend source and frontend
  assets were bundled. Secrets, live data, tests and redesign sources were excluded.
- Built with a dedicated ephemeral Docker Buildx builder, removed with its cache
  immediately after the image was loaded. No shared-builder pruning occurred.
- Complete stopped data backups were copied off host, checksum verified, and
  the first archive was fully read and tested against the candidate on an isolated
  restored database: 3,839 matches. Offline database initialization and startup
  health succeeded with `--network none`, no production API key and a separate
  copied data directory. The temporary smoke container and copied data directory
  were removed after verification, as was the newly pulled builder helper image.
- A fresh stopped backup was copied off host and checksum verified immediately
  before cutover. Snapshot stop/restart window: **49 seconds**; final replacement
  window through healthy acceptance: **66 seconds**. These are operation-window
  timings, not continuously measured public HTTP outage durations.
- The running image matches the pinned candidate, is healthy and has zero restarts.
  Sanitized startup-log checks confirm one scheduler start and successful startup
  refresh for CL, EL and Conference League.
  All neighboring container IDs, images and states were unchanged. Base Compose,
  production environment and Caddy hashes were unchanged; Caddy was not reloaded.
- Public HTTPS health, three frontend assets, and five endpoints across all nine
  CL/EL/Conference × 2024/2025/2026 views succeeded: standings, current stage,
  playoff pairs, one team's details and Final knockout pairs. Each standings view
  has exactly 36 unique teams. The harmless public POST policy probe returned 403.
- Historical CL/EL views have eight games per team; Conference has six. Current
  CL has teams on three/four games as matchday four progresses, and current
  Conference has two. No manual refresh or historical maintenance POST was issued.

## Durable operational state and rollback

Server evidence is under
`/opt/ucl-solkoff/releases/20261104T113416Z-0105334-14cb0d760c77/`.
Protected `/opt/ucl-solkoff/active-release.json` records full source/image/backup
checksums, exact active and rollback overrides, acceptance and limitations.
The prior metadata is retained as `previous-active-release.json`.
`release-receipt.json` preserves the published release metadata both in the server
release directory and alongside the private off-host backups. Installed helper
and systemd-unit checksums match the tracked local files.

Private off-host backups are under
`/home/linards/.local/share/ucl-solkoff/backups/20261104T113416Z/`, mode 0700,
with archive files mode 0600. `data-cutover.tar.gz` is the final rollback snapshot;
`data-before.tar.gz` is the earlier verified smoke source.

Rollback is **full data plus pinned old image**, not just a tag change: hold the
deployment lock, stop UCL, verify no writers, preserve the failed data in a unique
protected sibling, verify/extract the complete final backup into the exact data
bind destination with permissions intact, then start `compose.rollback.yml` and
repeat health/data acceptance. Never start the old image against unproved new
data. The legacy rollback image's observed functional limitations remain relevant.
Production rollback was not exercised.

## Retention

Installed `/opt/ucl-solkoff/prune_release_images.py` and enabled the daily
`ucl-image-retention.timer`, with tracked systemd units under `scripts/systemd/`.
The service runs as `deploy` under the shared `.release.lock`. Its dry-run and
first applied run succeeded, with no obsolete release tags to remove. Retention
keeps the active and immediate rollback images and any container-referenced
image, and refuses stale/unverified metadata. `latest` remains an alias of the
rollback image, not an additional image copy. No volumes, data, backups, shared
build cache, unrelated images or neighboring containers were deleted.

## Verification limitations

Backend plus deployment tests: **498 passed, 14 failed**. The failures match the
[documented backlog](../backend-test-backlog.md): six live-server prerequisite
failures and eight Elo/Solkoff fixture failures. The initial 120-second test run
timed out; the completed run used a longer timeout. No clean-suite claim is made.
Browser interaction/UI tests were not rerun; acceptance used direct HTTPS requests
and isolated container startup. Existing monitoring, licensing and credential-risk
limitations were not changed by this release.
