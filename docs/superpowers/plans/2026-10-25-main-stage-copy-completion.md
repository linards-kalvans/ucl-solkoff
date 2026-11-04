# Main-stage completion on corrected copy

**Authorization:** User selected “Complete main-stage data on copy.” This permits reviewed local identity/event reconciliation and new disposable candidates only. No production access, deployment, source-copy writes, commit, push or worktree creation.

**Goal:** Populate complete ESPN main-stage datasets for Europa and Conference seasons 2024/25, 2025/26 and the recorded 2026/27 snapshot, preserving prior correction work, CL data and qualifying history.

**Architecture:** Reuse the existing full `backend.espn_reconcile` manifest/review/build/verify workflow on the immutable correction candidate. Do not add another reconciliation tool or bypass full event/root coverage. Add only an optional, proof-backed team display/metadata update operation needed for clean existing-root presentation. Private manifests and database artifacts stay outside git; receipts/reports explicitly state copy-only authorization, not deployment approval.

**Mode:** tested, no mandatory tests-first ordering. Existing full-suite baseline: 611 passed, 14 documented failures. Preserve all unrelated staged/unstaged work.

## 1. Verified input and expected data shape

The immutable base is the prior correction candidate (3,245 match IDs, 510 retained teams, 338 qualifying rows, 13 flat redirects, ten provider-team mappings, 36 CL standings and coefficients). Pin its file hash and logical inventory before preparing a new manifest. The main evidence is 18 recorded, normalized response bodies covering all six competition-season datasets; no live freshness claim is made.

| Competition | 2024/25 | 2025/26 | Recorded 2026/27 |
| --- | ---: | ---: | ---: |
| Europa | 189 | 189 | 144 |
| Conference | 153 | 153 | 108 |

Reconcile 342 existing 2024/25 rows by adoption, preserving IDs, and insert 594 absent events (342 archived 2025/26 plus 252 recorded current fixtures). Final expected total: **3,839 matches**, with no source match deletion. Preserve all 338 qualifiers as `QUALIFYING`, all 1,516 CL fixtures and existing CL standings/PPG values.

The source identity audit proposes 151 provider roots: 111 backed by existing fixture concordance, 24 existing exact-name records, two translated-name aliases, and 14 previously absent clubs. These are proposals until independent evidence review. The recorded corrected base, not the original October 15 source, supplies exact beforeimages for every mutation.

## 2. Identity decisions and preservation rules

- Reuse the ten established provider mappings and thirteen redirects. Seven provider roots occur in main-stage scope; preserve the three qualifier-only mappings outside that scope.
- Derive main-stage clusters using pinned main/qualifying participant IDs, oriented fixtures, raw names/countries, existing redirects and protected CL membership. No fuzzy matching or removal of meaningful `City`/`United`/plural tokens.
- Prefer an existing protected CL root, otherwise a verified clean active source record. Merge all proven same-club fragments across shared history; do not select separate roots by competition/year. Keep original team rows as provenance.
- There must be no source ID or canonical ID claimed by two different real clubs, no self-play, no redirect chains/cycles and no change to an existing redirect target without an explicit reviewed operation. The current audit reports zero flattening conflicts; preserve that property.
- Review Red Star Belgrade ↔ FK Crvena Zvezda and Kairat Almaty ↔ FK Kairat against primary club/UEFA identity references. Record the URLs/evidence in the private manifest ledger.
- Confirm the 14 new clubs are absent from the full corrected-source inventory, including aliases. Allocate distinct, explicit int32 IDs after checking all existing and reserved IDs; do not invent a new club merely to avoid deciding an existing alias.
- The initial audit proposes 104 additional redirects and 25 existing-root kickoff-prefix cleanups. Recompute and verify those counts rather than forcing data to match them.

### Newly verified Randers/Rangers exception

The alias scan found Danish Randers fixtures mixed into the Scottish Rangers root and its kickoff-prefixed fragment. Eight exact match IDs are proved by the pinned 2021/22 Conference source: `-945539291`, `-598456382`, `-501067216`, `-458811722`, `-410138651`, `-344205818`, `-211103155`, `-84852366`. Deleting only the wrong cache key would leave these fixtures on the wrong club.

Prepare a separate disposable prerequisite copy with a collision-checked Randers FC identity, move only those eight participant slots, and repair the two `randers` alias keys. Preserve all Rangers slots and all dates/scores/rounds; do not redirect Rangers into Randers or widen general legacy scope. The private, fail-closed prerequisite script/operation ledger must pin the corrected-base hash, every full beforeimage and the eight raw source lines, and independently prove source immutability and row conservation. It is an intermediate copy artifact, not a production tool or a full-takeover approval; no new public reconciliation CLI is required.

