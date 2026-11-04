# Copy-only correction rehearsal

**Authorization:** The user selected “Prepare copy-only correction rehearsal” after reviewing the investigated correction policy. This authorizes local tooling, test fixtures and disposable-copy operations only. It does not authorize production access, deployment, writes to source snapshots, or approving the unresolved full ESPN takeover manifest.

**Goal:** Produce a reproducible, independently verified correction-only candidate and an analytics-impact report from the immutable October 15 audit snapshot and pinned evidence.

**Verification mode:** tested. Add focused synthetic regression tests; run the real rehearsal only after code and operation-ledger review. Preserve every unrelated working-tree edit. No commit, push, worktree, network refresh or production operation.

## 1. Design choice: separate correction profile

The existing full-takeover tool requires complete six-dataset identity/event reconciliation. The remaining 151-club main-stage draft is unapproved; weakening that validator to accommodate a partial rehearsal would create a dangerous bypass.

Add a separate, clearly named `backend/espn_correction_rehearsal.py` command and manifest profile. It may reuse existing safe-path, hashing, schema-migration, recorded-body and transaction helpers, but never changes the full takeover acceptance rules. Its successful receipt says `rehearsal_only: true`, `production_ready: false`, `full_takeover: false`, and `state: rehearsed`; it must not emit the generic `state: verified`/production-ready receipt or a full-takeover approval stamp.

Correction-only scope preserves all original match IDs and rows. The 342 missing 2025/26 main-stage events are reported as a separate pending insertion, not silently folded into these repairs. Current EL/UCL main-stage population and unresolved legacy-main guards may remain incomplete; report that honestly.

## 2. Evidence and input boundary

Use only already captured private evidence:

- Immutable audit snapshot and its source-hash-pinned CL provenance.
- Four-club/AS Roma case: root proposals, exact incident-row split, eight fixed-commit historical source files, ESPN participant evidence and the three older Roma exceptions.
- Fixture case: canonical 338-row qualifying ledger, two pinned ESPN qualifying scoreboards, four pinned raw source files, three main-stage shootout corrections, and source-schema beforeimages.
- Recorded main-stage captures solely to verify/report the separately pending main-stage events; no ingestion of unreviewed records.

Inventory and preview read source read-only. A private builder creates a new, exclusive output under a protected rehearsal directory and migrates only that copy. Refuse live bind paths, source/output aliases, symlinks/hard links, active WAL, existing output/sidecars, uncertain legacy default, stale body/source hashes and changed exact beforeimages. Inputs remain immutable and private generated manifests do not enter git or public diff uploads.

The operation manifest and a separate review receipt pin the exact plan hash and `scope: copy-only-corrections`. The receipt records user authorization scope plus independent technical review; it is not a production approval, signature or licensing attestation. The draft full-takeover manifest remains unapproved.

## 3. Closed operation scope

1. **338 qualifying fixtures:** retain IDs and rows, set `QUALIFYING`, preserve competition, use the pinned qualifier's UTC date and per-leg result/round. Verify the distinct qualifier league slug and event season 2025; do not pass qualifier bodies through the main-stage slug check or the 36-team standings validator. Match raw source participant/country evidence with explicit reviewed bindings; never fuzzy-match new identities. Expect 299 differing calendar dates and 11 shootout-score corrections; timestamps may change precision as explicitly declared.
2. **Five wrong-club qualifying rows:** correct only the identified participant slots: two Rostov-to-RFS, one La Fiorita-to-Tre Fiori, two Scottish Hibernian-to-Maltese Hibernians. Preserve the real clubs' other fixtures. Create exactly one distinct, collision-checked Maltese Hibernians identity, backed by ESPN 2499 versus Scottish Hibernian 258; no global redirect between different clubs.
3. **Three main-stage shootout rows:** correct the proved per-leg scores to 0–1, 0–2 and 5–4. Date/participants/stage remain unchanged unless the operation independently proves a specific correction.
4. **Mixed Lazio/Roma identity:** explicitly move the 21 proved Roma rows from the shared ID to Roma 100 before retiring that ID into the Lazio root. Account for its 11 Lazio rows separately; no whole-ID merge before the split. Full participant beforeimages and pinned source-line/provider proof are required.
5. **Four club clusters plus Roma aliases:** use the investigated existing-root proposals, flat redirects and explicit alias corrections; retain all original team rows. Preserve the 36-row CL snapshot, including Roma's established row. Do not average ratings or add standings records. Update the Lazio and Braga display names with full team beforeimages; do not create duplicate roots merely to get a clean name.
6. **Three older-row exceptions:** permit only the explicitly reviewed match IDs for Roma/Manchester United opponent/date/round fixes and Roma/Feyenoord per-leg 1–1. Pin those exact source lines and beforeimages; do not globally widen the legacy date scope or repair the entire Manchester City cluster.

