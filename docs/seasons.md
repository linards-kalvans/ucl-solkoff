# Season Switching

The app can switch every view between stored Champions League seasons.

## Scope and supported seasons

- Only **new-format** (league-phase / "Swiss") seasons are selectable: **2024/25 onward**.
  Older-format seasons and other competitions (Europa League, Conference League) are
  never listed or counted.
- A season is identified by its **start year** (`2024` means `2024/25`). The season
  window is **August 1 inclusive through the following August 1 exclusive** (UTC).

## API

`GET /api/seasons` returns the selectable seasons, newest first:

```json
{
  "currentSeason": 2025,
  "seasons": [
    {"year": 2025, "label": "2025/26"},
    {"year": 2024, "label": "2024/25"}
  ]
}
```

The current season is always listed, even when it has no stored matches yet.

These read endpoints accept an optional `season` query parameter (season start year):

- `GET /api/standings?season=2024`
- `GET /api/teams/{team_id}/solkoff-details?season=2024`
- `GET /api/tournament/current-stage?season=2024`
- `GET /api/playoff-pairs?season=2024`
- `GET /api/knockout-pairs/{stage}?season=2024`
- `GET /api/playoff-pairs/{team1}/{team2}/analysis?season=2024`

Behavior:

- **Omitting `season` preserves the existing contract**: the current season, read from
  the live stored `standings` table.
- An **unsupported year returns HTTP 400** (supported range is 2024/25 through the
  current season).
- A **valid season with no stored data returns an empty state** for that season; it
  never falls back to another season.

## How historical data is reconstructed

Selecting a season is **read-only**: no database writes, no data collection, no
re-import. Archived-season standings and Solkoff values are rebuilt on demand from
stored `FINISHED` matches in `backend/season_service.py`:

- 3/1/0 points, ordered by **points → goal difference → goals for**.
- **Solkoff** = mean unique-opponent PPG; **Strength** = `(Points % × Solkoff) / 100`.
- The live `standings` table is never modified.

### Legacy stored data

Matches imported **before** the text parser recognized the new-format headers
(`backend/football_txt_parser.py`) were stored with **NULL** `stage`/`round`/`group_name`/
`matchday`. To stay read-only and avoid a re-import, the reconstruction classifies them
safely:

- Explicit `LEAGUE_STAGE` rows are always league phase.
- A **NULL-tagged** row is treated as league phase only when `stage`, `round`,
  `group_name` and `matchday` are **all NULL** and its date is **before the February 1
  play-off boundary**.
- A row in the same NULL shape with a date **at or after February 1** is the knockout
  play-off round.
- Anything else untagged is left **unclassified** rather than guessed onto a wrong tab.

### Archive repair

Archived 2024/25 and 2025/26 fixtures are rebuilt offline from checksum-pinned
openfootball `cl.txt` sources by `backend/historical_repair.py`. The tool is
**dry-run by default** (`--apply` to write), performs no network access while
applying, and replaces only CL fixtures inside the two approved Aug 1–Aug 1
windows in a **single transaction**. It never modifies `standings`,
`elo_ratings`, `solkoff_coefficients` or `api_cache`; it rewrites only the
canonical source-name keys in `team_mappings` to their chosen clean club ids and
leaves every other mapping row unchanged. It is idempotent when re-run, and
resolves club identity by exact source name only: polluted (kickoff-prefixed)
team rows are left intact and a clean node is created when no clean identity
exists. It is not exposed through the API.

## Archived-season limitations

- **Win probability is unavailable for archived seasons.** Elo and live-strength inputs
  are built from present-day data and would leak later results into an archived
  prediction, so none is emitted; the UI shows *unavailable* with an explanation.
- No current-season draw lookup is performed for archived seasons.

## Frontend behavior

- A labelled native **Season** select lives in the header controls (keyboard- and
  mobile-friendly). It is populated from `GET /api/seasons`, with the **current season
  selected by default**.
- The selected year is appended to every season-dependent request.
- **Switching seasons** clears prior standings, expanded team details, knockout pair
  caches and any open pair analysis, then reloads all views. Request/generation guards
  ensure a late response for a previous season can never paint over the newly selected
  season, including loading and error states.
- **Reload data** re-reads stored data for the *currently selected* season. It is a
  GET-only reload and is **not** the same as a browser refresh (F5): a full page load
  starts again at the current season. Persisting the chosen season across a full page
  load is intentionally not implemented.
- Archived-season knockout cards show their real match dates; the live "too old" date
  heuristic does not apply to archives.
