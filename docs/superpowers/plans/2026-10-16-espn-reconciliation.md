# Safe ESPN Legacy Reconciliation Implementation Plan

**Goal:** Prepare a verified replacement database that connects ESPN Europa/Conference records to existing club history without duplicate clubs, duplicate fixtures or split Elo identities.

**Architecture:** Read-only source snapshot and pinned public payloads produce an explicit review manifest. An offline tool applies that manifest only to a new candidate database; the original snapshot and production data remain untouched. Runtime fail-closed guards stay enabled. Deployment is a later, separately authorized step using a fresh consistent snapshot, pinned image and schema-aware rollback.

**Tech stack:** Existing Python, DuckDB, pytest and ESPN parser/service. Use `uv run` for Python. No new dependency or public maintenance endpoint.

**Current request:** Plan only. Do not implement tooling, fetch new payloads, modify a database, commit, push or deploy during this planning task.

## 1. Evidence and scope

The October 15 audit backup contains 509 team rows and 3,245 matches. Of the names, 251 have leading kickoff artifacts; 183 have a clean-name twin. These numbers identify the audited snapshot, not a future deployment invariant. Current production may have changed.

| Audited window | Stored rows | NULL-stage rows | Interpretation |
| --- | ---: | ---: | --- |
| EL 2024/25 | 189 | 160 | Correct total alone does not prove correct identities/stages. |
| Conference 2024/25 | 153 | 124 | Same issue. |
| EL 2025/26 | 82 | 82 | Coverage/classification needs investigation. |
| Conference 2025/26 | 256 | 256 | Exceeds 153 main-competition fixtures; classify every excess row, do not assume all are duplicates. |

Unprotected copy-only simulation added 188/151 duplicate archive fixtures. Current guards correctly refuse the takeover and roll back all changes. The stored-data problem is broader than missing ESPN aliases: wrong name mappings, old canonical IDs and NULL stages also prevent safe fixture adoption.

The audited schema predates the unreleased competition-keyed snapshots and provider mapping tables. Reconciliation must explicitly migrate a candidate copy before applying a manifest; never rely on a running production process to repair the schema first. At planning time `current_season_year()` is 2026: 2024/25 and 2025/26 are archived, 2026/27 is current. Pin that effective current year in the manifest; a season rollover requires a new inventory and capture set.

**In scope:** EL and Conference seasons 2024/25 through current; provider IDs for every club found in those captures; explicitly evidenced duplicate club clusters needed to connect these clubs to shared European history. References to a confirmed club cluster may extend to CL and earlier seasons to keep one continuous identity.

**Out of scope:** global fuzzy cleanup, changing CL results/provider, deleting unrelated clubs, automatic cross-club mapping repair, pre-2024 stage reconstruction, global date repair, odds/prediction changes, weakening ingestion guards or activating production during implementation.

## 2. Approach and rejected shortcuts

**Recommended: explicit identity manifest plus fixture-by-fixture adoption on a shadow database.** Preserve existing fixture IDs when a unique match is proven. Permit source-window rebuild on the candidate only when every retired row has an explicit classification and beforeimage; this is an escalation within the manifest, not a default delete-and-reimport.

Alternatives considered:

1. **Alias mappings alone:** rejected. They do not fix NULL stages, polluted old participant IDs, duplicate fixtures or split historical Elo.
2. **Replace all EL/UCL windows immediately:** rejected as the default. Counts cannot establish which rows are duplicates, qualifiers, wrong competition or contradictory results. Blind replacement could destroy valid evidence and change unrelated history.

No most-matches heuristic, fuzzy similarity score, stripped `United`/`City` suffix or matching row count proves club equivalence. Never choose a clean root solely because it has more ESPN-season fixtures: that would deliberately retain shared CL/Elo splits.

## 3. Data flow and artifacts

