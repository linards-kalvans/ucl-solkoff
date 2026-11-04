"""Public "Reload data" behaviour.

The user-facing action re-reads already-stored data over GET endpoints; it must
never trigger a server-side data collection (no POST anywhere). These tests pin
that public contract plus the surrounding guarantees: single-flight + disabled
button, local-cache invalidation with generation-guarded reloads, preservation
of route/search/sort/toggle state, visible partial/full failures in every view,
and a timestamp written only by a successful standings fetch.
"""
from __future__ import annotations

import re

from playwright.sync_api import expect

ALPHA = (5, "Alpha Strength FC", None)
GAMMA = (1, "Gamma Pinnacle", None)

_FAILURE = re.compile(r"error|failed|try again|could not|unavailable", re.I)
_RELOAD_LABEL = re.compile(r"reload", re.I)

# The accessible description may live on the button itself or be referenced via
# aria-describedby; collect all of it before asserting the explanation.
_BUTTON_DESCRIPTION_JS = """
() => {
    const btn = document.getElementById('refreshBtn');
    if (!btn) return '';
    const parts = [
        btn.textContent || '',
        btn.getAttribute('aria-label') || '',
        btn.getAttribute('title') || '',
    ];
    const ids = (btn.getAttribute('aria-describedby') || '').split(/\\s+/);
    for (const id of ids) {
        if (!id) continue;
        const el = document.getElementById(id);
        if (el) parts.push(el.textContent || '');
    }
    return parts.join(' ').trim();
}
"""


def _open_league_ready(open_app, page):
    open_app("league")
    expect(page.locator("#league")).to_be_visible()
    expect(page.locator("#standingsTable")).to_be_visible()
    expect(page.locator("#standingsBody tr[data-team-id]").first).to_be_visible()


def _posts(api):
    return [entry for entry in api.log if entry[0] == "POST"]


# --------------------------------------------------------------------------- #
# Public contract: GET-only reload, never a POST, button label/explanation
# --------------------------------------------------------------------------- #
def test_reload_issues_no_post_requests_any_path(open_app, page, api, wait_until):
    _open_league_ready(open_app, page)
    standings_before = api.counts["GET /api/standings"]

    page.locator("#refreshBtn").click()
    # The reload must actually re-read data (otherwise "no POST" is vacuous).
    wait_until(lambda: api.counts["GET /api/standings"] > standings_before)
    expect(page.locator("#refreshBtn")).to_be_enabled()

    posts = _posts(api)
    assert not posts, f"public Reload must be GET-only; saw POST requests: {posts}"
    assert not any(path == "/api/refresh" for _, path in api.log), (
        "Reload must not call the server-side refresh/collection endpoint"
    )


def test_reload_button_labelled_reload_data(open_app, page):
    open_app("")
    expect(page.locator("#overview")).to_be_visible()
    button = page.locator("#refreshBtn")
    expect(button).to_be_visible()

    label = button.inner_text().strip() or button.get_attribute("aria-label") or ""
    assert _RELOAD_LABEL.search(label), f"button must read 'Reload data': {label!r}"


def test_reload_button_explains_stored_data_not_external_collection(open_app, page):
    open_app("")
    expect(page.locator("#refreshBtn")).to_be_visible()

    description = page.evaluate(_BUTTON_DESCRIPTION_JS)
    assert _RELOAD_LABEL.search(description), description
    assert re.search(r"stored|cached|saved|already loaded|latest data", description, re.I), (
        f"description must say it reloads stored data: {description!r}"
    )
    assert re.search(r"\bnot\b|never|without|no new|does not|doesn't", description, re.I), (
        f"description must say it does NOT trigger external collection: {description!r}"
    )


# --------------------------------------------------------------------------- #
# Single-flight / disabled button
# --------------------------------------------------------------------------- #
def test_reload_singleflight_disables_button_until_done(open_app, page, api, wait_until):
    _open_league_ready(open_app, page)
    api.hold_path("/api/standings")
    standings_before = api.counts["GET /api/standings"]

    button = page.locator("#refreshBtn")
    button.click()
    wait_until(lambda: api.held_count >= 1)
    expect(button).to_be_disabled()

    # A second activation while disabled must not start another cycle.
    try:
        button.click(timeout=500)
    except Exception:
        pass
    assert api.counts["GET /api/standings"] == standings_before + 1

    api.release_held()
    expect(button).to_be_enabled()


