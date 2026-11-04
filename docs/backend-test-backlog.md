# Backend test repair backlog

## Baseline and scope

The redesign verification run reported 162 passed and 18 failed tests. The same 18 backend failures existed before frontend work (baseline: 57 backend tests passed, 18 failed). All 105 frontend tests passed. Backend implementation and tests were not modified by the redesign.

These items were recorded for investigation, not waived. Section 2 was subsequently repaired during reconciliation tooling implementation; the other 14 baseline failures remain open. Mock mismatches are observed failure causes, not proof that production code is correct. Review intended contracts before changing tests.

## 1. Live-server integration prerequisites — 6 failures

File: `tests/backend/test_backend_data_integration.py`

- `test_standings_endpoint_returns_data`
- `test_standings_endpoint_data_quality`
- `test_standings_endpoint_has_all_teams`
- `test_health_endpoint`
- `test_current_stage_endpoint`
- `test_knockout_pairs_endpoints`

Observed: `httpx.ConnectError: [Errno 111] Connection refused`. Tests call a running backend through `API_BASE_URL` (default localhost:8000), rather than an in-process test app.

Future work: define explicit integration-test setup and populated-data prerequisites; decide whether to keep these as separately selected live smoke tests or replace them with hermetic integration fixtures. Do not silently skip failures in the default suite without documenting the test contract.

Acceptance: integration tests pass against their documented target; missing prerequisites fail clearly without being mistaken for application regressions.

## 2. Data-service mock call counts — resolved (4 former failures)

File: `tests/backend/test_data_service.py`

- `test_sync_teams_from_standings`: expects one commit, observes two.
- `test_sync_teams_from_matches`: expects one commit, observes two.
- `test_sync_matches`: expects one execute, observes mapping-table initialization plus match insert.
- `test_sync_standings`: expects one execute, observes mapping-table initialization plus standings insert.

Future work: distinguish service initialization effects from synchronization effects. Assert correct writes/transaction behavior rather than obsolete global call counts, after verifying intended persistence semantics.

Acceptance: correct rows and transaction behavior are tested without hiding unexpected writes or duplicate synchronization.

Resolved during offline reconciliation tooling work: the six tests in this file now use real temporary DuckDB databases and mock only the external client. Two additional identity-edge tests verify retired provider IDs preserve their root/retired-row metadata. All eight pass; assertions check stored teams, scores, stages, competition keys, snapshot values and knockout sync rather than obsolete global query counts. Redirect resolution remains fail-loud; no mock-only production bypass was restored.

## 3. Elo fixture coverage — 3 failures

File: `tests/backend/test_elo_calculator.py`

- `test_get_win_probability_equal_ratings`
- `test_get_win_probability_higher_rated_favored`
- `test_get_win_probability_includes_elo_details`

Observed: `TypeError: 'Mock' object is not iterable` in `get_team_stats`. Existing mocks do not supply iterable rows for newly consulted team statistics.

Future work: verify the current team-stat and probability contracts, provide representative rows, and retain assertions for equal ratings, favored team and Elo details.

Acceptance: these cases run through current statistics queries and verify intended probability semantics, including insufficient data where relevant.

## 4. Solkoff detail endpoint fixture — 1 failure

File: `tests/backend/test_main.py`

- `test_solkoff_details_endpoint`

Observed: HTTP 500 instead of 200, caused by `IndexError` reading `row[7]` from a shorter mocked match tuple.

Future work: align mock rows with the endpoint SELECT column order. Review the test's legacy `totalOpponentPoints` assertion against the current average-opponent-PPG response contract; do not restore an obsolete sum-based metric merely to satisfy the test.

Acceptance: representative opponent matches return the documented current detail schema and validate PPG calculations.

## 5. Solkoff calculator fixture shape — 4 failures

File: `tests/backend/test_solkoff_calculator.py`

- `test_calculate_solkoff_with_opponents`
- `test_calculate_solkoff_missing_standings`
- `test_calculate_all`
- `test_calculate_all_stores_values`

Observed: `IndexError` reading the second tuple field. Calculator needs opponent points and games played, while older fixtures provide shorter rows.

Future work: update fixtures and expectations for average opponent PPG, including missing standings and zero games. Verify stored coefficients rather than legacy sums.

Acceptance: all four cases pass with current PPG semantics and test correct values persisted for each team.

## Verification after future repairs

Run `uv run pytest tests/backend -q` with documented live-integration prerequisites or agreed hermetic replacements, then `uv run pytest`. Record exact pass/failure/skip counts; no unrelated frontend regressions and no hidden xfails/skips.