```text
immutable source snapshot + pinned ESPN bodies
                    |
           inventory / candidate clusters
                    |
       explicit reviewed teams + fixtures manifest
                    |
         fresh output DB: schema migration
                    |
           transactional reconciliation
                    |
       derived-table rebuild + offline replay
                    |
         verified receipt + candidate DB
                    |
      later: fresh-snapshot rollout / rollback
```

Private run artifacts live under `/home/linards/.local/share/ucl-solkoff/reconciliation/<run-id>/`, mode 0700 for directories and 0600 for database/payload/manifest files. Inspect parent directory before creating it. Commit only generic tooling, synthetic tests and documentation, never private database snapshots or generated manifests.

Required artifacts:

- `inventory.json`: schema version/fingerprint, table counts, foreign keys and dependency inventory, every scoped row and discovered ambiguity.
- `captures/` and `captures.json`: exact response bytes, request URL, requested competition/season, collection time and SHA-256 per body. The 2025/26 captures are not available yet; capturing and validating them is an implementation prerequisite, not completed evidence.
- `manifest.json`: version, source snapshot hash, migrated-source logical fingerprints, capture hashes, reviewed club roots/member IDs, provider mappings, exact fixture operations and expected postconditions. Review applies to the full manifest hash; changing any entry invalidates review.
- `review.md`: club-equivalence evidence, root rationale, fixture conflicts and their explicit resolutions; distinguish proposed candidates from accepted decisions.
- `beforeimages.jsonl`: complete original rows for every changed/retired record, operation reason and manifest entry ID. Snapshot remains the primary rollback source.
- `receipt.json`: tool/source revision hash, manifest/capture hashes, input/output fingerprints, operation counts, verification results and `verified`/`rejected` state.

Hash logical table content using documented explicit column ordering, stable row ordering, canonical JSON encoding and explicit NULL representation. File SHA-256 establishes artifact identity; logical fingerprints establish the data preconditions after schema migration. Row counts alone are insufficient.

## 4. Club identity decisions

### 4.1 Build complete reviewed clusters

Inventory every ESPN team ID in all six competition-season datasets (2024, 2025 and 2026 for EL/UCL). Existing 2024/2026 alias recommendations are proposals, not an approved manifest; they do not cover missing 2025 clubs. Include clubs appearing only in knockout fixtures, not just standings.

For each ESPN ID, record:

```json
{
  "provider": "espn",
  "external_id": "<actual ESPN team ID>",
  "canonical_team_id": "<reviewed existing or explicitly allocated integer>",
  "member_team_ids": ["<every proven historical ID for this club>"],
  "decision": "reuse | merge-reviewed-cluster | create-confirmed-new",
  "evidence": ["<source and exact club-equivalence evidence>"],
  "expected_beforeimages": ["<complete affected rows, not names alone>"]
}
```

The placeholders above describe required manifest fields, not ready-to-apply mappings. The tool must reject absent/placeholder IDs, unreviewed evidence or conflicting decisions.

Verify club equivalence using authoritative club identity and roster/fixture evidence, not aliases alone. Choose one root in this order: a uniquely verified authoritative positive provider ID already used by live CL; otherwise an established clean canonical row supported by the same identity evidence; otherwise explicit allocation of one new clean root if no safe existing root exists. More than one credible root remains a review conflict until lineage is resolved. Never create a new club merely to avoid deciding between two known duplicate rows.

Specific known reviews:

- **Lazio:** inspect `Lazio Roma` and `SS Lazio` plus kickoff-prefixed members together. If proven one club, unify their relevant European history; do not deliberately leave CL under one ID and EL under another. Inspect the suspicious cached key `21:00 as roma` and its normalized variants against their actual stored targets. Any confirmed AS Roma-to-Lazio cache entry must be removed on the candidate with its complete beforeimage and reason saved in `beforeimages.jsonl`; replace it only if a correct AS Roma target is independently established. Never move such a key with Lazio by association, and verify subsequent AS Roma resolution cannot return Lazio.
- **Benfica and Qarabag:** clean-name variants span CL and EL. Resolve club equivalence and one root across both; no ESPN-only root that leaves a shared-Elo split.
- **Braga:** kickoff-prefixed-only candidates need a clean display name, but first verify the underlying club and all related IDs. A display correction to a preserved ID is preferable when safe; a new root requires explicit allocation and full reference rewiring.
- **Apparently new 2026 clubs:** validate against full canonical inventory, including aliases and older fixtures; absence of an exact name is not proof of a new club.

