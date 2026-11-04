"""Season switcher: default selection, per-season request scoping, stale
caches/expansions, slow-response races, empty/error states and accessibility.

Only user-visible behaviour is asserted against the production frontend served
by the shared conftest fixtures.
"""
from __future__ import annotations

from playwright.sync_api import expect


SEASON_2024_ROW = {
    "teamId": 999,
    "teamName": "Season 2024 FC",
    "teamCode": "S24",
    "teamCrest": None,
    "position": 1,
    "played": 3,
    "won": 3,
    "drawn": 0,
    "lost": 0,
    "gf": 9,
    "ga": 1,
    "gd": 8,
    "points": 9,
    "solkoffCoefficient": 2.1,
    "strengthScore": 6.3,
}


def _seasons_for(api, path):
    return [season for _method, p, season in api.season_log if p == path]


def _wait_for_season(api, wait_until, path, season):
    wait_until(lambda: season in _seasons_for(api, path))


def test_defaults_to_current_season(open_app, page, api, wait_until):
    open_app()

    select = page.locator("#seasonSelect")
    expect(select).to_be_visible()
    wait_until(lambda: select.locator("option").count() == 3)

    assert select.input_value() == "2026"
    assert select.locator("option[value='2026']").inner_text() == "2026/27"
    assert select.locator("option[value='2024']").inner_text() == "2024/25"


def test_default_requests_are_scoped_to_current_season(open_app, page, api, wait_until):
    open_app()

    _wait_for_season(api, wait_until, "/api/standings", "2026")
    _wait_for_season(api, wait_until, "/api/tournament/current-stage", "2026")
    _wait_for_season(api, wait_until, "/api/knockout-pairs/ROUND_OF_16", "2026")


def test_switching_scopes_every_season_dependent_request(open_app, page, api, wait_until):
    open_app()
    _wait_for_season(api, wait_until, "/api/standings", "2026")

    page.select_option("#seasonSelect", "2024")

    assert page.locator("#seasonSelect").input_value() == "2024"
    _wait_for_season(api, wait_until, "/api/standings", "2024")
    _wait_for_season(api, wait_until, "/api/tournament/current-stage", "2024")
    _wait_for_season(api, wait_until, "/api/knockout-pairs/ROUND_OF_16", "2024")


def test_switch_shows_selected_season_standings(open_app, page, api, wait_until):
    api.set_standings_for_season(2024, [SEASON_2024_ROW])
    open_app("league")
    expect(page.locator("#standingsBody")).to_contain_text("Gamma Pinnacle")

    page.select_option("#seasonSelect", "2024")

    expect(page.locator("#standingsBody")).to_contain_text("Season 2024 FC")
    expect(page.locator("#standingsBody")).not_to_contain_text("Gamma Pinnacle")


def test_switch_clears_expanded_details_and_cache(open_app, page, api, wait_until):
    open_app("league")
    expect(page.locator("#standingsTable")).to_be_visible()

    page.locator('#standingsBody tr[data-team-id="1"]').click()
    expect(page.locator("#detail-1")).to_be_visible()
    _wait_for_season(api, wait_until, "/api/teams/1/solkoff-details", "2026")
    first_count = api.counts["GET /api/teams/1/solkoff-details"]

    page.select_option("#seasonSelect", "2024")
    _wait_for_season(api, wait_until, "/api/standings", "2024")
    expect(page.locator("#detail-1")).to_have_count(0)

    # Back to the current season: the cleared cache forces a fresh details fetch.
    page.select_option("#seasonSelect", "2026")
    _wait_for_season(api, wait_until, "/api/standings", "2026")
    page.locator('#standingsBody tr[data-team-id="1"]').click()
    wait_until(lambda: api.counts["GET /api/teams/1/solkoff-details"] > first_count)


def test_slow_standings_response_never_shows_old_season(open_app, page, api, wait_until):
    open_app("league")
    expect(page.locator("#standingsBody")).to_contain_text("Gamma Pinnacle")

    api.set_standings_for_season(2024, [SEASON_2024_ROW])
    api.hold_path("/api/standings")
    page.select_option("#seasonSelect", "2024")
    wait_until(lambda: api.held_count == 1)

    # The in-flight request has not returned: the old season's table must be gone.
    expect(page.locator("#standingsBody")).not_to_contain_text("Gamma Pinnacle")

    api.release_held()
    expect(page.locator("#standingsBody")).to_contain_text("Season 2024 FC")


