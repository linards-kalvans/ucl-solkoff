"""Competition selector: metadata, request scoping, cache clearing and empty
states. Uses the production frontend served by the shared conftest fixtures.
"""
from __future__ import annotations

from playwright.sync_api import expect


def _competitions_for(api, path):
    return [competition for _method, p, competition in api.competition_log if p == path]


def _wait_for_competition(api, wait_until, path, competition):
    wait_until(lambda: competition in _competitions_for(api, path))


EL_ROW = {
    "teamId": 777,
    "teamName": "Europa United",
    "teamCode": "ELU",
    "teamCrest": None,
    "position": 1,
    "played": 3,
    "won": 2,
    "drawn": 0,
    "lost": 1,
    "gf": 6,
    "ga": 3,
    "gd": 3,
    "points": 6,
    "solkoffCoefficient": 1.4,
    "strengthScore": 4.2,
}


def test_competition_selector_defaults_to_configured(open_app, page, api, wait_until):
    open_app()

    select = page.locator("#competitionSelect")
    expect(select).to_be_visible()
    wait_until(lambda: select.locator("option").count() == 3)

    assert select.input_value() == "CL"
    assert select.locator("option[value='EL']").inner_text() == "Europa League"
    assert select.locator("option[value='UCL']").inner_text() == "Conference League"


def test_default_requests_carry_competition(open_app, page, api, wait_until):
    open_app()

    _wait_for_competition(api, wait_until, "/api/seasons", "CL")
    _wait_for_competition(api, wait_until, "/api/standings", "CL")
    _wait_for_competition(api, wait_until, "/api/knockout-pairs/ROUND_OF_16", "CL")


def test_switching_competition_shows_that_competition(open_app, page, api, wait_until):
    api.set_standings_for_competition("EL", [EL_ROW])
    open_app("league")
    expect(page.locator("#standingsBody")).to_contain_text("Gamma Pinnacle")

    page.select_option("#competitionSelect", "EL")

    expect(page.locator("#standingsBody")).to_contain_text("Europa United")
    expect(page.locator("#standingsBody")).not_to_contain_text("Gamma Pinnacle")
    _wait_for_competition(api, wait_until, "/api/standings", "EL")
    _wait_for_competition(api, wait_until, "/api/seasons", "EL")


def test_empty_competition_shows_empty_state_not_default(open_app, page, api, wait_until):
    api.set_standings_for_competition("UCL", [])
    open_app("league")
    expect(page.locator("#standingsBody")).to_contain_text("Gamma Pinnacle")

    page.select_option("#competitionSelect", "UCL")
    _wait_for_competition(api, wait_until, "/api/standings", "UCL")

    expect(page.locator("#standingsBody")).to_contain_text("No standings data available")
    expect(page.locator("#standingsBody")).not_to_contain_text("Gamma Pinnacle")


def test_slow_competition_switch_never_shows_old_rows(open_app, page, api, wait_until):
    api.set_standings_for_competition("EL", [EL_ROW])
    open_app("league")
    expect(page.locator("#standingsBody")).to_contain_text("Gamma Pinnacle")

    api.hold_path("/api/standings")
    page.select_option("#competitionSelect", "EL")
    wait_until(lambda: api.held_count == 1)

    # The in-flight competition's table has not returned: the old rows are gone.
    expect(page.locator("#standingsBody")).not_to_contain_text("Gamma Pinnacle")

    api.release_held()
    expect(page.locator("#standingsBody")).to_contain_text("Europa United")


def test_competition_control_is_labeled_and_mobile_friendly(open_app, page, api, wait_until):
    open_app()

    select = page.locator("#competitionSelect")
    expect(select).to_be_visible()
    assert select.get_attribute("aria-label") == "Select competition"
    expect(page.locator("label[for='competitionSelect']")).to_contain_text("Competition")

    select.focus()
    assert page.evaluate("document.activeElement && document.activeElement.id") == "competitionSelect"

    page.set_viewport_size({"width": 375, "height": 800})
    expect(select).to_be_visible()