Retain old `teams` rows as provenance rather than deleting them. A mandatory `team_redirects(retired_team_id, canonical_team_id)` table marks reviewed retired identities; both columns reference existing teams, the retired ID is the primary key and cannot equal its target. Targets must be final active roots: reject cycles/chains in the manifest rather than resolving them by guesswork. Do not trust `team_mappings` updates alone to prevent resurrection.

Create a shared redirect resolver in `backend/team_identity.py`. TeamMatcher must canonicalize cached targets and exact matches and exclude retired rows from fuzzy/new-target candidates; preserve aliases of retired names by resolving them to their root. ESPN mapped/exact identities and football-data team/match/standings ingestion must canonicalize IDs before writing references. The football-data `teams` row upsert itself must resolve the incoming provider ID to its reviewed root or refuse the write; it must not update/recreate a retained retired row by raw ID. Preserve established root metadata unless the manifest/provider identity policy explicitly permits its update.

Audit offline historical repair identity entry points and use the same resolver without expanding its authorized competition/season scope. The shared helper accepts the Database wrapper or a native DuckDB connection through a small query interface; on a legacy schema without `team_redirects`, resolution returns the original ID without creating a table or mutating the source. When the table exists, enforce valid active-root resolution and fail on corrupt redirects. Candidate reconciliation explicitly requires the new table. Tests must prove every supported writer cannot reactivate a retired ID, including cached aliases and a later CL payload at the actual `sync_teams` upsert boundary. This is a bounded redirect integration, not a global fuzzy matcher rewrite.

Allocation for a confirmed new root must reserve an int32 ID against **all** existing team IDs, provider mappings, redirect members/roots and other IDs allocated by this manifest. A deterministic collision must fail or use a recorded alternative; never overwrite an existing row or assume an ESPN/synthetic number is globally unique.

### 4.2 Reference inventory and conflict rules

| Reference | Action for confirmed `old -> root` |
| --- | --- |
| `matches.home_team_id`, `away_team_id` | Repoint all reviewed-club history, including earlier seasons/CL; preserve every unrelated match field unless separately classified fixture reconciliation applies. |
| `provider_team_mappings.team_id` | Update only explicitly listed mappings; verify no provider ID already belongs to another real club. |
| `team_mappings.team_id` | Rewrite only reviewed alias keys; remove confirmed cross-club contamination with beforeimages, replacing only with an independently verified target. |
| `team_redirects` | Insert reviewed retired-to-active-root relationships; no cycles, chains, root collision or reactivation. |
| `standings` | Preserve authoritative live CL row and values; conflict between two snapshot rows requires an explicit winner/equivalence decision. Never sum played/points or silently prefer root values. Rebuild current ESPN snapshots from validated feed. |
| `solkoff_coefficients` | Derived values: regenerate after final matches/standings; never add or average duplicate rows. |
| `elo_ratings` | Derived values: replay final retained European history; no averaged ratings or silent carry-over of retired IDs. |
| `provider_match_mappings.match_id` | Rewire before retiring a referenced fixture; require a one-to-one ESPN event/retained fixture mapping. |

Check actual schema dependencies; an unexpected table referencing a scoped ID blocks the run until its policy is recorded. Candidate roots must exist before references move. Do not change primary-key values in `teams` in place or disable foreign-key checks.

## 5. Fixture reconciliation

### 5.1 Pin missing source data first

