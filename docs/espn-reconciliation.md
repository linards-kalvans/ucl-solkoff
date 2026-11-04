# Offline ESPN reconciliation tooling

## Status and boundary

The offline tool is implemented; **production reconciliation and deployment have not been performed**. It does not select club identities or approve a migration for the operator.

Use it to inventory an immutable snapshot, apply an explicitly reviewed manifest to a **new candidate database**, and verify that candidate against the source and pinned ESPN bodies. There is no in-place apply command, public maintenance endpoint, network fallback or guard-bypass switch.

The detailed design and activation/rollback gates are in the [implementation plan](superpowers/plans/2026-10-16-espn-reconciliation.md). ESPN source limitations remain in [provider documentation](espn-provider.md).

This page describes the **full-takeover** tool (`espn-reconcile/6`, manifest version 2). The [main-stage copy completion](espn-main-stage-copy.md) has independently accepted the full reviewed scope on a private candidate; its generic `verified` state is data verification, not deployment authorization. The separate [copy-only correction rehearsal](espn-correction-rehearsal.md) uses `espn-correction-rehearsal/5`, profile/manifest version 3 and a `.rehearsal-review.json` receipt. Its successful state is `rehearsed`, with production readiness and full takeover both false. Do not interchange the manifests, review receipts or acceptance rules.

## 1. Prepare a private offline bundle

Use a consistent, checkpointed snapshot, not a live database. The source must be a regular single-link file without symlink/path aliases or active WAL/temporary sidecars. Production bind paths `/opt/ucl-solkoff/data` and `/app/data` are refused.

Keep snapshots and generated artifacts outside the repository in a private run directory. New tool-written files are mode 0600; newly created report directories are mode 0700. Protect an existing run/report directory yourself; the tool does not change permissions on shared existing directories.

Example layout:

```text
private-run/
  source/
    ucl.db
    source.provenance.json
  bundle/
    captures.json
    bodies/
    manifest.json
    manifest.review.json
  report/
  candidate.db                    # must not exist before build
  candidate.db.beforeimages.jsonl  # must not exist before build
  candidate.db.receipt.json        # must not exist before build
```

### Source provenance

If legacy `standings` or `solkoff_coefficients` lack competition keys, `source.provenance.json` must independently establish the snapshot's configured competition. Required fields are `version`, `source_sha256`, `configured_competition` and `evidence`; optional `deployment` records release details. Bind the evidence to the actual source hash and deployment record. Do not infer a competition from the most common match in a pooled historical database.

Unknown legacy provenance is reported as pending and prevents candidate creation. Already competition-keyed snapshots need no invented legacy attribution. Schema migration happens only on a private scratch/candidate copy; the source remains unchanged.

### Pinned response bodies

`bundle/captures.json` indexes bodies by `competition`, `season_year`, exact `url`, `sha256` and relative `path`. Store the exact bytes being pinned and preserve their collection/provenance record. Captured normalized cache bytes must be described as such, not claimed to be original HTTP wire bytes.

Current tool revision `espn-reconcile/6` uses manifest version **2**, with a pinned current season start year **2026**. All six datasets are required for a build:

- EL and Conference (`UCL`) for 2024/25, 2025/26 and 2026/27.
- Each dataset needs its season standings and the two calendar-year scoreboards.
- Adjacent datasets share scoreboard URLs: repeated URLs must pin identical bytes/hash.
- Parsing uses each event's actual season, not its calendar-query label. Conflicting duplicate events fail; unresolved future placeholders are not fabricated.

Inventory may report incomplete capture coverage; build cannot proceed with it. The recorded-body client never falls back to HTTP, even when a capture is missing or a former cache TTL has expired.

## 2. Inventory without changing the source

Set the variables below to paths inside the prepared private run directory. Run from the repository root:

```bash
uv run python -m backend.espn_reconcile inventory \
  --source "$SOURCE_COPY" \
  --captures "$BUNDLE_DIR" \
  --report-dir "$REPORT_DIR"
```

The command writes a new `inventory.json` and prints JSON. Existing reports are not overwritten. The inventory includes source/schema/logical hashes, provenance, capture coverage, provider clubs/events, scoped unmanaged rows and reference columns.

**`can_build=true` means the source/provenance inventory permits migration, not that the bundle or decisions are ready.** It does not incorporate capture completeness: check `capture_status.complete` separately, then obtain manifest review. Build rechecks every required capture. NULL-competition/unknown-date records cannot silently disappear from the reconciliation scope. Unexpected reference tables/columns, readable malformed captures and inconsistent source data fail closed.

## 3. Review the manifest

The operator must explicitly review the full scope, including earlier European references needed to avoid split club histories. The tool does not use fuzzy matching or choose a root by match count.

Manifest version 2 requires these top-level fields:

```text
version, source_sha256, source_path, legacy_competition_default,
source_logical_hashes, capture_index, captures, club_roots,
team_redirects, team_mapping_operations, fixture_operations,
standings_decisions, reference_rewiring
```

