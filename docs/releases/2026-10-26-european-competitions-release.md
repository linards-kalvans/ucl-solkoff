# European competitions production release — 2026-10-26

## Outcome and authority

The multi-competition application and reconciled schema/data were activated at
**2026-10-26T15:20:45Z** at https://ucl.mplytics.eu. The final outcome is
**`production_verified_with_limitations`**, following independent corrective
review **ACCEPT** (zero remaining critical findings) and browser acceptance.
CL uses football-data.org; EL and Conference League use ESPN. The API code `UCL`
means Conference League, not Champions League.

This was an authorized application/data rollout, not an image-only schema-compatible
update. The [rollout plan](../superpowers/plans/2026-10-26-european-production-rollout.md)
records authorization, safeguards, the credential incident and subsequent informed
risk acceptance. No git commit or push was performed.

The authoritative private record is `FINAL-OPERATIONAL-RECEIPT.json` under:

```text
/home/linards/.local/share/ucl-solkoff/releases/20261026T115740Z-0105334-574720437325/cutover-20261026T150358Z/
```

The corresponding server evidence directory is
`/opt/ucl-solkoff/releases/20261026T115740Z-0105334-574720437325/cutover-20261026T150358Z/`.
Paths below that directory are relative unless stated otherwise. Private databases,
credentials and raw diagnostic transcripts are not included in this release record.

## Process deviation: review occurred after activation

**The original cutover preceded the scope-pinned review and receipt for the actual
final stopped source.** Activation occurred at **15:20:45Z**; the actual-source pin
and technical review are recorded at **15:51:31Z**. The earlier source pin referred
to the 15:05 snapshot, not the final stopped source. An earlier review artifact had
an incorrect timestamp/path/hash binding, and metadata prematurely claimed
`production_verified`. These artifacts remain preserved as superseded history.

The corrective post-cutover audit reconstructed the correction and main-stage
chains from the actual final source, proved full logical equivalence, corrected
the public acceptance scope and historical comparator, and hardened rollback
guards. Independent corrective review accepted the result with no current critical
findings. Browser acceptance subsequently completed; the final receipt was issued
at 16:25:59Z and `FINAL-STATE-CHECK.json` confirmed the final state at 16:29:30Z.
**This does not retroactively satisfy preactivation independent final-source
review. Not every original procedural gate passed in its required order.**

## Immutable identities

The application bundle contains 31 working-tree whitelist files, not merely
`git archive HEAD`: `Dockerfile`, `pyproject.toml`, `uv.lock`, backend source and
frontend assets. Base HEAD is `0105334cdede14b35cf2de1077aba922201da61c`;
uncommitted application work is identified by the bundle checksum.

| Item | Identity / SHA-256 |
| --- | --- |
| Release / image tag | `ucl-solkoff:20261026T115740Z-0105334-574720437325` |
| Application bundle | `574720437325f8c87c5252dc35ecae473222d69858011e976415318b0ddb1958` |
| Running image ID | `sha256:c84e165cb978f81ea865959907f0c1f39489ab0a917eb03085bdf4b9812dc3d0` |
| Actual final stopped source DB | `173a63516f0c3ebb8b9effbaa112ddc11a3c42662fe17201d2b2bbec6dfb1867` |
| Complete final pre-cutover archive (`data-cutover.tar.gz`) | `401223dde55d8b003a97c349fce5c1aa00a7e2f00646f72bfe842781df06a2d5` |
| Actual-source reconstructed manifest file | `a31015c1589568a922036d844a93285a75591c74b8f37d64e3e66f0e4c91a0f0` |
| Actual-source manifest canonical hash | `d45056c189d5938f99cc77f5d8595597a843a80860494ccf419147628d91fa63` |
| Physically installed candidate before startup refresh | `51f50242792b2cc5f75b88f008d286d8cd254f5bf3105dc744d28cf139959e0b` |
| Actual-source reconstructed candidate | `eca90083a8e508d6521b8bf5fa902e1e409cbf0703d801daa820fae9c271656a` |
| Shared candidate logical fingerprint | `9bf17555465717d7eeb2ccdcef75d15b8d3758f0cbd9da80349e11ebb8970fd3` |
| Final active metadata | `52bde47337344e02ab94a145e39aef419a76cf8528db8b21bddfad5fd9cb33e0` |

The existing physical candidate was retained; it was **not physically rebuilt and
reinstalled from the final source**. Post-cutover reconstruction from that source
proved equivalent logical tables, with exact-image verification passing 14 checks
and zero failures. Relative to the accepted fresh source, matches, identities,
protected CL values and cache semantics were unchanged. Drift was confined to
503 Elo `updated_at`, 36 standings `last_updated` and 509 coefficient
`calculated_at` values. These timestamp exclusions do not mean byte-identical DBs.
Normal startup refresh can subsequently change the live DB checksum.

## Data and acceptance

All **3,245 source match IDs were retained**, with **594 new matches**, yielding
**3,839 matches, 525 teams, 108 current standings and 108 current coefficients**.
The migration included reviewed qualifying/identity corrections and the
151-root / 936-main-event reconciliation; source preservation does not imply that
incorrect legacy club assignments or scores were left unchanged.

Current 2026/27 public standings at acceptance were:

| Competition | Teams | Played per team | Finished league matches | Sum of standings points | Stage |
| --- | ---: | ---: | ---: | ---: | --- |
| CL | 36 | 3 | 54 | 156 | `LEAGUE` |
| EL | 36 | 3 | 54 | 153 | `LEAGUE` |
| Conference (`UCL`) | 36 | 2 | 36 | 98 | `LEAGUE` |