At planning time the required season_year integers are **2024 (archived 2024/25), 2025 (archived 2025/26), and 2026 (current 2026/27)** for both competitions. Of these, 2025 is the missing capture set: collect its EL/UCL bodies using the bounded client (normally six requests total, polite spacing, no per-event fanout), and refresh the other pinned captures as needed during later execution. All six competition-season datasets are mandatory. Validate source identity, season, 36 clubs, unique events and complete per-team W/D/L/GF/GA/points consistency.

- Completed seasons: EL 144 league + 45 knockout = 189; Conference 108 + 45 = 153.
- Current season: validated finished records plus resolved future fixtures, no fabricated TBD clubs or missing-score zeroes.
- Unsupported points deductions or partial payloads fail the same existing validation; do not loosen validation for migration.

### 5.2 Classify every scoped legacy row

After virtual club-root resolution, generate candidate correspondences from competition, season, canonical participants, home/away orientation and source dates. Stage/round and leg evidence refine the match; NULL stage is unknown, not permission to assume league. A date discrepancy requires source-specific reschedule/placeholder evidence. Never use an arbitrary date-tolerance window or score alone to establish identity.

Each legacy row receives exactly one explicit disposition:

1. **Adopt:** unique proven ESPN event; preserve legacy match ID; normalize reviewed participants, date, stage, round and per-leg score/status. Record old and new values.
2. **Duplicate of retained fixture:** same real event independently established, equivalent results, one reviewed survivor. Retire duplicate on candidate only, save full beforeimage and rewire any mappings first.
3. **Valid qualifying event:** preserve the result and classify its stage as the explicit canonical value `QUALIFYING`, keeping its evidenced qualifying round and beforeimage. This requires independent qualifying-event evidence in the reviewed manifest, not inference from excess counts. Update both ESPN unmanaged-legacy guard predicates to exclude precisely `stage = 'QUALIFYING'`; use `stage IS DISTINCT FROM 'QUALIFYING'` so NULL/unknown stages stay guarded. Qualifiers remain in the existing pooled European Elo corpus but are excluded from league standings, main-stage fixture counts and knockout pairs. Update `backend/playoff_analyzer.py` pair/stage queries (`get_playoff_pairs`, `get_pairs_by_stage`, `get_current_stage`, including heuristic fallback paths) with the same explicit exclusion. A qualifying `Play-off Round` or high matchday must never trigger main-stage playoff detection. Other out-of-scope/wrong-competition classifications need their own evidenced disposition; an unsupported main-stage row remains a blocker.
4. **Wrongly classified/invalid row:** quarantine from candidate's main match corpus only with explicit evidence and reviewed reason. Never infer invalidity from excess counts.
5. **Conflict/unresolved:** block that candidate. Conflicting final scores, ambiguous participants, missing event or uncertain classification cannot be silently overwritten by ESPN.

Each ESPN event similarly receives exactly one retained or explicitly inserted fixture target. A new future event is legitimate only when not an alias of an existing fixture and the reviewed window has no unaccounted legacy rows. Hash-ID collisions fail before writes. Bijection is based on real event identity, not provider-ID equality alone.

If adoption is impractical in a window, allow an explicitly enumerated candidate-only replacement: every retired row must have one of the reviewed classifications above, all beforeimages preserved, every new row backed by a pinned ESPN event. No `DELETE WHERE season=...` without a complete row-level retirement allowlist.

After reconciliation there must be no unmanaged 2024+ EL/UCL main-competition row within the approved takeover scope. Unrelated pre-2024 archives remain untouched except proven club-ID reference rewiring. Review the mandatory `QUALIFYING` classification/guard change with tests proving an unclassified, NULL-stage or unsupported main-stage row still blocks takeover and no environment/blanket bypass exists. If any qualifying row cannot be evidenced, leave it unresolved and block the candidate.

## 6. Tool and file-by-file implementation

**Verification mode for later implementation: `tested`.** Planning itself is documentation-only (`simple`); no test runs needed now.

### Task A — Read-only inventory and manifest preparation

Create `backend/espn_reconcile.py` with:

