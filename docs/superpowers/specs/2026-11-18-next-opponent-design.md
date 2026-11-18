# Next opponent in League standings

Show what comes next without changing how completed performance is measured. In the live League table, show the first confirmed upcoming fixture in the selected competition’s league phase, with opponent name, Home/Away, and the opponent’s current league-phase points per game and games played. Put it below the team name rather than adding a sortable column. Render `Next: Club X · Away · 2.00 PPG from 5 matches`. If PPG is unavailable, show the fixture without it. With no eligible fixture, show nothing. Archived tables and Knockout stay unchanged.

## Approaches

- Selected: show opponent PPG and sample size. Uses familiar league data and makes small samples visible; PPG describes results, not underlying ability or win odds.
- Deferred: venue-adjusted Elo. Better matches an ability-based rating in principle, but coverage and calibration need validation.
- Rejected: fold future fixtures into Solkoff/Strength. Unplayed games should not affect completed-results metrics or rankings.

Use stored schedule and standings only. No schema changes, extra browser requests, probability model, inferred opponents, schedule aggregation, or ranking changes.
