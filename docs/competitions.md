# European Competitions

The competition selector scopes Overview, League, knockout pairs, Solkoff details
and pair analysis to one European competition and season.

| Provider code | Selector label | League-phase games per club |
| --- | --- | --- |
| `CL` | Champions League | 8 |
| `EL` | Europa League | 8 |
| `UCL` | Conference League | 6 |

**`UCL` is football-data.org's Conference League code, not Champions League.**
Game counts describe the new league-phase format; they do not imply that all
fixtures are stored, and they are never used to fabricate results.
The application retains these codes while explicitly routing CL to football-data.org,
EL to ESPN `uefa.europa`, and UCL to ESPN `uefa.europa.conf`. Provider selection is
not a failure-triggered fallback. See [ESPN provider documentation](espn-provider.md)
for ingestion safeguards, identity risks and rollout gates.

## API reference

### `GET /api/competitions`

Returns an object with `default` (the validated configured code) and
`competitions` (an ordered array). Each entry contains `code` and `label` strings,
`leagueMatches` integer metadata, and `hasCurrentData` boolean availability.
For example, an empty database with the default configuration returns:

```json
{
  "default": "CL",
  "competitions": [
    {"code": "CL", "label": "Champions League", "leagueMatches": 8, "hasCurrentData": false},
    {"code": "EL", "label": "Europa League", "leagueMatches": 8, "hasCurrentData": false},
    {"code": "UCL", "label": "Conference League", "leagueMatches": 6, "hasCurrentData": false}
  ]
}
```

`hasCurrentData` means at least one live `standings` row exists for that
competition. It does not certify freshness, completeness, historical availability
or provider entitlement. Empty competitions remain in the selector. An
uninitialized database returns HTTP 503; an internal query failure returns 500.

### Request selection and empty data

The following existing GET routes accept optional `competition`:

| Route | Also accepts optional `season` start year |
| --- | --- |
| `/api/seasons` | No; discovers seasons for the selected competition |
| `/api/standings` | Yes |
| `/api/teams/{team_id}/solkoff-details` | Yes |
| `/api/tournament/current-stage` | Yes |
| `/api/playoff-pairs` | Yes |
| `/api/knockout-pairs/{stage}` | Yes |
| `/api/playoff-pairs/{team1_id}/{team2_id}/analysis` | Yes |

Codes are trimmed and case-insensitive. An omitted or blank value resolves to
the validated `COMPETITION_ID`. An explicit unsupported code returns HTTP 400
with a `detail` message identifying the supported codes, rather than falling back.
Existing route response schemas are unchanged. Health, administrative POSTs and
the debug endpoint do not gain this selection parameter.

`season` defaults to the current season and uses its start year (`2024` means
2024/25). Supported years run from 2024 through the current season; unsupported
years return HTTP 400. See [season switching](seasons.md) for season windows and
archived-result behavior.

A supported selection with no stored data is not an invalid competition.
Standings and pair lists return `[]`, and season discovery still lists the
current season. Other routes retain their existing shapes: stage detection can
report `LEAGUE` without fixtures, an existing team's Solkoff details can contain
zero values and no opponents, and an unknown team returns 404 from the details
route. Pair analysis retains its analysis response rather than becoming an empty
list. No route substitutes another competition or season to fill missing data.

Examples against a running local application:

```bash
curl 'http://localhost:8000/api/competitions'
curl 'http://localhost:8000/api/seasons?competition=el'
curl 'http://localhost:8000/api/standings?competition=UCL&season=2024'
curl -i 'http://localhost:8000/api/standings?competition=UNKNOWN'
```

## Selector behavior

The frontend loads competition metadata before discovering seasons. Initial
selection uses the API's configured default and the current season. Changing
competition clears standings, details, knockout caches and open pair analysis;
it reloads seasons for the new competition and selects its current season.
Request-generation guards reject late responses for previous selections,
including competition metadata and season discovery. Reload data performs GETs
for the selected competition and season; it does not trigger ingestion. A full
page load restores the configured default competition and current season.

## Refresh configuration

`COMPETITION_ID` defines the default request/UI competition. Values are normalized
case-insensitively; missing, blank or unsupported values resolve to `CL`.

Application startup explicitly passes all three supported codes to the scheduler.
Initial and periodic refreshes therefore attempt CL, EL and UCL, independently
of `COMPETITION_ID` or `COMPETITIONS`. A failure for one competition is logged
without stopping the others or erasing its stored data. Elo is recalculated once
after the refresh loop. Missing `EXTERNAL_API_KEY` disables CL network refresh,
not scheduler initialization or EL/UCL ESPN refresh. No ESPN key is required.
GET views of stored data remain available without a football-data.org client.

