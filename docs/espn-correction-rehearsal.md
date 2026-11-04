# Copy-only ESPN correction rehearsal

## Status and authorization

The October 24, 2026 run produced a disposable correction-only candidate from the **October 15 audit snapshot**, not from a fresh production snapshot. Its successful machine state is `rehearsed`, with `rehearsal_only: true`, `production_ready: false` and `full_takeover: false`. Neither the source nor production was changed. No deployment, commit or push was performed.

The user selected “Prepare copy-only correction rehearsal.” That authorization covered reviewed corrections on disposable copies, not production access or full takeover. At this October 24 checkpoint, the **151-club full-takeover draft was unapproved**, and **342 missing main-stage events remained a separate insertion phase**. This run does not establish current production freshness or complete application readiness.

**October 26 status:** the separately authorized [main-stage completion on a new copy](espn-main-stage-copy.md) is now independently accepted, including all 151 reviewed roots, insertion of those 342 events and read-only API/Chromium checks. The counts and limitations below describe the historical correction-only artifact, not the later full candidate. Its generic reconciler is now revision 6; the revision-5 reference below records the original architecture checkpoint.

## Architecture decision: isolate the correction profile

### Context

The [full-takeover reconciler](espn-reconciliation.md) requires complete identity and event accounting across six datasets. A partial correction run cannot satisfy those gates. Weakening them would turn an investigation workflow into an accidental production acceptance path.

### Decision

Use the separate offline module `backend.espn_correction_rehearsal`, revision `espn-correction-rehearsal/5`, with manifest/profile version **3** and profile/scope `copy-only-corrections`. Reuse existing safety, hashing and migration helpers without changing the full-takeover validator. The generic tool remains `backend.espn_reconcile`, revision `espn-reconcile/5`, manifest version **2**.

Allow only enumerated corrections, team creation/renames, flat redirects, alias changes and declared reference rewiring. Preserve every match row and ID. Do not expose match insertion, deletion, arbitrary SQL, in-place apply, guard bypass or a runtime/API integration. Rebuild derived data only on isolated copies, after the correction transaction because the calculators commit internally.

### Consequences

The correction candidate can be independently reconstructed and audited without approving unresolved full-takeover decisions. Its deliberately limited receipt cannot substitute for full reconciliation, production authorization, licensing review or deployment gates. Separate manifests, review receipts and commands must remain distinct; they are not interchangeable approval formats.

## Evidence and operation contract

- **Main-stage proof:** use pinned main-stage ESPN bodies for the corresponding event corrections. Captured main events are not authorization to insert missing events.
- **Qualifying proof:** use the distinct qualifying league slugs, event season, UTC day, round and per-leg result. Qualifier bodies must not pass through the main-stage parser or its 36-club standings validator.
- **Historical raw proof:** reparse hash-pinned source text, date headers and section context using the explicitly declared file season. Do not apply generic July inference or change the runtime August main-stage season convention.
- **Participant bindings:** per-match/per-side evidence can establish an unchanged participant without globally merging its club records or creating a persistent provider-root mapping. A changed participant must resolve to a separately proven root. Names, country, explicit variants and exact beforeimages supply proof; fuzzy similarity does not.
- **Root scope:** retain the nine previously investigated roots and add Manchester United (`66`) as the tenth evidence root solely to prove the older opponent corrections. This does not authorize an extra source-club merge or a global Manchester City repair.
- **Closed writes:** require exact 12-column match beforeimages/afterimages, enumerated writable fields, reviewed root membership, collision checks and declared rewiring counts. Apply the mixed identity split before redirect rewiring. Preserve all original team rows as provenance.

The executed profile contains 13 flat redirects, 23 alias corrections, two display renames (Lazio and Braga), and one new Maltese Hibernians identity, distinct from Scottish Hibernian. It splits 32 mixed Lazio/Roma rows into 21 Roma and 11 Lazio rows. Five wrong qualifying participant slots are corrected individually, not by merging distinct clubs.