Every write is an enumerated team, match, alias or redirect operation. Global retired-ID reference rewiring is permitted only after the enumerated mixed-ID exceptions are accounted for and must be derived from the declared flat redirects. Beforeimage every affected row. Disallow row deletions, arbitrary SQL, unrelated metadata writes and unreviewed event insertions in this profile.

Keep identity **mutations** separate from evidence-only participant **bindings**. The selected roots are not a declaration that every opponent in 338 qualifiers has already been globally canonicalized. Include Manchester United solely as a proven target for the two older opponent corrections; a legacy field allowlist cannot waive target-identity proof. Per-match/per-side bindings may prove an unchanged existing participant from its exact source-team beforeimage, pinned provider record and explicit raw-name/country alias, without creating a persistent provider-root mapping or merging that participant's other legacy records. Changed participant slots must resolve to a separately proven existing/created root. Root name equivalence is an explicit reviewed variant set backed by provider and country-bearing source evidence, not string equality with one display name or fuzzy similarity.

An identity-only older-row correction may preserve a known wrong date/round only when the unchanged discrepancy is explicitly recorded and a unique pinned oriented fixture, competition/file season and stored-score representation establish identity. It cannot authorize date/round/score edits outside the three enumerated field exceptions. Group-stage headings are valid identity context, not a reason to reject a proven participant split or rewrite the group as a knockout round.

Qualification-year correction uses explicit qualifying source context/pinned ESPN UTC timestamps. Do not change the August main-stage season convention and do not naively rerun the parser's existing July inference.

## 4. Candidate phases and independent verification

1. Validate pinned input identity, profile/review hash, proof roles, event uniqueness, all target beforeimages, allowable fields, root membership, new-ID collisions, and operation overlap. Consolidate overlapping match changes into one explicit afterimage; conflicting assignments refuse.
2. Exclusively create a disposable candidate, apply existing schema migration with recorded CL attribution, then apply corrections in one shared-lock transaction. Split mixed IDs before root rewiring, create roots before dependent references, keep FKs enabled. If DuckDB refuses a referenced-parent metadata update, use a validated shadow rebuild rather than disabling constraints.
3. Rebuild derived Solkoff and pooled European Elo on the isolated candidate after the data transaction (calculators commit internally). Preserve qualifying results in Elo, exclude them from league/pair views. Clear stale retired derived rows; keep source unchanged.
4. Independently reconstruct expected source-plus-operations on another throwaway copy, compare logical fingerprints, check zero match deletions/insertions and the unchanged match-ID set, no dangling/retired references, protected CL standings, full 338-row event concordance and source hash. Repeat construction/replay deterministically, excluding declared timestamps/cache fields only.
5. Report before/after team/match/mapping counts, changed calendar dates/scores/participants/names, Elo and Solkoff deltas, archived table shape, retained qualification counts, pending full-takeover guard state and missing main-stage population. Do not call a cleared fake historical table a completed league table while its main-stage results remain absent.

Expected conservation checks for this snapshot: 3,245 match rows and the exact same IDs retained; 509 source team rows plus one proved Maltese identity; the existing CL standings' data values preserved after schema attribution. Derived values can change globally through Elo's connected graph, so compare input lineage and repeatable replay rather than requiring identical ratings.

## 5. File-level implementation