def test_empty_season_shows_empty_state_not_prior_data(open_app, page, api, wait_until):
    open_app("league")
    expect(page.locator("#standingsBody")).to_contain_text("Gamma Pinnacle")

    api.set_standings_for_season(2024, [])
    page.select_option("#seasonSelect", "2024")
    _wait_for_season(api, wait_until, "/api/standings", "2024")

    expect(page.locator("#standingsBody")).to_contain_text("No standings data available")
    expect(page.locator("#standingsBody")).not_to_contain_text("Gamma Pinnacle")


def test_error_season_does_not_show_prior_data(open_app, page, api, wait_until):
    open_app("league")
    expect(page.locator("#standingsBody")).to_contain_text("Gamma Pinnacle")

    api.set_standings_status_for_season(2024, 500)
    page.select_option("#seasonSelect", "2024")
    _wait_for_season(api, wait_until, "/api/standings", "2024")

    expect(page.locator("#error")).to_be_visible()
    expect(page.locator("#standingsBody")).not_to_contain_text("Gamma Pinnacle")


def test_reload_retains_selection(open_app, page, api, wait_until):
    open_app()
    _wait_for_season(api, wait_until, "/api/standings", "2026")

    page.select_option("#seasonSelect", "2024")
    _wait_for_season(api, wait_until, "/api/standings", "2024")
    before = len(_seasons_for(api, "/api/standings"))

    page.locator("#refreshBtn").click()
    wait_until(lambda: len(_seasons_for(api, "/api/standings")) > before)

    assert page.locator("#seasonSelect").input_value() == "2024"
    assert _seasons_for(api, "/api/standings")[-1] == "2024"


def test_historical_pair_date_is_shown(open_app, page, api, build_pair, wait_until):
    historical = build_pair(1, (1, "Alpha", None), (2, "Bravo", None),
                            date="2025-03-04T20:00:00Z")
    api.set_pairs("ROUND_OF_16", [historical])
    api.set_pairs_for_season(2024, "ROUND_OF_16", [historical])
    open_app("round-of-16")
    expect(page.locator("#round-of-16-pairs .playoff-pair-card")).to_have_count(1)

    page.select_option("#seasonSelect", "2024")
    _wait_for_season(api, wait_until, "/api/knockout-pairs/ROUND_OF_16", "2024")

    # The archived tie's real date survives instead of being blanked as too old.
    expect(page.locator("#round-of-16-pairs .pair-date").first).to_contain_text("2025")


def test_historical_analysis_probability_explained_as_unavailable(
        open_app, page, api, build_pair, build_analysis, wait_until):
    pair = build_pair(1, (1, "Alpha", None), (2, "Bravo", None),
                      date="2025-03-04T20:00:00Z")
    api.set_pairs("ROUND_OF_16", [pair])
    api.set_pairs_for_season(2024, "ROUND_OF_16", [pair])
    api.set_analysis(1, 2, build_analysis((1, "Alpha", None), (2, "Bravo", None),
                                          method="historical_no_data"))
    open_app("round-of-16")
    page.select_option("#seasonSelect", "2024")
    _wait_for_season(api, wait_until, "/api/knockout-pairs/ROUND_OF_16", "2024")

    page.locator("#round-of-16-pairs .playoff-pair-card").first.click()
    panel = page.locator("#analysisPanel")
    expect(panel).to_be_visible()
    expect(panel).to_contain_text("unavailable")
    # The explanation must say *why* (archive), not present a fake calculation.
    expect(panel).to_contain_text("archived seasons")


def test_season_control_is_labeled_keyboard_and_mobile_friendly(open_app, page, api, wait_until):
    open_app()

    select = page.locator("#seasonSelect")
    expect(select).to_be_visible()
    wait_until(lambda: select.locator("option").count() == 3)

    assert select.get_attribute("aria-label") == "Select season"
    expect(page.locator("label[for='seasonSelect']")).to_contain_text("Season")

    select.focus()
    assert page.evaluate("document.activeElement && document.activeElement.id") == "seasonSelect"

    page.set_viewport_size({"width": 390, "height": 800})
    expect(select).to_be_visible()