The derived audit scope is pinned to season start year **2026**, with bounds `2026-08-01` to `2027-08-01`; it does not drift with execution time. Sixteen declared older metadata discrepancies remain retained identity-only evidence limitations, not a date/round cleanup mandate. Only the three explicitly allowlisted older-row exceptions authorize their specified nonidentity fields.

## Review receipt

For a manifest named `correction-plan.json`, the sibling receipt is `correction-plan.rehearsal-review.json`, **not** the generic `.review.json` receipt. All of these fields are required:

| Field | Contract |
| --- | --- |
| `version` | `3` |
| `scope`, `profile` | Both `copy-only-corrections` |
| `manifest_sha256` | Canonical manifest digest from this module's `manifest_sha256(manifest)` |
| `authorization` | Nonempty record of the user's copy-only authorization |
| `technical_review` | Object containing nonempty `reviewer`, `reviewed_at` and `notes` strings |

Unknown receipt fields, mismatched scope/version, missing review or a changed manifest digest are refused. Any manifest change requires renewed review. Independent manifest review was accepted before the executed copy-only receipt was stamped.

The operator-recorded receipt binds the reviewed contents and scope. It is **not a cryptographic signature**, proof that a human review occurred, production approval or a terms/licensing attestation. Recalculating its digest is not review. There is no review-stamping CLI command.

## CLI reference

Run from the repository root. Variables below represent private local paths; do not use a live database or a production bind path.

```bash
uv run python -m backend.espn_correction_rehearsal preview \
  --source "$SOURCE_COPY" --manifest "$CORRECTION_MANIFEST"

uv run python -m backend.espn_correction_rehearsal rehearse \
  --source "$SOURCE_COPY" --manifest "$CORRECTION_MANIFEST" \
  --output-db "$NEW_CANDIDATE"

uv run python -m backend.espn_correction_rehearsal verify \
  --candidate "$NEW_CANDIDATE" --manifest "$CORRECTION_MANIFEST"
```

`preview` validates the source, pinned evidence, operations and review without creating a candidate. Its result has `state: previewed` and `would_build: true`; this is not rehearsal success.

`rehearse` exclusively creates a new candidate, applies migrations and reviewed corrections on that copy, rebuilds derived data, and invokes independent verification. The candidate and its `.rehearsal-beforeimages.jsonl` and `.rehearsal-receipt.json` sidecars must not already exist. Preserve rejected/partial artifacts for diagnosis; they are not overwrite targets or publishable candidates.

`verify` reads the candidate and pinned source, reconstructs the expected result on temporary disposable copies, and compares logical fingerprints and explicit conservation/evidence checks rather than trusting a receipt alone. Keep the source, manifest, review and proof bundle available. Verification does not modify the candidate; temporary reconstruction is not a source write.

All commands keep the source read-only. Safety checks refuse production paths, aliases, symlinks/hard links, active source sidecars, stale source/proof hashes, uncertain legacy attribution, changed beforeimages and unsafe output paths. Output belongs in a protected private directory. Recorded evidence has no HTTP fallback. Pending takeover counts are explicitly `declared_not_verified`, not proof that takeover is complete.

Commands print JSON on success. Exit **0** means successful preview or a `rehearsed` result, **1** means a returned unsuccessful rehearsal/verification result, and **2** means a refused precondition (`REFUSED: ...` on stderr). Inspect both the result state and exit status.

### Python entry points

These are offline helpers, not runtime service interfaces. They are exported by `backend.espn_correction_rehearsal`:

| Signature | Result |
| --- | --- |
| `inspect_source(source_db: str) -> Dict[str, Any]` | Read-only source inventory for private plan preparation. |
| `preview(source_db: str, manifest_path: str) -> Dict[str, Any]` | Validated profile summary; no candidate creation. |
| `rehearse(source_db: str, manifest_path: str, output_db: str) -> Dict[str, Any]` | New disposable candidate and scoped rehearsal receipt. |
| `verify_candidate(candidate_db: str, manifest_path: str) -> Dict[str, Any]` | Independent reconstruction and candidate checks. |
| `manifest_sha256(manifest: Dict[str, Any]) -> str` | SHA-256 of canonical JSON, not the raw file-byte digest. |
| `review_receipt_path(manifest_path: os.PathLike) -> Path` | Correction-specific sibling review path. |
| `main(argv: Optional[Sequence[str]] = None) -> int` | CLI dispatch and exit status. |

