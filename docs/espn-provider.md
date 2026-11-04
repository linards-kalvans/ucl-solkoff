# ESPN provider

## Routing and credentials

| Application code | Competition | Current-season provider | ESPN slug |
| --- | --- | --- | --- |
| `CL` | Champions League | football-data.org | Not applicable |
| `EL` | Europa League | ESPN | `uefa.europa` |
| `UCL` | Conference League | ESPN | `uefa.europa.conf` |

`UCL` means Conference League in the application's API, not Champions League.
Routing is explicit; a provider failure never switches competitions or silently
falls back to another provider. ESPN requires no API key. A missing
`EXTERNAL_API_KEY` disables CL network refresh, not EL/UCL ESPN refresh or GET
views of stored results. Football-data.org fallback requests on older archive
paths also require that credential.

EL/UCL use ESPN for current and archived seasons starting in 2024 (2024/25).
CL and older EL/UCL archives retain the existing verified openfootball GitHub
source path and bounded football-data.org fallback. Conference seasons before
its 2021 inception are skipped. The selectable season UI still starts at 2024/25.

Startup attempts all three competitions independently. A failed refresh is logged
without stopping the other competitions. Successful refreshes recalculate their
Solkoff coefficients; pooled European Elo is replayed once after the loop.
See [competition contracts](competitions.md) and [season behavior](seasons.md).

## Requests, cache and load

For a season start year `Y`, the client requests:

```text
https://site.api.espn.com/apis/v2/sports/soccer/{slug}/standings?season=Y
https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard?dates=Y&limit=1000
https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard?dates=Y+1&limit=1000
```

`Y+1` denotes the following calendar year, not a literal query expression.
Single-calendar-year pulls are necessary: the checked endpoints rejected date
ranges with HTTP 400 and ignored numeric pagination. Events are filtered by
**`event.season.year`**, not the scoreboard's advertised current season. The
scoreboard's top-level league metadata must include the requested slug, and
standings must identify the requested competition and season. Events are
deduplicated by provider event ID; conflicting fixture identities are rejected,
while compatible repeated snapshots use the later score/status snapshot.

Each uncached competition-season normally needs three GETs, without per-event
request fanout. The client spaces requests by at least 0.5 seconds and defaults to
a 30-second timeout and at most three attempts per URL. Network errors, HTTP 429
and HTTP 5xx receive bounded exponential backoff (default base: one second).
Other HTTP errors and malformed responses fail rather than retrying indefinitely.

Persistent cache keys use the `espn:` namespace and full URL. The ESPN client
defaults to a 3600-second TTL. Its timing, retry and TTL settings are Python
constructor parameters; `API_MIN_REQUEST_INTERVAL` and `API_CACHE_TTL` configure
the football-data.org path, not ESPN client defaults. HTTP failures and malformed
JSON are not cached as successful payloads. If parsing or dataset consistency
validation rejects a season, that season's three ESPN cache keys are evicted so
the invalid payload is not served for the rest of its TTL.

## Validation and persistence

An import requires a complete 36-club standings table and internally consistent
data. Every club's played, wins, draws, losses, goals for, goals against, goal
difference and points must agree with the finished league fixtures. League pairs
must be unique, clubs must resolve, and fixtures must fall within the August 1
through next August 1 exclusive season window. Unknown non-qualifying stages,
invalid completed scores, foreign-league payloads and conflicting identities fail
visibly. Known qualifying stages are excluded. Unresolved future placeholders
are skipped with diagnostics, never assigned fabricated clubs or 0–0 results.

Completed seasons require all fixtures to be finished: 144 league plus 45 knockout
fixtures for EL (189 total), or 108 plus 45 for Conference (153 total). Current
seasons may have incomplete future schedules, but finished fixtures must fully
reconcile with standings and fixture counts cannot exceed those bounds. The
importer does not invent missing matches to satisfy counts.

External fetching and dataset validation precede the ingestion transaction.
One `Database.transaction()` holds the shared connection's reentrant lock across
BEGIN through COMMIT or ROLLBACK. Provider mappings, new canonical teams,
fixtures and the selected competition's current standings are persisted atomically;
another thread's cache commit cannot commit a partial import. Identity or database
write failures roll back the ingestion. HTTP work stays outside the transaction.
Cache writes are separate from this atomic domain-data import.

Only current-season ingestion replaces the selected competition's live standings.
Archive imports write fixtures and identities but **zero live standings rows**;
archive GETs reconstruct tables read-only. Other competitions' snapshots are not
replaced. Solkoff recalculation and pooled Elo replay are scheduler operations,
not part of the ESPN ingestion transaction.

## Identity reconciliation

