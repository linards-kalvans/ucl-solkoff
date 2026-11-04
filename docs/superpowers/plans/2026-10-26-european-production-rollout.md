# European competitions production rollout

**Authorization:** After production preflight and disclosure of disk, freshness and source-risk gates, the user said “Disk cleaned up elsewhere, proceed.” Treat this as authorization to resume the guarded production rollout, including short maintenance for consistent snapshots and an eventual app/data cutover if every gate passes. No extra approval is required for routine recovery or already-authorized phases. This does not authorize unrelated cleanup, neighbor changes, a git commit/push, a proxy-policy change or unvalidated data replacement.

**Goal:** Deploy the tested multi-competition app and a migration built from fresh production data and refreshed ESPN evidence, preserving a complete rollback to the previous image and schema/data.

**Mode:** tested operational rollout. Existing implementation baseline: 637 passed, 14 documented server/fixture failures; copy-only data/API/browser acceptance passed. Recheck live state and validate the exact packaged image and fresh-source migration; old-copy acceptance alone is insufficient.

## 1. Safety invariants

- Reverify disk headroom rather than assuming external cleanup succeeded. No pruning, deleting backups or other host cleanup in this task.
- Keep the standalone Compose project/service, environment file, loopback port and bind path identities. Preserve public GET/HEAD/OPTIONS-only Caddy policy; no Caddy edit/reload for this app/data rollout.
- Never run old `deploy.sh`, stack-wide down, volume deletion, force push or package-manager dependency sync at runtime.
- Never print secrets or full resolved container/environment/Caddy configuration. Read selected nonsecret identity fields and hashes only.
- Source/audit snapshots and prior candidates remain immutable. Do not put the October 15 database into production.
- All backup, data, manifest, review and rollback artifacts are private. Each release has an independent phase ledger; do not reuse completed October 12/15 cutover state or blindly replay old scripts.
- Failure after stopping the app must restore/restart the old service or clearly report a concrete restoration blocker. After data/schema activation, rollback restores **both** old data and old image. Preserve failed data/logs. Do not start the old image against the new schema.

## 2. Gate A: identity, capacity and immutable image

1. Read-only SSH recheck: health, actual image/container, active-release metadata, original ordered manifest list, data/port bindings, env/Caddy/base-compose hashes, neighbor IDs and available disk. Protect the actual current image as rollback; if external cleanup removed older rollback images, do not assume they still exist.
2. Validate the chosen off-host backup root is private and writable by permission checks. Never expose credentials or change the existing env file.
3. Package the **current working-tree** application whitelist: Dockerfile, pyproject.toml, uv.lock, backend source and frontend assets. `git archive HEAD` alone would omit uncommitted feature work and must not be used as the authoritative source. Exclude env/data/git/tests/redesign, bytecode caches and backup artifacts such as `index.html.bak`; retain all actual runtime imports/assets. Do not delete excluded local files.
4. Pin file list, per-file hashes, bundle checksum, base HEAD and unique timestamp/HEAD/bundle label. Stage only into a new release directory, build while production stays up, retain the current image, and check disk before/after. No broad image pull/prune or source overwrite.
5. Offline dependency smoke with `--network none`: frozen uv environment works with `--no-sync`, runtime dependencies exist and Playwright is absent. No production mount.

## 3. Gate B: isolated startup and real refresh

ESPN now refreshes without a football-data key, so the old “no key means no outbound sync” smoke assumption is obsolete. Do not silently patch startup behavior or skip its data paths.

- Use a separate, verified-unused loopback port and an explicitly isolated copy of the accepted full candidate. Confirm its bind is not the production bind before launch. Label the smoke container uniquely and stop only it after checks.
- For no-network startup, seed a **smoke-only copy** with the six fresh, validated ESPN current-season cache responses and sufficient historical data; never seed production. With no CL key, CL refresh must be truthfully disabled and ESPN refresh must use those cache records. The complete history must prevent an unintended background GitHub/backfill fanout.
- For real-refresh smoke, use a different isolated copy with caches cleared only on that copy, the existing server env file passed directly to Docker without printing it, and explicit DB/data overrides to the isolated mount. Rate-limit CL requests conservatively through smoke-only overrides. Verify actual CL and both ESPN refreshes, schema/identity stability, 36 teams each, season history, details and knockout paths. Record expected source advances; no admin POST to production.
- Startup failures, unexpected backfill/network fanout, mapper conflicts, restart loops or hash/identity drift block progression. Clean up only the known smoke container, preserving evidence and old production service.