```python
def inventory(source_db: str, captures_dir: str) -> dict: ...
def validate_manifest(manifest: dict, inventory: dict) -> None: ...
def build_candidate(source_db: str, manifest_path: str, output_db: str) -> dict: ...
def verify_candidate(candidate_db: str, manifest_path: str) -> dict: ...
```

These signatures specify the proposed interface, not implemented functions. Keep one module initially; split manifest validation only if implementation size requires it. Read source with `duckdb.connect(..., read_only=True)`. The command must reject symlink/path aliases to source, existing output targets, production bind paths and any source mutation. Inventory command defaults to no writes except a separately specified private report directory.

Proposed command contract:

```bash
uv run python -m backend.espn_reconcile inventory --source "$SOURCE_COPY" --captures "$CAPTURES" --report-dir "$REPORT_DIR"
uv run python -m backend.espn_reconcile build-candidate --source "$SOURCE_COPY" --manifest "$MANIFEST" --output-db "$NEW_CANDIDATE"
uv run python -m backend.espn_reconcile verify --candidate "$NEW_CANDIDATE" --manifest "$MANIFEST"
```

No in-place `--apply` mode, public API or environment bypass. Build refuses unreviewed manifest or stale hashes/beforeimages. Reapplication to an existing output is rejected, while two independent builds from identical input must be logically identical.

### Task B — Shadow schema and reconciliation transaction

`build_candidate` creates an independent copy in a protected run directory, applies the existing `Database` schema migration with the explicitly recorded legacy competition default, and validates migrated-source fingerprints. Derive the default from snapshot deployment provenance and confirm it against the legacy snapshot's known competition; the audited source is CL. Do not infer it from whichever competition appears most often in match history. Because `Database` reads `COMPETITION_ID`, set it to this verified value inside the isolated CLI process before construction, restore its previous value in `finally`, and assert every migrated legacy snapshot row has that competition. Refuse uncertain provenance or attribution mismatch. The tool does not start FastAPI, scheduler or source fetching.

Apply reviewed roots/references, fixture operations and provider maps inside `Database.transaction()`. Retain all original team rows, collect beforeimages, reject self-play, dangling references, duplicate event targets and conflicting snapshot keys. Injected failure rolls back the data transaction. A schema or later verification failure leaves the output marked rejected, never publishable; input remains immutable.

Use existing `ESPNClient` parsing and validation through a tool-local recorded-body subclass whose `_request(url)` reads only manifest-hashed response bytes by exact URL. A missing capture raises, never falls back to HTTP or expired cache retrieval. Patch outbound HTTP to raise in verification tests and assert zero requests. Do not call generic ESPN sync to work around unresolved legacy rows.

Required runtime files beyond the tool: `backend/database.py` creates `team_redirects`; new `backend/team_identity.py` resolves/validates active roots; `backend/team_matcher.py`, `backend/data_service.py`, `backend/espn_service.py` and applicable `backend/historical_repair.py` identity entry points consult that resolver. `backend/espn_service.py` implements the precise `QUALIFYING` guard exclusion, and `backend/playoff_analyzer.py` excludes qualifying records from all main-stage pair/current-stage query paths and heuristic fallbacks. `backend/main.py` applies the same exclusion to its inline knockout-debug query so diagnostics do not label qualifiers as main-stage knockout fixtures. Preserve CL feed selection, repair authorization and public API contracts; add no public mutation route or bypass flag.

### Task C — Derived state and deterministic verification

After the data transaction commits on the isolated candidate, rebuild current EL/UCL standings, Solkoff and pooled Elo. Existing calculators call `commit()` internally: run them as separate candidate-only phases, not inside an outer transaction they could prematurely commit. Candidate stays unpublished until every phase succeeds.

Modify `backend/elo_calculator.py` only as necessary to guarantee deterministic replay using `ORDER BY date, id`; document why a fixed tie-break is required when old dates are approximate. Rebuild active ratings from the retained FINISHED European corpus and remove stale retired-cluster ratings explicitly. Never average ratings or add separate IDs' `matches_played` counters without verifying the underlying deduplicated matches.