- Isolated offline/startup and real-refresh smokes passed. Production startup
  refreshed all three competitions successfully with one scheduler; the uv parent
  and uvicorn child are not two application workers. The exact image was healthy
  with restart count zero.
- Full public HTTPS acceptance: **71 requests, zero failures, nine
  competition-season views** (CL/EL/UCL × 2024/2025/2026), covering standings,
  details, stage, pairs, knockout pairs and analysis.
- All six archived views have 36 teams, `FINAL` stage, 23 all-stage pairs and
  eight knockout play-off pairs. CL/EL have eight league games per team;
  Conference has six. The earlier missing-history flags came from a hard-coded
  six-game comparator, not missing data; no historical refetch was needed.
- Browser acceptance: **37/37 effective checks passed**, zero console/page errors,
  failed requests or HTTP 4xx/5xx responses. The 41 raw entries include two
  duplicate IDs and two superseded regex-harness failures; corrected live
  assertions passed against the same DOM. Competition switching, scoped details,
  archived knockout/sidebar behavior and return to the current season passed.
- Headless Chrome used desktop 1440×900 and mobile-width 390×844 viewports, not
  physical touch or touch-gesture testing. Two non-blocking usability findings
  remain: expanded details require horizontal table scrolling to reach the close
  control; the active Final tab label can remain offscreen in the tab strip.

## Runtime invariants and downtime

The standalone Compose project/service remains `ucl-solkoff`, with bind
`/opt/ucl-solkoff/data` → `/app/data` and loopback `127.0.0.1:8003` → `8000`.
Environment, base Compose, Caddy and all 14 neighboring container identities were
unchanged during cutover. Caddy was neither edited nor reloaded. The environment
hash below is the intentional post-user-edit baseline established before cutover,
not a claim that the environment never changed during earlier incident handling.

| Protected configuration | SHA-256 |
| --- | --- |
| Base Compose | `6f789ae189a52c0020771617a2552b91f31ad4d12bb54c0cb529e2831fa2646f` |
| Environment baseline | `10f0222c45de4d4f11b1fc8257f4bf14f6b12077f91a10318403a31a952ec0dc` |
| Caddy | `912002664f9de42b10edb268c2cb6a9ee74bf900ea3388c368d8f48f52c02b23` |

Public write-method probes returned 403 for POST/PUT/PATCH/DELETE/TRACE. HEAD and
OPTIONS probes returned application 405, not successful method support; the proxy
policy remains unchanged. Administrative mutation remains SSH-loopback-only.

Recorded final cutover downtime was **31 seconds**. An earlier stop/snapshot/old-service
restoration window was approximately **125 seconds**, giving **156 seconds** across
these two cutover-task windows. This is not the entire rollout's cumulative outage
measurement; the prior fresh-snapshot maintenance window of 42 seconds was separate.

## Full schema/data and image rollback

Protected `/opt/ucl-solkoff/active-release.json` selects
`compose.release.yml` and `compose.rollback.yml` in the server evidence directory.
Every recreate must use the trusted pinned override, never mutable `latest` or
the base manifest alone. The previous immutable image is
`ucl-solkoff:20261015T133605Z-54564e7-5e0f273a1d7f`, ID
`sha256:ba9d26a54f0427fd354afaee4828acd975b5880343f71f0515de8e480acd9497`.
The full old data directory is retained at
`/opt/ucl-solkoff/data.pre-20261026T150358Z`, with the DB and archive hashes above;
the private off-host archive is retained in the local evidence directory.

Rollback requires stopping UCL and verifying no writers, preserving failed data in
a unique protected sibling, restoring the complete verified old data/schema into
the exact bind destination with ownership/modes intact, verifying destination and
DB hash proof, and **only then** starting the pinned old image and repeating
health/data acceptance. Never start the old image against the new schema.
Corrective `rollback_restore.py`, `rollback_gate.py` and `rollback.py` enforce
preservation/destination/hash gates; fault injection verified that unproved
restoration blocks old-image startup. Production rollback was not executed.
Caddy policy remains in place; no image rollback reopens public administration.

## Limitations and evidence index

The exposed credential was **not rotated**. The user accepted the disclosed risk;
redaction does not remove copies that may remain in diagnostic transcripts or
provider-side model context. No credential value is reproduced here. ESPN remains
an unofficial source with unverified licensing/reuse terms; user authorization is
not an independent licensing attestation. Existing monitoring limits remain:
UCL Caddy access logging is absent, Loki delivery was not reverified by this
rollout, and backup/restore tooling follow-ups remain separate.

The reported implementation baseline is **637 passed / 14 known server-fixture
failures**. The full suite was not rerun for the final acceptance or this
documentation-only update; no clean-suite claim is made.

Supplementary private evidence: `FINAL-CUTOVER-REPORT-CORRECTED.json`,
`PHASE-LEDGER.json`, `FINAL-SOURCE-PIN-ACTUAL-173.json`,
`FULL-TABLE-EQUIVALENCE-173.json`, `EXACT-IMAGE-FINAL-VERIFY-173.json`,
`CORRECTIVE-AUDIT-RECEIPT.json`, `PUBLIC-ACCEPTANCE-FULL.json`,
`HISTORICAL-COVERAGE-173.json`, `BROWSER-ACCEPTANCE.json`/`.md` and
`FINAL-STATE-CHECK.json`. Interim pending states and superseded premature
verification flags in these artifacts do not override the final operational receipt.