`provider_team_mappings` persists `(provider, external_id) -> team_id`;
`provider_match_mappings` persists `(provider, external_id) -> match_id`. ESPN's
numeric IDs are not used directly as canonical IDs. Existing mappings take
precedence and survive repeated imports. Where legacy-takeover guards permit
creation, new clubs receive collision-checked,
bounded negative canonical IDs; new fixtures use deterministic provider-namespaced
negative candidate IDs with collision checks.

First-time club resolution requires an exact conservatively cleaned name or an
explicitly supplied, reviewed alias to a canonical name. Cleaning casefolds names
and collapses punctuation/spacing; it does not remove club suffixes such as
`United` or `City`. There are **no fuzzy merges**. Ambiguous canonical names fail
for review. Existing canonical names and populated metadata are preserved; missing
code/crest metadata may be filled safely.

**Production reconciliation was deployed on 2026-10-26**, with outcome
`production_verified_with_limitations`; see the
[release record](releases/2026-10-26-european-competitions-release.md) for the actual
final-source evidence, post-cutover review-timing deviation and rollback requirements.
The default alias map remains empty. The earlier blocking audit used a private
offhost production backup copy, without opening or writing the production database.
It found 509 canonical teams and 3,245 matches, including 251 kickoff-prefixed
team names, 183 of which had clean-name twins. Many ESPN short names also differed
from existing names. An unguarded simulation created 50 additional current-season
club identities and duplicated 188 EL and 151 Conference fixtures from 2024/25.
These identity splits would also split historical results and pooled Elo.

The service now fails closed with `ESPNIdentityError`:

- If the competition has unmanaged legacy fixtures in the 2024/25+ window, a club
  must have a prior provider mapping or resolve to a unique exact/reviewed-alias
  canonical name; an unmatched club cannot create a new canonical record.
- If an incoming fixture has no provider mapping and cannot be uniquely adopted,
  creation is blocked while **any** unmanaged row remains in the selected
  competition-season, even if its canonical pair differs. Polluted legacy club
  identities make pair-only duplicate detection unsafe.

“Unmanaged” means a fixture lacks a mapping for this provider. Pre-2024 archives
do not trigger the new-format team guard. Clean or fully mapped competitions can
still add genuinely new clubs and fixtures. Guard failures roll back all ingestion
writes in the shared-lock transaction; there is no bypass environment variable or
flag. The guards prevent unsafe takeover, not reconcile the existing database.

Aliases currently exist only through the Python constructor
`ESPNDataService(db, client=None, provider="espn", aliases=None)`. The `aliases`
dictionary maps an incoming name to a reviewed canonical name. The application
dispatcher constructs the service without aliases. A Python caller can supply a
reviewed map by injecting the service; there is no UI, environment-variable or
public HTTP alias configuration. Existing provider mappings are not automatically
rewritten when aliases change.

On first fixture import, a unique existing row is adopted only when competition,
season, canonical home/away clubs, stage and round agree. Adoption applies current
score, status and date immediately. Ambiguity or an already-claimed fixture fails
rather than overwriting unrelated history. Mapped fixtures are rechecked against
stored identity before updates; reschedules inside the same season are allowed.
Production reconciliation requires the [reviewed reconciliation workflow](espn-reconciliation.md).
The [October 26 main-stage copy](espn-main-stage-copy.md) has accepted real reviewed
canonical decisions and a complete reconciled candidate for COPY scope only,
including independent data, read-only API and Chromium acceptance. Its 151 roots
are reviewed and the previously missing 342 archived events are inserted. This
did not itself reconcile production or authorize rollout; the later production
rollout is recorded separately above. Review club lineage, fixture
ownership and Elo continuity for each exact source; exact matching or an alias
alone is not proof of correctness.

Reviewed retired identities use `team_redirects` and a shared resolver across
ESPN, legacy name matching, football-data ingestion and authorized offline repair.
Missing legacy redirect tables are read-only identity passthrough; corrupt or
unsupported redirect sources fail loudly. Explicitly evidenced `QUALIFYING` rows
are excluded from takeover guards, main-stage pairs/current-stage detection and
knockout diagnostics, while their finished results remain in the Elo corpus.
NULL/unknown stages are not exempted from the guards.

## API and Python entry points

Public response schemas and competition/season parameters are unchanged; provider
metadata is not added to `GET /api/competitions`. Existing administrative
`POST /api/refresh` refreshes configured scheduler competitions, not a selected
UI season. `POST /api/refresh-historical?years_back=10&delay=3.0` retains its
historical-window and existing-presence heuristics while routing EL/UCL 2024+
seasons to ESPN. It is not a forced replacement or repair endpoint. Neither route
adds an alias parameter. Refresh completion messages do not certify that every
competition imported successfully; inspect per-competition errors.