`source_path` pins the snapshot used again during verification. `capture_index` is `captures.json`, beside the manifest. Unknown fields/operations and placeholder decisions are refused. The synthetic examples in `tests/backend/test_espn_reconcile.py` (`make_manifest`, `write_manifest`) document the concrete schema; **their generated identities/review receipts are test data, not production approval templates**.

| Decision | Required safeguard |
| --- | --- |
| Club roots and members | Explicit provider-to-root mappings, evidence, int32/collision checks; flat retired-to-active-root redirects. Optional declared root beforeimages are enforced. |
| Legacy alias cache | Explicit retirement or verified replacement, with beforeimages; never move an AS Roma alias to Lazio by name association. |
| Adopt/insert fixture | Exactly one captured event/target; participants, date, status, stage, round and per-leg scores must match the pinned event. Adoption preserves the legacy fixture ID. |
| Correct old data | Exact legacy beforeimage and structured capture-pinned correction evidence; free-form reasons alone are insufficient. A manifest date cannot override the pinned event date. |
| Qualifying fixture | Hashed qualifying-event proof must match season, competition, date, score, round and the actual legacy participants through reviewed roots. NULL competition can be attributed only through that proof. |
| Duplicate/quarantine | Individual retirement allowlist and full beforeimage, not blanket season deletion. Conflicting/unresolved rows remain blockers. |
| Snapshot collision | Explicit same-cluster winner/loser decision; never sum records or silently select root values. |
| Reference rewiring | Only allowlisted team-ID columns and declared retired-to-root relationships. Scores/names/arbitrary columns cannot be rewired. |

Every scoped legacy row and every captured main-stage event must be accounted for. Both forward and inverse ESPN mappings are checked; a pre-existing scoped mapping absent from the pinned captures also blocks verification.

### Optional team display and metadata operations (revision 6)

`team_updates` is an additive, optional top-level list. Version-2 manifests that omit it remain valid; the manifest version and CLI are unchanged. Each entry requires `canonical_team_id`, a unique `entry_id`, a reviewed `reason`, `expected_beforeimage`, nonempty `fields`, and `metadata_proof`. Targets must be declared canonical roots, with at most one update per root. Unknown fields and duplicate entry IDs are refused.

For an existing root, `expected_beforeimage` must match the complete source team row. For a confirmed-new root it must be `null`, and the source row must be absent. `fields` accepts only `name`, `code` and `crest`, each a non-placeholder string:

- `name` must equal either the actual source name with its leading kickoff artifact removed or the pinned provider spelling. This is not arbitrary renaming or identity substitution.
- `code` and `crest` must equal usable values in the pinned provider record. Existing values may be filled only when `NULL` or empty; nonempty source metadata, including protected CL metadata, cannot be overwritten. The accepted copy used only NULL-source fills.
- `metadata_proof` requires exactly `url`, `sha256` and `provider_id` as strings. URL/hash must identify a declared pinned capture; its provider record must match the root's own `external_id`. A different provider ID is refused even when that other record is present in the captures.

Confirmed-new `club_roots` may instead carry optional `code`, `crest` and `metadata_proof` creation fields. Code/crest require proof, the declared name must match that provider record, and proof must identify this root's provider ID. Existing roots must use `team_updates`, not creation metadata. A separate update cannot fill metadata already supplied at root creation.

Updates are proved before application, recorded in beforeimages and included in independent expected-state and preservation checks. Source hashing, full event/root coverage, review digest binding and verification requirements remain unchanged; no runtime/API metadata operation or third reconciliation tool is introduced.

The sibling `manifest.review.json` contains `manifest_sha256`, calculated using `backend.espn_reconcile.manifest_sha256(manifest)`. It binds the reviewed JSON contents, not merely the filename. Changing any decision requires a new review and digest. **This receipt is not a cryptographic signature, proof of independent human review or permission to deploy.** It must only be created after the actual identity/fixture review.

After that review, record its digest with this explicit step (there is no separate review CLI subcommand):

```bash
uv run python - "$BUNDLE_DIR/manifest.json" <<'PY'
import json
import os
import sys
from pathlib import Path
from backend.espn_reconcile import manifest_sha256, review_receipt_path

path = Path(sys.argv[1])
manifest = json.loads(path.read_text(encoding="utf-8"))
receipt = {"manifest_sha256": manifest_sha256(manifest)}
fd = os.open(review_receipt_path(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(receipt, handle, sort_keys=True)
    handle.write("\n")
PY
```

The command refuses an existing review receipt. If the manifest changes, preserve the old review artifact and perform a new review before creating a replacement; do not treat recalculating the digest as review itself.

## 4. Build a new candidate

```bash
uv run python -m backend.espn_reconcile build-candidate \
  --source "$SOURCE_COPY" \
  --manifest "$BUNDLE_DIR/manifest.json" \
  --output-db "$NEW_CANDIDATE"
```