## 4. Gate C: consistent fresh snapshot

The user authorization includes a brief, bounded maintenance window. Build/image smoke must pass first.

1. Stop only UCL with its trusted current pinned override and the full original manifest list; verify graceful exit and no remaining data consumers/writers. The uv parent and one uvicorn child are expected processes, not two independent application workers.
2. Archive the complete data bind including any sidecars while no writer is active. Protect the server archive and off-host copy; check equality, extraction safety, readability, schema and row counts. A forced/unclean stop requires successful offline WAL recovery validation before trusting rollback.
3. Restore service on the previous image promptly if candidate preparation will outlast a short window. Record a maintenance deadline; do not hold the site down indefinitely while investigating new data problems.
4. Before final cutover, stop again and capture the actual final pre-cutover source. If business-data fingerprints/beforeimages changed, rebase and verify the affected candidate before activation. Cache/timestamp-only changes still require new source/manifest hashes and a scope-pinned technical review; they are not permission to ignore file-hash gates. On an unexpected rebase, restart the old service and finish offline rather than guess.

For a cache/metadata-only final-source advance, a previously built logical candidate may be retained only after a new pinned source/provenance/manifest chain is prepared and exact-image reconstruction verifies it against that final source. Explicitly compare all raw football inputs and protected CL values; report any excluded cache/derived timestamp differences. Preserve the latest cache where practical and never describe verification-by-equivalence as a physical rebuild. Any changed football record/identity or unproved beforeimage requires actual rebasing/reconstruction or old-service restoration, not a hash substitution.

## 5. Gate D: rebase the tested correction chain

Use the new snapshot and deployment provenance, not the old rehearsal DB:

1. Rebase the investigated correction-only operations against fresh exact beforeimages and source/logical hashes. Preserve the freshest CL data/metadata. Refresh current ESPN evidence from the runtime host, update every adjacent-season index entry sharing a changed URL, and keep archived evidence/data complete.
2. Apply the reviewed 338 qualifying repairs, mixed Roma/Lazio split, known wrong-club/score corrections, alias/root operations and the eight Randers-specific participant corrections on new private copies only. Do not invent new identity changes or widen legacy dates globally.
3. Rebase the complete 151-root / 936-main-event manifest, beforeimages, metadata proof and adoptions/inserts against those copies. Preserve qualifier-only mappings, real Manchester City, Scottish/Maltese Hibernian(s) and Rangers/Randers separation.
4. Source/data or provider-ID contradictions block the migration. Routine source hashes and current-result advances are expected rebasing work, not a new user sign-off gate. Technical/data reviews must pin the exact new manifests before candidate construction.
5. Build with the exact release image/runtime where possible. Verify all preserved source IDs, CL standings/current coefficients, qualifiers, six 36-club EL/Conference tables, per-team source statistics and manual Solkoff, event uniqueness, knockout views and Elo replay. Compare duplicate builds/logical fingerprints and independently check the operation chain.

## 6. Gate E: activation and rollback

- With writers stopped and the final-source candidate accepted, preserve the complete original data directory as a protected sibling. Stage a full replacement preserving ownership/modes and all non-DB data; replace only with the verified candidate DB and no stale WAL from another DB. Keep the bind identity unchanged.
- Create new image-only release/rollback overrides. Rollback points to the actual pre-cutover image, never mutable `latest`. Do not rewrite env/base Compose/Caddy.
- Start only UCL with the new pinned override, monitor health and a single scheduler, verify actual image and data binding, then run public HTTPS GET acceptance for all competitions/seasons, details, stage/knockout views, freshness and mobile/browser behavior. Normal startup refresh may legitimately advance current results; explain changes against pinned inputs, never silently lose source records.
- Publish protected active-release metadata only after acceptance. On failure, stop candidate, preserve failed data, restore old data/schema, select the old image, then restart and verify. Each independent recovery action gets its own error handling; no Caddy reset is needed because policy never changed.

## 7. Completion evidence