For direct Python callers:

- `ESPNClient.fetch_season(competition_id, season_year)` fetches and parses a
  season into standings, matches and diagnostics without writing domain tables.
- `ESPNDataService.sync_all(competition_id)` imports the current season.
- `ESPNDataService.sync_season(competition_id, season_year)` validates and imports
  one season, returning competition/year, team and fixture counts, created/reused
  teams, new/adopted/updated matches, standings writes, stage counts and diagnostics.
- `DataService.sync_all(competition_id="CL")` dispatches to the explicit provider;
  its wrapper does not return the ESPN ingestion counters.

## Design decision: isolated provider ingestion

### Context

EL/UCL need a different source while clubs and Elo remain shared with CL and
historical imports. Provider IDs and names cannot safely serve as canonical
identity, and concurrent users share a database connection.

### Decision

Keep CL on football-data.org and isolate ESPN fetching, strict season validation
and conservative identity resolution behind the existing dispatcher. Persist
provider mappings and domain data in one shared-lock transaction after fetching.
Retain existing API contracts and archive sources outside the new-format ESPN scope.
Fail closed on unresolved legacy takeover rather than create parallel identities
or fixtures; reconciliation is a separate reviewed operation, without a bypass.

### Consequences

Provider failures remain isolated and repeated imports preserve mapped identity.
Strict checks may reject temporarily inconsistent feeds rather than replace valid
stored data. The backup-copy audit established the need for separate reviewed
production reconciliation; guards preserve data but do not repair it. Unofficial endpoints also
require ongoing compatibility and terms review.

## Reported verification evidence

The following results were reported for the integration, not rerun for this
documentation-only change:

| Temporary-database check | EL | Conference (`UCL`) |
| --- | --- | --- |
| Current season start year 2026: clubs | 36 | 36 |
| Current fixtures | 144 | 108 |
| Current finished fixtures | 36 | 18 |
| Archived season start year 2024: total fixtures | 189 | 153 |
| Archive live standings writes | 0 | 0 |

Current per-team GF, GA, points and Solkoff were checked. Repeated cached imports
made zero GETs and created no new rows. The current live smoke used **exactly six
new GETs** across both competitions before hardening; the earlier archive smoke used **six GETs**
separately. These were temporary-database checks, not production reconciliation.

After hardening, replay of the four current/archive public-body datasets passed
offline with **zero GETs**. Raw-cache TTL expiry initially blocked replay; a
separate recorded-body harness corrected that verification setup. This is not a
new live-source check. A fresh four-dataset replay against a backup-derived copy
raised as intended: teams remained **509 -> 509**, matches **3,245 -> 3,245**, and
provider mappings **0 -> 0**. No production database was opened or written.

Focused ESPN verification reports **81 tests**, including six legacy-takeover
guard tests beyond the earlier 75 (72 plus 3 concurrency tests). Final combined
targeted backend, full frontend and deployment-contract verification passed
**363 tests**. That earlier ESPN-integration full backend run reported **296 passed and 18 original
failures**. The whole suite is not green; deployment-test success is not evidence
of deployment. Documentation was checked against source by inspection; live
requests were not repeated for that update. Subsequent offline reconciliation
tooling verification reports **545 passed and 14 remaining baseline failures**
across the full suite; four stale data-service mock failures were repaired.
See [tooling evidence and outstanding review](espn-reconciliation.md#6-verified-progress-and-remaining-gates).

## Limitations and production gates

- ESPN endpoints are unofficial and undocumented. Availability and payload shape
  can change without notice. Public accessibility does not establish unrestricted
  licensing or reuse rights; production terms review remains an unresolved risk.
- Points deductions are unsupported: standings must satisfy strict 3/1/0 scoring
  (`points = 3 * wins + draws`). Sanction-adjusted tables fail validation.
- No aggregate/shootout schema expansion is included. Per-leg scores feed the
  existing analyzer's aggregate calculation; its existing penalty-shootout
  limitation remains. A tied aggregate does not establish a shootout winner.
- Future replacements require a fresh-source reviewed plan, a restorable backup
  and a rollback runbook. Resolve canonical club ownership, reviewed
  aliases and legacy fixtures for the fresh snapshot, including ambiguous duplicate
  clubs such as Lazio. Earlier historical-copy acceptance alone does not
  establish freshness or production security. Verify Elo continuity, not just
  counts; preserve schema/data and the previous image for rollback. Disabling
  guards is not a rollout procedure.
- Review the outstanding backend failures and terms risk before future production
  decisions. The release record documents deployment, not licensing approval.

No production replacement, database mutation, deployment, commit or other production
action is part of this documentation update.