The output database and both sidecars must be absent. They are exclusively created; a failed/partial candidate is not an overwrite target. The source hash and exact logical preconditions are rechecked.

The tool migrates only a copy, applies reviewed mutations inside `Database.transaction()`, then rebuilds current ESPN standings, Solkoff and pooled Elo in candidate-only phases. Calculators commit internally, so derived work occurs after the reconciliation transaction rather than prematurely committing it. Old team rows remain as provenance; shared redirects prevent later cached/exact/fuzzy/API imports from reactivating retired IDs. Elo replay uses deterministic date/ID ordering, not averaged ratings.

Artifacts:

- `.beforeimages.jsonl`: original affected rows with operation IDs/reasons, including snapshot/derived-table rebuilds.
- `.receipt.json`: verified/rejected state, source/manifest/capture/tool identities, checks and resulting logical fingerprints.

A source or schema/correction mismatch refuses the run. An apply/derived/verification failure cannot produce a publishable candidate. No network refresh or scheduler runs during the build.

## 5. Verify independently

```bash
uv run python -m backend.espn_reconcile verify \
  --candidate "$NEW_CANDIDATE" \
  --manifest "$BUNDLE_DIR/manifest.json"
```

Keep the pinned source and bundle available. Verification reads the candidate and source and reconstructs the expected result on a throwaway copy, comparing logical content rather than trusting a recomputed receipt. Unrelated CL result changes, rogue mappings, stale derived rows and retired references are checked. Refresh timestamps/cache metadata are excluded from logical comparisons.

Exit codes: **0** for successful inventory/verified candidate, **1** for failed candidate verification/build receipt, **2** for a refused precondition (`REFUSED: ...`). Inspect both exit status and receipt; neither a created file nor successful inventory proves readiness.

## 6. Verified progress and remaining gates

Implementation verification includes **50 tool tests plus 9 independent edge tests**, covering source immutability, correction/provenance refusal, redirects, classification, paths, rollback/rejected artifacts, deterministic builds and candidate tampering. Further runtime/whole-suite evidence is recorded separately; the complete test suite is not claimed green.

Final full-suite verification after stabilizing the concurrency handshake: **545 passed, 14 failed**. The 14 are the remaining documented baseline failures (six live-server prerequisites, three Elo fixtures, one details fixture and four Solkoff fixtures). The four former data-service mock failures are repaired; no skips or xfails were added. The concurrency regression pauses after a real first fixture write rather than imposing a five-second budget on a full import, retains bounded blocking observation, and always releases/joins workers before closing the database.

A real read-only inventory ran on a new copy of the audited October 15 backup. Both the source and new copy retained their SHA-256 and **509 teams / 3,245 matches**. All six required datasets were assembled **offline**, including 2025/26, from previously stored public responses; no new ESPN request was needed. Inventory found **151 provider clubs, 680 scoped legacy fixtures and 936 main-stage events** needing reviewed decisions. An unreviewed draft was refused before candidate creation.

That inventory was **not full-takeover rehearsal evidence**. The October 24 correction-only candidate subsequently passed 401/401 tool checks, repeated construction matched all nine logical table hashes, and all 3,245 match IDs were retained. At that checkpoint the 151-club draft was unapproved and 342 missing archived main-stage events had not been inserted. See the [historical correction checkpoint](espn-correction-rehearsal.md#recorded-execution-checkpoint-october-24-2026).

**October 26 completion:** the full manifest was reviewed and accepted for COPY scope, using a new immutable Randers prerequisite copy of the corrected base. All 151 main-stage roots are now reviewed on this copy, and all 342 formerly missing 2025/26 events were inserted, together with 252 recorded current fixtures. The final candidate has **3,839 matches / 525 teams**, retaining all original match IDs. Independent acceptance reports **82 PASS / 0 FAIL**, including independently composed expected rows, PPG/Elo, read-only native API and Chromium checks. Two builds match all nine logical tables. See the [complete copy report](espn-main-stage-copy.md) for conservation, datasets and limits.

The completion handoff reports **26 new metadata tests and 155 regression tests passed**; the updated full suite reports **637 passed, 14 failed**, with the same six server-prerequisite and eight fixture baseline failures, and no skips. These supersede the earlier suite counts as the latest reported evidence; tests were not rerun for this documentation-only checkpoint.

The audit backup is historical; production activation still requires a fresh consistent snapshot, a reviewed manifest and reconciliation for that exact snapshot, isolated smoke, terms/security review and rollback preserving both the previous schema/data and image. These are future rollout requirements, not unfinished acceptance steps for the completed private copy. Never use the old `deploy.sh`, disable guards or swap only an image after a failed schema/data migration.

ESPN endpoints remain unofficial; source terms/reuse risk is unresolved. No deployment, production data mutation, commit or push is implied by this tooling.