For direct Python callers, the constructor is
`DataScheduler(db, competition_id="CL", competitions=None)`. An explicit
`competitions` list takes precedence. When it is omitted, the scheduler reads
the comma-separated `COMPETITIONS` environment variable; absent or blank input
falls back to `[competition_id]`. Environment-list codes are normalized,
duplicates removed in first-seen order, and unsupported entries raise
`ValueError`. **This environment variable does not control the application's
startup list**, because startup supplies an explicit list.

## Storage and migration

Back up the existing database before rollout, and confirm that `COMPETITION_ID`
matches the competition represented by its legacy live snapshots before the
first initialization with this version.

Initialization migrates legacy `standings` and `solkoff_coefficients` to primary
keys of `(competition_id, team_id)`. Legacy snapshots without competition keys
are attributed to the validated configured default. The snapshot rebuild is
atomic, rolls back on failure and is a no-op once the schema is current. Values
and team foreign keys are preserved, with legacy integer Solkoff values converted
to REAL. The schema's `DEFAULT 'CL'` for inserts omitting `competition_id` is a
legacy compatibility default, not a dynamic reflection of `COMPETITION_ID`.

Matches with NULL competition provenance remain unknown: migration does not
guess that they belong to CL or to the configured default. They are excluded
from competition-filtered views and pooled Elo until provenance is established.

### Design decision: live snapshots and shared Elo

#### Context

Multiple competitions share clubs in one database, while archived standings must
not read present-day results or require a second historical standings source.

#### Decision

Standings and Solkoff coefficients remain **live-only**, competition-keyed
snapshots, without a season key. Historical new-format seasons (2024/25 onward)
are reconstructed read-only from competition- and season-filtered stored matches.
Elo intentionally remains one team rating pooled across CL, EL and UCL results.

#### Consequences

A club can have distinct live standings and coefficients per competition without
cross-contamination. Historical selection does not modify live snapshots, and
archive completeness depends on stored results. Pooled Elo can inform current
predictions across European competitions, but it is not a competition-specific
league ranking; archived win probabilities remain unavailable to avoid future-data
leakage. Pre-2024 matches may contribute to Elo without appearing in the season UI.

## Historical sources and provider access

EL/UCL current and archived seasons from **2024/25 onward** use ESPN. Completed
archives require 36 clubs, 144 EL or 108 Conference league fixtures, and 189 or
153 total main-competition fixtures respectively. Archive imports never replace
live standings; archived views reconstruct tables from stored results.

CL and pre-2024 EL/UCL archives retain the existing source path. The GitHub path
accepts only the requested competition's recognized filename
and a confirming competition header: CL uses `cl.txt` or `champions-league.txt`,
EL uses `el.txt` or `europa-league.txt`, and UCL uses `conf.txt` or
`conference-league.txt`. Missing or mismatched headers are rejected; another
competition's file is never a fallback.

If a verified usable GitHub source is unavailable on that retained path, ingestion
can request that same competition and season through the existing rate-limited
football-data.org client when configured. This fallback
is bounded to the configured historical sync window, not an unbounded search.
Missing or denied data is logged and stays unavailable; neither CL substitution
nor fabricated results fills the gap. The separate offline archive-repair tool
remains authorized only for its documented CL seasons.

Football-data.org coverage and entitlement still constrain requests on the retained
source path. Conference seasons before its 2021 inception are skipped. ESPN access
requires no key, but its endpoints are unofficial and public accessibility is not
unrestricted licensing; production terms review remains an open risk.

Persistent provider mappings preserve canonical team and fixture IDs across imports.
Initial club matching uses exact conservatively cleaned names or explicitly supplied,
reviewed aliases, never fuzzy merges. **The default alias map is empty.** Aliases
currently require Python injection, not UI or environment configuration.
**Production rollout is blocked:** the backup-copy audit confirmed conflicting
club identities and duplicate-fixture risk, not merely hypothetical spelling
differences. Imports now fail closed on unresolved legacy identities/fixtures,
with no bypass flag. Reconciliation requires a separate reviewed plan and a
restorable backup before service replacement; it is outside this implementation.
See [identity reconciliation](espn-provider.md#identity-reconciliation).

## Verification evidence

Reported ESPN verification now covers **81 focused tests**, including six new
legacy-takeover guard tests. Final combined targeted backend, full frontend and
deployment-contract checks passed **363 tests**. The final full backend run reported
**296 passed and 18 original failures**. The whole suite is not
green. Temporary-database live checks covered current 2026 and archived 2024
EL/UCL data. Post-hardening recorded-body replay passed offline with zero GETs;
see [reported evidence](espn-provider.md#reported-verification-evidence)
for counts and limitations. Deployment tests do not mean a deployment occurred.
This documentation-only update uses source consistency inspection, without
rerunning tests or taking production actions.