# --------------------------------------------------------------------------- #
# GET reload of standings + selected stage pairs
# --------------------------------------------------------------------------- #
def test_reload_refetches_standings_and_selected_stage_via_get(
        open_app, page, api, build_pair, wait_until):
    api.set_pairs("ROUND_OF_16", [build_pair(1, ALPHA, GAMMA)])
    open_app("knockout")
    expect(page.locator("#knockout")).to_be_visible()
    wait_until(lambda: api.counts["GET /api/standings"] >= 1)
    wait_until(lambda: api.counts["GET /api/knockout-pairs/ROUND_OF_16"] >= 1)

    standings_before = api.counts["GET /api/standings"]
    pairs_before = api.counts["GET /api/knockout-pairs/ROUND_OF_16"]

    page.locator("#refreshBtn").click()
    wait_until(lambda: api.counts["GET /api/standings"] > standings_before)
    expect(page.locator("#refreshBtn")).to_be_enabled()

    assert api.counts["GET /api/knockout-pairs/ROUND_OF_16"] > pairs_before
    assert not _posts(api), "no POST may occur from a browser reload"


def test_reload_fetches_shared_pair_stage_once_for_overview_and_knockout(
        open_app, page, api, build_pair, wait_until):
    api.set_pairs("ROUND_OF_16", [build_pair(1, ALPHA, GAMMA)])
    open_app("knockout")
    expect(page.locator("#knockout")).to_be_visible()
    wait_until(lambda: api.counts["GET /api/knockout-pairs/ROUND_OF_16"] >= 1)
    before = api.counts["GET /api/knockout-pairs/ROUND_OF_16"]

    page.locator("#refreshBtn").click()
    wait_until(lambda: api.counts["GET /api/knockout-pairs/ROUND_OF_16"] > before)
    expect(page.locator("#refreshBtn")).to_be_enabled()

    # Overview preview and the selected Knockout stage share one cache entry.
    assert api.counts["GET /api/knockout-pairs/ROUND_OF_16"] == before + 1


# --------------------------------------------------------------------------- #
# Cache invalidation and superseded in-flight requests
# --------------------------------------------------------------------------- #
def test_reload_invalidates_analysis_cache(open_app, page, api, build_analysis,
                                            build_pair, wait_until):
    api.set_pairs("ROUND_OF_16", [build_pair(1, ALPHA, GAMMA)])
    analysis = build_analysis(ALPHA, GAMMA)
    api.set_analysis(5, 1, analysis)
    api.set_analysis(1, 5, analysis)
    open_app("knockout")
    page.locator("#knockout .match-card, #knockout .playoff-pair-card").first.click()
    expect(page.locator("#knockout")).to_contain_text("Alpha Strength FC")
    assert api.counts["GET /api/playoff-pairs/5/1/analysis"] == 1

    standings_before = api.counts["GET /api/standings"]
    page.locator("#refreshBtn").click()
    wait_until(lambda: api.counts["GET /api/standings"] > standings_before)
    expect(page.locator("#refreshBtn")).to_be_enabled()

    page.locator("#knockout .match-card, #knockout .playoff-pair-card").first.click()
    expect(page.locator("#knockout")).to_contain_text("Alpha Strength FC")
    assert api.counts["GET /api/playoff-pairs/5/1/analysis"] == 2