## Recorded execution checkpoint: October 24, 2026

The private final execution report, human report, analytics report and observation report record the following results. Detailed manifests, database bytes, beforeimages and local paths remain private; only this sanitized summary belongs in the repository. The sanitized human report is intended for separate attachment, not as a committed private bundle.

| Observation | Recorded result |
| --- | --- |
| Source SHA-256 | `d6012cfbcb2037cc6436b0a56f920b42dcde1e2134e9a9e4461dce2ed155190f`, unchanged |
| Evidence | 20 pinned capture entries; 936 main-stage and 338 qualifying events; no HTTP attempts |
| Tool candidate verification | 401/401 checks passed |
| Repeat construction | All nine logical table hashes identical after declared timestamp exclusions; database byte hashes need not match |
| Match conservation | 3,245 rows and exactly the same match IDs before and after; no insertions or deletions |
| Changed match rows | 363 direct corrections plus 98 rewiring-only rows = 461 distinct changed rows; 2,784 untouched |
| Qualifying retention | All 338 retained and classified as `QUALIFYING` |
| Score/participant changes | 15 score changes and five wrong-club qualifying slots observed in the candidate |
| Mixed identity | 32 rows partitioned into 21 Roma and 11 Lazio rows |
| Team conservation | All 509 original rows retained; 510 after the single Maltese identity creation |
| Protected CL standings | All 36 stored rows retain identical data values after schema attribution |

The 15 score changes comprise 11 qualifying corrections, three main-stage shootout/per-leg corrections and one older Roma per-leg correction. The two older Manchester United opponent/date corrections were observed in the candidate. A separate post-candidate audit passed **121/121 independent checks** and revalidated **338/338 qualifying proofs**. That audit recomputed expected rows from the source and declared operations rather than trusting the tool's apply function or receipt, and also compared ten candidate/repeat tables, including the unchanged cache table.

### Analytics interpretation

- **Solkoff:** 509 stored coefficient rows become 36 live CL rows in the pinned audit scope. All 36 live values match manual opponent-PPG calculations and are unchanged from the source's live values. The other 473 stored rows were stale derived data, not deleted matches.
- **Elo:** 509 stored rows become 492 active rows. A source replay with the same current algorithm yields 503, so stored-before versus after is not a like-for-like correction delta. Removal of 13 retired redirect entries and five stale entries without retained matches, plus the new Maltese identity, explains the stored-row change. No match is deleted by this derived cleanup.
- **Archived 2024 tables:** EL changes from 46 to 44 rows; UCL remains 43. Neither is yet a canonical 36-club table.
- **Archived 2025 tables:** EL changes from 52 to zero rows and UCL from 174 to zero. The former tables incorrectly included qualifiers. Once classified correctly, those qualifiers no longer fabricate league tables; the 342 missing main-stage events were not inserted. Empty is truthful here, not a completed season-switching feature.

Derived Elo/Solkoff changes are replays, not arbitrary manifest edits. Corrections can propagate through the connected Elo graph and affect predictions. These results are rehearsal impact, not a production forecast or a claim of unchanged application output.

### Verification limits and remaining gates

The implementation handoff reports **66 focused tests passed**, **56 existing regression tests passed** and **50 generic reconciler tests passed**. Fresh final full-suite verification reports **611 passed, 14 failed**. The failure names match the remaining documented baseline: six live-server prerequisites, three Elo fixtures, one details fixture and four Solkoff fixtures. No tests were skipped or marked xfail to hide those failures. Documentation edits were verified by inspection, not by an additional test run.

The independent correction-candidate audit is complete; it does not validate future takeover or deployment changes. The later main-stage copy has completed identity/event reconciliation and application acceptance for its own exact source. Before production activation, obtain a fresh consistent snapshot, review and reconcile that exact source, perform isolated application checks, resolve provider terms/security requirements, and preserve restorable schema/data plus the previous image. The historical audit snapshot does not establish production freshness. Do not bypass guards or treat `rehearsed` as deployment authorization.