After independent acceptance, use that new prerequisite copy as the immutable full-manifest input. Regenerate hashes, alias operations and rewiring counts; do not reuse the withdrawn manifest prepared from the mixed source. Expected final team count becomes 525 (510 corrected-base teams + one Randers identity + fourteen main-stage clubs), while the expected 3,839 match count is unchanged. This is a necessary identity repair within the requested main-stage completion, not a request for another user approval gate.

## 3. Fixture corrections and proof

- Adopt each 2024/25 event exactly once, using its pinned source/ESPN identity, complete source-row beforeimage and structured correction evidence. Old date-only values require explicit timestamp normalization, not a silent mismatch exemption.
- Audit identified 164 calendar-day errors, 178 timestamp-only normalizations, 284 stage and 56 round changes, and participant canonicalization. Every change must appear in the private operation ledger; counts are audit hypotheses until checked independently.
- **Fifteen Manchester United fixtures are stored on Manchester City IDs 65 or -49777.** Correct only those event participants to proven United root 66. Never redirect either City ID into United or alter actual City history/CL state. Keep these contaminated IDs out of United's cluster.
- **Lazio–Bodø/Glimt, event 733642:** stored `2025-04-10`, 2–3 is the shootout tally; pinned second leg is `2025-04-17T19:00Z`, 3–1 after extra time. Verify raw `2-3 pen. 3-1 a.e.t.` evidence, then correct only that adopted row.
- Preserve all previously corrected qualifying dates/scores/club slots, the mixed Roma/Lazio split and the three older exception repairs. Those qualifiers are not main-stage adoption/deletion targets.
- The full event partition is 936 events = 342 unique adoptions + 594 unique inserts. Collision-check all new fixture IDs against the source and each other. Wrong competition/season, conflicting proof, missing beforeimages or an unaccounted legacy main row blocks the build.

## 4. Minimal code extension

Modify only `backend/espn_reconcile.py` as needed to support an **optional `team_updates`** list in the existing reviewed manifest. Existing manifests without it remain valid. Do not weaken source hashes, capture coverage, event validation, row partitions, review digests, path protections or independent candidate verification.

Each update targets a declared root, has an entry ID/reason, exact beforeimage and explicit after fields. Permit:

- `name`: remove the actual leading kickoff artifact while preserving the remaining established club name, or apply another explicitly source-proven reviewed spelling; no arbitrary club substitution.
- `code`/`crest`: fill missing metadata from the matching provider-team record in pinned captures, not overwrite established nonempty CL metadata. Capture URL/hash/provider ID must identify the same reviewed root; mismatched/forged values refuse.

Confirmed-new roots may receive their code/crest from the same pinned provider record. Preserve all other source team fields. Record update beforeimages, apply roots before dependent references, and include metadata changes in independent expected-state/conservation checks. If DuckDB constraints require shadow rebuilding, retain constraints and exact original team IDs; no blanket FK disabling.

Create `tests/backend/test_espn_main_stage_metadata.py` for optional-operation compatibility, correct prefix cleanup, root-target and beforeimage refusal, provider metadata proof/collision, missing-only metadata fill, protected CL preservation, transaction failure and candidate tamper detection. Reuse existing synthetic capture/database fixtures. Update existing fixture helpers only if necessary; do not change unrelated tests to hide failures.

## 5. Private manifest and execution

In a new protected private run directory, prepare a complete inventory, identity evidence ledger, beforeimages, 151 root decisions, new/retained redirects, team updates, 936 fixture operations and source/capture hashes. Resolve routine aliases using the recorded evidence; stop only genuine contradictions, not every spelling variant.

No review receipt until separate code and data/manifest review accept the exact hash for **copy-only main-stage completion**. A generic `verified` result means data verification on this private copy, not production approval; record `production_ready: false` in the run report. Do not reuse the correction-profile receipt for the full manifest.

Build a new candidate and a second independent repeat with the existing generic CLI. Never overwrite the corrected base, original audit backup, prior candidates or sidecars. Hard-block outbound HTTP during preview/build/verification and compare input hashes before/after.

## 6. Acceptance and verification