Ratings can legitimately change for opponents outside a reviewed cluster through the shared Elo graph. Verify exact input lineage and repeatable replay, not identical old rating values. Produce per-root history counts and explain all match additions, removals, score/date corrections and rating changes from approved operations. Preserve live CL standings values and unrelated fixture metadata; any exception must be separately enumerated in the manifest.

### Task D — Tests and documentation

Create `tests/backend/test_espn_reconcile.py` with synthetic legacy and migrated databases, plus `tests/backend/test_espn_reconcile_manifest.py` if validator tests become substantial. Cover:

- legacy schema migration on new candidate only; wrong default/schema/source/capture hashes refused;
- Lazio/AS Roma contamination removed or correctly replaced with full beforeimage, later AS Roma resolution never returns Lazio, multiple clean roots unresolved until explicit evidence, polluted-only club display correction;
- one root for a proven CL+EL club across all historical references, no ESPN-only split;
- snapshot-key collisions with equal and conflicting values; no sums or implicit winner;
- NULL-stage adoption, different old participant IDs, reschedule evidence, explicit duplicate retirement, score conflict, unknown qualifier/classification and hash-ID collision; evidenced `QUALIFYING` rows preserved in Elo but excluded from both takeover guards/league/pairs, while NULL/unsupported stages still block; qualifying `Play-off Round`/high-matchday rows do not alter current-stage detection or either pair API;
- unmanaged row/event remaining, two events claiming one fixture, stale/missing beforeimages and unexpected references;
- interrupted build/transaction/derived phase leaves source intact and candidate rejected;
- two builds have identical logical fingerprints, offline ESPN replay creates zero duplicate teams/matches, and cached/exact/fuzzy legacy matching, CL API ingestion, ESPN mapping and authorized offline repair cannot resurrect retired IDs; redirect cycles/chains and cross-club redirects rejected;
- confirmed-new canonical root allocation checks all existing/manifest-reserved IDs and stays in int32; collision never overwrites a team;
- source path alias/symlink/production path/existing output rejected; no network and no source writes.

Focused command after implementation:

```bash
uv run pytest tests/backend/test_espn_reconcile.py tests/backend/test_espn_legacy_guard.py tests/backend/test_espn_service.py tests/backend/test_espn_edges.py tests/backend/test_historical_repair.py -q
```

Then run full backend/frontend/deploy checks and compare actual failure names, not only counts, with the existing 18-failure backend backlog. No hidden skips/xfails. Add `docs/espn-reconciliation.md` with CLI, manifests and activation/rollback gates; update `docs/espn-provider.md` only after a verified rehearsal. Release record is created only when an authorized rollout actually occurs.

## 7. Acceptance gates before any rollout

All must pass:

- All six datasets for `season_year in (2024, 2025, 2026)` and `competition in (EL, UCL)` are captured/validated, all provider team/event IDs and scoped legacy rows accounted for; missing archived 2025 captures must be supplied. If the effective current year changes before execution, rebuild the scope/inventory rather than treating these three years as permanently current.
- Reviewed club clusters have one verified root across shared history. No arbitrary Lazio/Benfica/Qarabag root and no inherited AS Roma cross-club alias.
- Fixture disposition partition is complete and disjoint; no duplicate real event, no unexplained count change, no mismatched score overwritten. Every retirement has beforeimage and reviewed reason.
- Referential integrity holds in FK and non-FK reference tables. Retired aliases cannot be selected by future import. Unrelated team rows/fixtures remain intact except declared club-root reference rewiring.
- Runtime guards stay enabled. Offline source replay after reconciliation succeeds with zero new duplicate clubs/fixtures. Repeat replay leaves logical rows unchanged, excluding refresh timestamps/cache metadata.
- Current standings and Solkoff reproduce pinned source/independent calculations; archives preserve live snapshots; pooled Elo is deterministic and derives from exactly the approved retained corpus.
- Every source and old backup is unchanged; two independent builds match; deliberately stale manifests and simulated failures are rejected.
- Independent code/data-manifest review accepts the rehearsal; all remaining test failures, source-terms risk and data limitations are explicitly reported. No production readiness inferred from plan completion.