def test_reload_invalidates_solkoff_detail_cache(open_app, page, api,
                                                 build_solkoff_details, wait_until):
    _open_league_ready(open_app, page)
    api.set_solkoff(1, build_solkoff_details(1, "Gamma Pinnacle", "PIN", None))

    row = page.locator("#standingsBody tr[data-team-id='1']").first
    row.click()
    expect(page.locator("#standingsBody")).to_contain_text("Opponent United")
    assert api.counts["GET /api/teams/1/solkoff-details"] == 1

    standings_before = api.counts["GET /api/standings"]
    page.locator("#refreshBtn").click()
    wait_until(lambda: api.counts["GET /api/standings"] > standings_before)
    expect(page.locator("#refreshBtn")).to_be_enabled()

    # Cache cleared: the still-expanded row refetches its details.
    wait_until(lambda: api.counts["GET /api/teams/1/solkoff-details"] >= 2)


def test_reload_supersedes_in_flight_standings_request(open_app, page, api,
                                                       standings, wait_until):
    old = [dict(row) for row in standings]
    old[0]["teamName"] = "Old Reload FC"
    new = [dict(row) for row in standings]
    new[0]["teamName"] = "New Reload FC"
    api.standings_sequence = [(200, old), (200, new)]

    # Hold the initial standings response so it is still in flight during reload.
    api.hold_path("/api/standings")
    open_app("league")
    expect(page.locator("#league")).to_be_visible()
    wait_until(lambda: api.held_count >= 1)
    expect(page.locator("#standingsBody")).to_be_visible()

    page.locator("#refreshBtn").click()
    expect(page.locator("#standingsBody")).to_contain_text("New Reload FC")

    # The superseded request resolving late must never overwrite the fresh data.
    api.release_held()
    page.wait_for_timeout(150)
    expect(page.locator("#standingsBody")).to_contain_text("New Reload FC")
    expect(page.locator("#standingsBody")).not_to_contain_text("Old Reload FC")


# --------------------------------------------------------------------------- #
# State preservation
# --------------------------------------------------------------------------- #
def test_reload_preserves_route_search_sort_and_toggle(open_app, page, api, wait_until):
    _open_league_ready(open_app, page)
    search = page.locator("#teamSearch")
    search.fill("delta")
    sort = page.locator("#leagueSort")
    sort.select_option("points")
    toggle = page.locator("#advancedToggle")
    toggle.uncheck()

    standings_before = api.counts["GET /api/standings"]
    page.locator("#refreshBtn").click()
    wait_until(lambda: api.counts["GET /api/standings"] > standings_before)
    expect(page.locator("#refreshBtn")).to_be_enabled()

    expect(page.locator("#league")).to_be_visible()
    assert search.input_value() == "delta"
    assert sort.input_value() == "points"
    assert not toggle.is_checked()


# --------------------------------------------------------------------------- #
# Failures are inline, visible and the button is restored
# --------------------------------------------------------------------------- #
def test_reload_failure_is_inline_and_restores_button(open_app, page, api,
                                                      standings, wait_until):
    _open_league_ready(open_app, page)
    dialogs = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))

    # Public Reload re-reads data over GET; fail that read.
    api.set_standings(standings, status=500)
    page.locator("#refreshBtn").click()
    expect(page.locator("#refreshBtn")).to_be_enabled()
    assert not dialogs, f"reload failure must not use alert(): {dialogs}"
    expect(page.locator("#league")).to_contain_text(_FAILURE)


def test_partial_reload_failure_is_visible(open_app, page, api, standings):
    _open_league_ready(open_app, page)
    api.set_standings(standings, status=500)
    page.locator("#refreshBtn").click()
    expect(page.locator("#refreshBtn")).to_be_enabled()
    expect(page.locator("#league")).to_contain_text(_FAILURE)


def test_timestamp_only_advances_after_successful_standings_fetch(
        open_app, page, api, wait_until):
    _open_league_ready(open_app, page)
    timestamp = page.locator("#lastUpdated")
    expect(timestamp).to_contain_text(re.compile(r"standings loaded", re.I))
    before = timestamp.inner_text()

    api.hold_path("/api/standings")
    page.locator("#refreshBtn").click()
    wait_until(lambda: api.held_count >= 1)
    # While standings are still in flight the timestamp must not change.
    assert timestamp.inner_text() == before

    api.release_held()
    expect(page.locator("#refreshBtn")).to_be_enabled()
    expect(timestamp).to_contain_text(re.compile(r"standings loaded", re.I))