- Source corrected-copy file and evidence hashes unchanged; original 3,245 match IDs retained; final 3,839 rows if no extra issue is proved. All new events hash-collision-free and uniquely mapped.
- Preserve 338 qualifying rows and their correction afterimages. Preserve existing CL fixture metadata/standings, allowing only proved same-club canonical ID rewiring; current 36 CL Solkoff values must remain unchanged.
- Completed 2024/25 and 2025/26: **36 clubs each**, EL 144 league + 45 knockout, Conference 108 + 45; every club's W/D/L, played, goals and points match pinned standings. Preserve the app's declared historical tie-order policy; distinguish tie ordering from incorrect statistics.
- Recorded 2026/27: EL 144 and Conference 108 resolved fixtures, 36 live standings each; all finished/results counts match the recorded capture, not a claim of live freshness. Three competitions each have separate current standings/coefficients.
- Manual opponent-PPG calculation agrees with all Solkoff values; qualifiers never count as league opponents. Completed knockout views expose correct stage pairs and two-leg aggregates; tied shootout advancement remains the documented separate limitation.
- No canonical/provider collisions, dangling/retired references, self-play, duplicate events, unclassified main rows, visible kickoff-prefixed main-stage club names or loss of qualifier-only mappings.
- Native read-only FastAPI tests cover seasons, standings, breakdowns, stage detection and knockout/pair analysis across all competition/season combinations; any potentially mutating app smoke uses a further scratch copy with network/scheduler disabled.
- Recompute all expected rows and Elo independently, compare repeat logical tables, report actual rating/standings changes and remaining old metadata limitations. Do not trust a receipt alone.
- Run focused new/old ESPN, identity, season and knockout regressions, then full suite and compare the fourteen baseline failure names. No hidden skips/xfails.

## 7. Documentation and scope boundary

Update `docs/espn-reconciliation.md` and provider/README status only after real copy verification; add a sanitized report for Discord. Private database bytes, generated manifests and beforeimages stay out of git/public diff pages. Show filtered code/documentation diff only when project files change.

Completion of this copy phase does not authorize deployment or assert ESPN licensing/current-feed freshness. Production still requires a fresh consistent snapshot, exact reviewed migration, isolated app smoke, existing security policy and restorable schema/data plus image rollback. No production commands run in this task.

## Execution checkpoint: October 26, 2026

**Complete for COPY scope.** The reviewed full manifest used the new immutable Randers prerequisite copy, not the mixed corrected base. Both inputs remain unchanged. The eight-record Randers split and alias repair were independently accepted without a global Rangers redirect. The generic reconciler is `espn-reconcile/6`, manifest version 2; optional `team_updates` and proof-backed new-root metadata are additive, with old manifests still valid. No additional reconciliation tool or runtime/API interface was introduced.

The full candidate contains **3,839 matches / 525 teams**, retaining all 3,245 source match IDs. All 936 events are accounted for: 342 adoptions and 594 inserts (342 archived plus 252 recorded current fixtures). All 151 main-stage roots are reviewed: 137 existing and 14 new; 117 flat redirects and 154 mappings include three qualifier-only mappings. There are 123 metadata updates with NULL-source fills and 25 kickoff-name cleanups. Nonempty source/CL metadata remains protected, including rejection of proof using a different provider ID even when present in captures.

All 338 qualifiers retain dates/scores, with only 50 same-club canonical slots changed. All 1,516 CL rows retain nonidentity fields, with 304 canonical slots changed; 36 CL standings and PPG values are unchanged. The individual Manchester United/City participant corrections and Lazio–Bodø/Glimt April 17 per-leg correction are accepted without global cross-club merges. All six EL/Conference views have 36 clubs and pinned per-team statistics; completed seasons have 189/153 events, while the recorded current snapshot has 54 finished and 198 future fixtures. Current standings and coefficients each have 108 rows across three competitions.

Independent acceptance reports **82 PASS / 0 FAIL**, including independently composed expected matches, manual PPG, pure Elo replay of 405 ratings within 2e-4 with exact counts, and nine logically identical tables across two fresh builds. Read-only native API and actual Chromium acceptance cover both competitions and all three seasons, details and knockout views, 375px layout and zero unexpected JavaScript errors. Fifty API calls were served locally; 856 external attempts and existing UEFA fallback requests were intercepted/blocked, with no outside payload. This does not test live logos or fresh feeds. These later checks complete the API/browser work deferred in the initial execution summary.

The handoff reports **26 new metadata tests plus 155 regressions passed**, and the full suite reports **637 passed / 14 known baseline failures**, with no skips. No tests were rerun for this documentation-only final checkpoint. Inspection covered the actual private execution/data-review/independent-acceptance reports and targeted generic metadata validators/resolvers, not the entire reconciler module. The private data-review report has an inconsistent trailing canonical-digest summary; the checked digest and later execution/independent acceptance identify the accepted manifest consistently. No private report was edited.

Sanitized results and future rollout boundaries are in [the main-stage copy report](../../espn-main-stage-copy.md); the tooling reference, README and historical status notes now distinguish completed copy work from future fresh-snapshot, terms/security and schema/data-plus-image rollback requirements. Final checkpoint edits are documentation-only. No production access, fresh network collection, deployment, source/test/private-bundle writes, commit, push or worktree action occurred.
