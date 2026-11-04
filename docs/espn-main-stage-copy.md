# ESPN main-stage completion on a private copy

## Result and scope

On October 26, 2026, the user-authorized “Complete main-stage data on copy” phase finished with `INDEPENDENT_COPY_ONLY_ACCEPTED`: **82 checks passed, zero failed**. The full reviewed manifest produced a new disposable candidate using `backend.espn_reconcile`, revision `espn-reconcile/6`, manifest version **2**. The report states `copy_only: true`, `production_ready: false` and `deployment_approved: false`. Generic tool state `verified` certifies the candidate's data, not permission to deploy it.

This completes the main-stage identity and fixture work on this copy. The earlier [correction-only rehearsal](espn-correction-rehearsal.md) remains a historical, partial artifact, not a deployable replacement. Its former “151 unapproved roots / 342 missing events” status no longer describes the full candidate.

No production access, fresh network collection, deployment, commit, push or worktree action was performed. Database bytes, generated manifests, raw beforeimages and private paths remain outside the repository.

## Evidence chain and identity decisions

The immutable corrected base was not used directly as the full manifest's source. A separate Randers prerequisite copy repaired eight Danish Randers participant records that had been stored on Scottish Rangers IDs. The mixed 59-record source cluster became 51 Rangers records plus eight Randers records on a new, distinct identity; the `randers` aliases were corrected. This was an eight-record split, **not a global Rangers-to-Randers redirect**. The full manifest pins that new prerequisite copy as its source.

| Artifact | SHA-256 prefix | Role |
| --- | --- | --- |
| Corrected base | `a36a6ab7` | Immutable previous correction result |
| Randers prerequisite | `a4b26839` | Immutable source for the full build |
| Full manifest | `1cac9e29` | Canonical manifest digest accepted for COPY scope |
| Generic reconciler source | `982e19db` | Reviewed revision-6 implementation |
| Full candidate | `3328ad4a` | Independently accepted database |

These abbreviated identifiers are for orientation, not hash validation. Full digests are retained in private reports. The evidence consists of 18 pinned, normalized responses recorded October 15/16, not fresh live feeds or original HTTP wire bytes. Source, capture and code hashes remained unchanged.

All **151 main-stage roots** were reviewed: 137 existing and 14 confirmed-new clubs. The result has **117 flat redirects** (13 retained plus 104 new) and **154 provider-team mappings**, including three preserved qualifier-only mappings. No retired references, canonical/provider collisions or visible kickoff-prefixed main-stage root names remained.