- New `backend/espn_correction_rehearsal.py`: strict profile loading/validation, role-aware evidence checks, correction-only exclusive candidate builder, verification and impact report. Keep this isolated from runtime/API and the full-takeover CLI. A small evidence helper module is allowed if it prevents duplicating the existing parser logic.
- New `tests/backend/test_espn_correction_rehearsal.py`: synthetic real-DuckDB tests for success, immutable source, qualifier/main role separation, season/club/country/score/date proof, missing/forged review, stale/partial beforeimages, mixed-ID split ordering, unrelated-row conservation, root rename and FK preservation, allocation collision, no deletion/insertion escape, source/output/sidecar path protection, failed-phase receipt and tamper detection.
- Private run builder/manifest under the existing protected reconciliation artifact root: derive operations from the investigated cases, pin hashes and serialize every before/after row. No guessed unresolved identities, no production approval stamp.
- Update `docs/espn-reconciliation.md` with the distinct correction-profile boundary and actual results; record a sanitized run report outside the repo for Discord.

Use `uv run pytest` for focused tests and the existing ESPN/redirect/qualifier regressions, then the full suite. Current known baseline after previous repairs is 14 failures (six live-server prerequisites, eight stale Elo/detail/Solkoff fixtures); compare actual names. No hidden skips/xfails.

## 6. Completion versus rollout

This unit succeeds when the correction-only copy is reproducible, reviewed and accurately reported—not when the site is ready to deploy. Stop any unproved/conflicting operation without changing the source. Keep missing 342 main-stage insertions, the unapproved 151-club full-takeover draft, current-season feed freshness, ESPN terms risk and a fresh production backup as explicit later gates.

## 7. Execution checkpoint — October 24, 2026

The reviewed implementation is `espn-correction-rehearsal/5`, profile/manifest version 3, scope `copy-only-corrections`. Independent manifest review was accepted before the scoped copy-only review receipt was stamped. The separate generic full-takeover tool remains revision 5 with manifest version 2; its acceptance guards were not weakened.

The final private execution report records successful preview, disposable candidate construction and independent tool verification: **401/401 checks passed**. A second fresh candidate matched all **nine logical table hashes**, excluding declared timestamps. Source SHA-256 remained `d6012cfbcb2037cc6436b0a56f920b42dcde1e2134e9a9e4461dce2ed155190f`; pinned evidence remained unchanged and no HTTP attempts occurred. The run used 20 pinned capture entries covering 936 main-stage and 338 qualifying events.

Both candidates retain all **3,245 match IDs**. There are 363 directly corrected rows, 98 additional rewiring-only rows and 2,784 untouched rows. All 338 qualifiers remain; 15 score changes and five wrong-club slots were observed. The 32 mixed rows split into 21 Roma and 11 Lazio rows. All 509 source teams remain, with one Maltese Hibernians creation bringing the total to 510. The run applies 13 redirects, 23 alias corrections and two display renames; the 36 CL standings rows retain identical values. Manchester United 66 is the tenth proof root after the nine prior roots, not authorization for an additional global source-club merge. Sixteen older metadata discrepancies remain declared identity-only limitations rather than broad date cleanup.

Derived cleanup is not match deletion: Solkoff rows change from 509 stored to 36 live CL rows; Elo changes from 509 stored to 492 active rows, with a same-algorithm source baseline of 503. Archived EL/UCL 2024 tables remain noncanonical at 44/43 rows (previously 46/43). The former 2025 qualifier-derived league tables become truthfully empty (52/174 to zero), because 342 missing main-stage events were deliberately not inserted. The 151-club full-takeover draft remains unapproved.

The implementation handoff reports 66 focused, 56 existing regression and 50 generic reconciler tests passing. Fresh final full-suite verification reports **611 passed, 14 failed**, matching the remaining baseline failure names. The separate post-candidate audit passed **121/121 independent checks** and **338/338 qualifying-proof revalidations**, compared every source/candidate match field independently of the apply function, and reproduced all 492 active Elo ratings within stored REAL precision. It confirmed unchanged live CL Solkoff values and ten-table repeat equality including cache. No skips/xfails were added; documentation verification uses source/report inspection only.

The machine result is `state: rehearsed`, `rehearsal_only: true`, `production_ready: false`, `full_takeover: false`. No source/production mutation, runtime/API change, deployment, commit or push occurred. The October 15 snapshot remains historical; a fresh snapshot, exact-scope review, complete takeover decisions, isolated smoke/rollback and terms review are later gates. Detailed artifacts and generated manifests remain private; the sanitized human report is intended for separate attachment. See the [public runbook and evidence summary](../../espn-correction-rehearsal.md).