Record source/bundle/image/backup/manifest/candidate hashes, exact phase outcomes, downtime, no source data loss, pre/post public/source counts, schema-aware rollback locations, unchanged Caddy/env/base Compose and neighbor identities, real refresh results, test exceptions and monitoring limits. Add a release record only for an actual rollout; no unperformed gate is marked passed.

ESPN is an unofficial source. The user selected ESPN and instructed production progression after the risk was disclosed; this run must not represent public endpoint access or user authorization as an independent licensing attestation. Existing terms/monitoring/test limitations remain explicitly documented.

## 8. Staging checkpoint and security halt

Disk cleanup was independently confirmed: 10.2 GB free before build and about 9.6 GB afterward. Release `20261026T115740Z-0105334-574720437325` packages the current working-tree whitelist (31 files), bundle SHA-256 `574720437325f8c87c5252dc35ecae473222d69858011e976415318b0ddb1958`, image ID `sha256:c84e165cb978f81ea865959907f0c1f39489ab0a917eb03085bdf4b9812dc3d0`. Offline and isolated real-refresh smokes passed; production container, image, data, env, proxy and neighbor identities were not changed. No fresh snapshot or cutover has started.

The rollout is **halted at IMAGE_SMOKED** because a staging diagnostic accidentally exposed the football-data.org API key in internal tool output. The staging artifact was redacted, but a durable diagnostic transcript and provider-side model context may retain the value. The local diagnostic-store directory/database/WAL permissions were restricted without deleting audit evidence. No raw credential is recorded in this plan or release bundle.

The exposed key must be revoked/rotated through the provider and its replacement put into the existing protected server env file over a secure channel, never chat. Before continuation, verify the new credential in memory without env inspection or credential output, record the intentional env-hash change, and rerun only credential-dependent checks. Do not replay the completed image build or assume redaction makes the old key safe. Staging/current/rollback images remain preserved; production stop/migration/cutover stays blocked until credential remediation is complete.

## 9. Explicit user decision superseding the rotation halt

The user subsequently stated that the provider does not support token rotation and explicitly instructed proceeding with the existing token after the exposure warning. Resume the already-authorized rollout under that informed risk acceptance; do not describe the token as rotated or the exposure as erased. The provider limitation is a user statement, not an independently certified fact. Keep diagnostic-store permissions restricted and never inspect/output container environments or credential values again.

Preserve the failed rotation-check records as evidence. Record a separate private risk-acceptance checkpoint, verify only the effective nonsecret configuration and provider response, and use the existing credential solely in memory through the protected server env file. The user's env edit changed its checksum, so establish the intentional new baseline and check effective nonsecret settings before recreation. Do not replay the image build or passed staging work, and do not ask for another approval of the same credential risk. All snapshot, freshness, independent data review and rollback gates remain mandatory.

## 10. Completion checkpoint — production verified with limitations

Release `20261026T115740Z-0105334-574720437325` was activated at
2026-10-26T15:20:45Z. The final authoritative private
`cutover-20261026T150358Z/FINAL-OPERATIONAL-RECEIPT.json` records
`production_verified_with_limitations`, independent corrective review ACCEPT with
zero remaining critical findings, and browser acceptance. The
[actual release record](../../releases/2026-10-26-european-competitions-release.md)
contains immutable hashes, data counts, acceptance, downtime and guarded full
old-schema/data-plus-old-image rollback locations.

**Procedural deviation:** cutover occurred before the scope-pinned review/receipt
for the actual final source. The actual source pin and technical review are dated
15:51:31Z, after the 15:20:45Z activation. Corrective post-cutover reconstruction
proved logical equivalence from the actual stopped source; it did not physically
replace the installed candidate or retroactively satisfy the preactivation review
requirement. Earlier wrong-source/review artifacts and premature verification
metadata were superseded, not erased. This checkpoint does not claim that every
original procedural gate passed in order.

All three current competitions have 36 teams; all 3,245 source match IDs were
retained, with 594 additions (3,839 total). Full public acceptance covered 71
requests and nine views with zero failures; browser acceptance passed 37/37
effective checks with zero errors. Two non-blocking mobile findings remain, and
viewport emulation was not physical-touch testing. The 637-pass/14-known-failure
baseline was not rerun. Credential exposure remains accepted, not rotated;
unverified ESPN terms and existing monitoring limits remain open. The security
history in sections 8–9 remains applicable. No commit or push was performed.