## 8. Future production activation and rollback

This section is a runbook design, not authorization to execute it.

1. Rehearse from the existing audit snapshot first, but do not deploy that old candidate. For deployment, gracefully stop the sole writer under an explicitly approved window, confirm no other consumers, snapshot the full data directory including WAL/sidecars, verify off-host checksum/extraction and retain the old image/override. Follow `DEPLOYMENT.md`, never old `deploy.sh`.
2. Produce a fresh inventory and manifest against that consistent snapshot. Rebase prior decisions only when exact beforeimages/evidence still match; any drift requires renewed affected-entry review. Do not extend downtime indefinitely to resolve surprises: restart old service and stop if a gate fails.
3. Build/migrate/reconcile/verify a new candidate offline using the pinned release image and payloads. Record source bundle/image/manifest/candidate identities. Smoke on an isolated port and candidate copy only, no production API key/network ingestion, no public tunnel.
4. With all writers still stopped, preserve the entire old data directory as a protected rollback sibling and activate the verified candidate in the same bind-mount identity. Ensure original ownership/permissions and no stale WAL from another DB. No deletion of old data, shared images or neighbor services.
5. Start the pinned candidate image with the existing Compose project, service, environment and loopback port. Do not reload/change Caddy for an image-only release. Preserve public method restrictions. Verify all three public competition views, archival selection, details, knockout views, health, one scheduler, stable restarts and no guard failures/identity forks after the first normal refresh.
6. On failure, stop candidate, retain its data/logs, restore the verified pre-migration directory **and** previous image/override, then repeat health/data/security checks. Image-only rollback is unsafe here because schema and canonical data both change; do not attempt ad-hoc down-migration.

## 9. Planning completion

This document authorizes no execution and records no selected production canonical roots. The next bounded implementation unit is the offline inventory/manifest/candidate tooling and its synthetic tests. A separate production decision follows validated 2025 captures, explicit identity/fixture review and full backup-copy rehearsal.

**Planning review:** No canonical roots have been selected. The independent safety review accepted the plan with mandatory redirects, the AS Roma cache correction and explicit `QUALIFYING` stage filtering retained. Documentation verification was performed by inspection; implementation tests are planned, not run. This review does not establish production readiness or authorize implementation or rollout.

## 10. Implementation checkpoint

The user subsequently authorized offline tooling implementation. Implemented `backend/espn_reconcile.py` (tool revision 5, manifest version 2), shared retired-team redirects, explicit qualifier filtering and deterministic Elo replay. The tool exposes inventory, new-candidate build and independent verification; there is no in-place apply or production activation. Fifty tool tests plus nine independent edge tests pass. Four stale data-service failures were repaired with real temporary-database assertions, leaving 14 original baseline failures open.

Independent safety review accepted the implemented refusal rules after capture/season/participant/date/score, provenance, beforeimage, file-safety and candidate-verification fixes. A full-suite run then exposed a concurrency-test handshake coupled to the duration of a 189-fixture import; the pause was moved to the first real fixture write, with explicit worker cleanup and bounded blocking observation. Fresh full-suite verification after that correction: **545 passed, 14 remaining baseline failures**. No new skips/xfails or production changes were introduced.

All six EL/UCL datasets for 2024, 2025 and 2026 were assembled from existing stored public responses with zero new HTTP requests. A read-only inventory on a new copy preserved the audited source hash and 509 teams / 3,245 matches, and reported 151 provider clubs, 680 legacy fixtures and 936 main-stage events requiring explicit review. The unreviewed draft refused before candidate creation. No real canonical roots, real reconciled candidate, production changes, deployment or commit were produced. See [tooling usage and remaining gates](../../espn-reconciliation.md).
