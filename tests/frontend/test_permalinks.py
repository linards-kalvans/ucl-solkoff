"""Shared dataset URLs restore selectors, requests, and browser history."""
import re
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect


def selection(page):
    return parse_qs(urlparse(page.url).query)


def test_shared_link_scopes_first_requests(open_app, page, api, wait_until):
    open_app("league", path="/?competition=UCL&season=2025")
    expect(page.locator("#competitionSelect")).to_have_value("UCL")
    expect(page.locator("#seasonSelect")).to_have_value("2025")
    expect(page.locator("#league")).to_be_visible()
    wait_until(lambda: api.counts["GET /api/standings"] > 0)
    assert all(code == "UCL" for _, path, code in api.competition_log
               if path != "/api/competitions")
    assert all(year == "2025" for _, path, year in api.season_log
               if path not in ("/api/competitions", "/api/seasons"))


def test_switch_keeps_season_toolbar_stage_and_unrelated_params(open_app, page):
    open_app("league", path="/?competition=CL&season=2025&source=shared")
    expect(page.locator("#seasonSelect")).to_have_value("2025")
    page.locator("#teamSearch").fill("Gamma")
    page.select_option("#leagueSort", "points")
    page.locator("#advancedToggle").uncheck()
    page.select_option("#competitionSelect", "UCL")
    expect(page.locator("#seasonSelect")).to_have_value("2025")
    expect(page.locator("#teamSearch")).to_have_value("Gamma")
    expect(page.locator("#leagueSort")).to_have_value("points")
    expect(page.locator("#advancedToggle")).not_to_be_checked()
    expect(page).to_have_url(re.compile(r"competition=UCL.*#league$"))
    assert selection(page) == {"competition": ["UCL"], "season": ["2025"], "source": ["shared"]}
    page.reload()
    expect(page.locator("#competitionSelect")).to_have_value("UCL")
    expect(page.locator("#seasonSelect")).to_have_value("2025")


def test_back_forward_restores_dataset_and_view(open_app, page):
    open_app("league", path="/?competition=CL&season=2025")
    expect(page.locator("#seasonSelect")).to_have_value("2025")
    page.select_option("#competitionSelect", "EL")
    expect(page).to_have_url(re.compile("competition=EL"))
    page.select_option("#seasonSelect", "2024")
    page.go_back()
    expect(page.locator("#seasonSelect")).to_have_value("2025")
    page.go_back()
    expect(page.locator("#competitionSelect")).to_have_value("CL")
    expect(page.locator("#seasonSelect")).to_have_value("2025")
    page.go_forward()
    expect(page.locator("#competitionSelect")).to_have_value("EL")
    expect(page.locator("#seasonSelect")).to_have_value("2025")


def test_unavailable_season_falls_back_on_competition_switch(open_app, page, api):
    api.set_seasons_for_competition("EL", {
        "currentSeason": 2026, "seasons": [{"year": 2026, "label": "2026/27"}],
    })
    open_app("quarter-final", path="/?competition=CL&season=2025")
    expect(page.locator("#seasonSelect")).to_have_value("2025")
    page.select_option("#competitionSelect", "EL")
    expect(page.locator("#seasonSelect")).to_have_value("2026")
    expect(page).to_have_url(re.compile(r"season=2026#quarter-final$"))


def test_invalid_link_uses_valid_defaults(open_app, page):
    open_app("league", path="/?competition=unknown&season=invalid")
    expect(page.locator("#competitionSelect")).to_have_value("CL")
    expect(page.locator("#seasonSelect")).to_have_value("2026")
    expect(page).to_have_url(re.compile(r"competition=CL&season=2026#league$"))