There were **123 proof-backed existing-root metadata updates**, using NULL-source code/crest fills, with **25 kickoff-name cleanups** within the updates. New roots received metadata from their own pinned provider records. Full beforeimages were checked, and nonempty source metadata, including protected CL metadata, was preserved. The additive optional manifest contract is documented in [reconciliation tooling](espn-reconciliation.md#optional-team-display-and-metadata-operations-revision-6); it introduces no application/runtime API change or additional reconciliation tool.

## Architecture decision: extend the existing offline manifest

### Context

Full main-stage completion needed clean root presentation and pinned code/crest metadata without discarding earlier corrections or weakening complete reconciliation. The correction-only profile intentionally cannot insert the missing events.

### Decision

Reuse the generic full reconciler and version-2 review/build/verify workflow. Add optional `team_updates` and capture-proven new-root metadata, bind proof to the root's own provider ID, and retain exact beforeimages and missing-only metadata fills. Keep the Randers prerequisite as a separately reviewed immutable input rather than a global identity merge.

### Consequences

Existing version-2 manifests remain valid, and no third tool or application/runtime API is needed. Metadata changes participate in independent reconstruction and preservation checks. Full-copy acceptance remains separate from the correction profile and from any future production rollout.

## Fixture accounting and preservation

The 936 captured main-stage events partition exactly into **342 existing 2024/25 adoptions** and **594 inserts**: 342 for 2025/26 and 252 recorded 2026/27 fixtures. Each event maps to exactly one match. All **3,245 old match IDs** survive, yielding **3,839 matches**, with no deletions. The prerequisite's **511 teams** become **525**: 510 from the corrected base, one Randers identity and 14 new main-stage clubs. Original team rows remain as provenance.

- All **338 qualifying rows** retain their dates, scores and other nonidentity fields. Only **50 same-club canonical participant slots** changed in the full build. Earlier wrong-club corrections remain intact.
- All **1,516 CL fixtures** retain every field except **304 proven same-club canonical identity slots**. All **36 CL standings and 36 CL Solkoff/PPG values** remain unchanged.
- Fifteen EL 2024/25 Manchester United participant records stored on Manchester City IDs were corrected individually to United root `66`. City IDs were not globally merged into United, and unrelated City history was preserved.
- The Lazio–Bodø/Glimt second leg is represented exactly once at **April 17, 2025, 19:00 UTC**, **3–1 after extra time**, correcting the stored April 10 date and 2–3 shootout tally during adoption. This repairs the per-leg result, not the analyzer's shootout-advancement limitation.

## Completed dataset shape

`UCL` below means Conference League, not Champions League.

| Dataset | EL fixtures | Conference fixtures | Clubs per competition |
| --- | ---: | ---: | ---: |
| 2024/25 completed | 189: 144 league + 45 knockout | 153: 108 league + 45 knockout | 36 |
| 2025/26 completed | 189: 144 league + 45 knockout | 153: 108 league + 45 knockout | 36 |
| 2026/27 recorded snapshot | 144 league | 108 league | 36 |

Every completed league table has eight games per EL club or six per Conference club. Across all six datasets, per-team played, W/D/L, GF/GA/GD and points match pinned standings; the current snapshot has **54 finished and 198 future fixtures** across EL/Conference. Current stored standings and coefficients each contain **108 rows**, 36 per competition across CL, EL and Conference. Archived views reconstruct 36-club league tables; qualifiers do not fabricate league standings. Historical tie ordering can follow the application's declared policy without changing these statistics.

## Independent acceptance and application evidence

The independent acceptance reconstructed all **3,839 expected match rows** without trusting the apply function or receipt; every row matched. All 936 operations matched pinned dates, status, participants and scores. Manual opponent-PPG calculations agreed with current coefficients and all six EL/Conference season views. A pure-Python Elo replay matched all **405 active ratings** within **2e-4**, with exact match counts. Two fresh builds matched all **nine logical tables** after declared timestamp exclusions; byte-identical database files are not required.

Elo is a measured replay, not a production forecast: the corrected base's 492 stored rating rows become 405 active rows after canonicalization and full event completion. Of 390 shared IDs, 335 ratings changed; the maximum absolute delta was approximately 165.515. Complete event coverage and shared identity consolidation can propagate changes through the pooled graph even while CL standings/PPG remain unchanged.

Native FastAPI acceptance used a read-only database wrapper and blocked outbound HTTP. Competition/season selection, standings, team details, current stage, knockout pairs and pair analysis passed for **EL and Conference × 2024, 2025 and 2026**. Default CL behavior matched explicit CL 2026. Completed seasons exposed one Final pair and eight Round-of-16 pairs; qualifiers were excluded. Invalid competition/season returned 400, unknown teams returned 404, and the candidate hash remained unchanged.

Actual Chromium rendered 36 rows matching API team sets in all six views, opened Solkoff details and rendered historical Final/Round-of-16 views. At **375px**, there was no page horizontal overflow, and there were **zero unexpected JavaScript console/page errors**. The browser's **50 API calls** were served locally from the read-only wrapper. All **856 external CDN/image/font/analytics attempts** were intercepted with empty responses, with no external payload fetched. Existing analyzer UEFA draw fallback requests were blocked; stored pairs supplied the expected fallback. These checks do **not** establish logo-network availability, live feed compatibility or production security.

The earlier `EXECUTION.json` and `MAIN-STAGE-SUMMARY.md` deferred API/browser checks to independent follow-up. The later `INDEPENDENT-ACCEPTANCE.json` and `.md` record those checks as completed. They supersede that earlier deferral, not the earlier execution record. `DATA-REVIEW.json` records accepted COPY-scope data review; its checked canonical digest agrees with execution/independent acceptance, while its trailing manifest summary contains an inconsistent digest. The accepted identifier above follows the later independently checked input identity, not that stale summary field.

The implementation handoff reports **26 new metadata tests and 155 regression tests passed**. The latest full-suite result is **637 passed, 14 failed**: six known server-prerequisite failures and eight known fixture failures (three Elo, one details, four Solkoff), with no skips. The provider-ID/root mismatch rejection is covered, including a foreign provider record that is present in the pinned capture. These are recorded implementation results, not tests rerun for this documentation-only checkpoint.

## Remaining boundaries and future rollout

Copy acceptance is complete. Future production work must use a fresh consistent snapshot, reconcile and review the exact new source/manifest, perform isolated application checks, address provider terms/reuse and security policy, and retain a tested backup/rollback covering **schema/data and the previous image**. No further approval cycle is implied for this already accepted private copy, and it cannot substitute for a future production decision.

Recorded captures do not establish current production or feed freshness. Unofficial ESPN terms/reuse risk remains unresolved. Correct per-leg scores do not add shootout-winner inference: a tied aggregate still cannot establish advancement. Do not bypass takeover guards, use the old deployment shortcut or attempt image-only rollback after a schema/data migration failure.
