"""Multi-competition edge regressions for the browser UI.

Independent from ``test_competitions.py``; these cover races around season
discovery and the current-stage wait that the main suite does not:

* a failed seasons fetch after a competition switch must clear the previous
  competition's season options and its standings, not leave them selectable;
* a delayed previous-season standings response must not repaint while the new
  season's current-stage request is still pending;
* the same for the Overview knockout-pair preview.

Only user-visible behaviour is asserted. The production frontend is served by
the shared conftest fixtures against a deterministic API router (no network).
"""
from __future__ import annotations

from playwright.sync_api import expect


def _release_oldest_held(api):
    """Fulfil ONLY the oldest held request, leaving newer holds pending.

    ``ApiRouter.release_held`` fulfils every held route at once; these races need
    the previous-season response delivered while the new-season current-stage
    request is still in flight. Uses the router's own bookkeeping, which the
    shared fixture exposes.
    """
    route, status, body = api._held_routes.pop(0)
    api._fulfill(route, status, body)


def _watch_reappearance(page, element_id, needle):
    """Flag when ``needle`` (re)appears inside ``#element_id`` while watching."""
    page.evaluate(
        """([id, needle]) => {
            window.__edgePainted = false;
            window.__edgeWatching = false;
            const el = document.getElementById(id);
            new MutationObserver(() => {
                if (window.__edgeWatching && el.textContent.includes(needle)) {
                    window.__edgePainted = true;
                }
            }).observe(el, {childList: true, subtree: true, characterData: true});
        }""",
        [element_id, needle],
    )


def _start_watching(page):
    page.evaluate("window.__edgeWatching = true")


def _painted(page):
    return page.evaluate("window.__edgePainted")


# --------------------------------------------------------------------------- #
# Failed season discovery after a competition switch
# --------------------------------------------------------------------------- #

def test_failed_seasons_after_competition_switch_clears_stale_options(
        open_app, page, api, wait_until):
    api.set_standings_for_competition("EL", [])
    open_app("league")

    select = page.locator("#seasonSelect")
    wait_until(lambda: select.locator("option").count() == 3)
    expect(page.locator("#standingsBody")).to_contain_text("Gamma Pinnacle")

    # Season discovery for the new competition fails (500, after the retry).
    api.seasons_status = 500
    seasons_before = api.counts["GET /api/seasons"]
    page.select_option("#competitionSelect", "EL")
    wait_until(lambda: api.counts["GET /api/seasons"] >= seasons_before + 1)
    page.wait_for_timeout(600)  # let the built-in retry also fail and settle

    # The previous competition's seasons must not linger under the new one.
    assert select.locator("option[value='2024']").count() == 0, (
        "stale 2024/25 remain in the season switcher after a failed seasons fetch"
    )
    assert select.locator("option[value='2025']").count() == 0, (
        "stale 2025/26 remain in the season switcher after a failed seasons fetch"
    )
    # And the previous competition's standings must not repaint.
    expect(page.locator("#standingsBody")).not_to_contain_text("Gamma Pinnacle")


# --------------------------------------------------------------------------- #
# Delayed previous-season payloads during the new-season current-stage wait
# --------------------------------------------------------------------------- #

def test_stale_standings_cannot_repaint_during_new_season_current_stage_wait(
        open_app, page, api, wait_until):
    api.set_standings_for_season(2025, [])
    open_app("league")
    expect(page.locator("#standingsBody")).to_contain_text("Gamma Pinnacle")

    _watch_reappearance(page, "standingsBody", "Gamma Pinnacle")

    # A previous-season standings reload stays in flight...
    api.hold_path("/api/standings")
    page.locator("#refreshBtn").click()
    wait_until(lambda: api.held_count == 1)
    # ...and the new season's current-stage wait is held too.
    api.hold_path("/api/tournament/current-stage")
    _start_watching(page)

    page.select_option("#seasonSelect", "2025")
    wait_until(lambda: api.held_count == 2)

    # Deliver ONLY the previous-season standings while the new-season
    # current-stage request is still pending.
    _release_oldest_held(api)
    page.wait_for_timeout(250)

    assert _painted(page) is False, (
        "previous-season standings repainted during the new-season current-stage wait"
    )

    api.release_held()
    expect(page.locator("#standingsBody")).to_contain_text("No standings data available")


def test_stale_pairs_cannot_repaint_during_new_season_current_stage_wait(
        open_app, page, api, build_pair, wait_until):
    old_pair = build_pair(
        1, (101, "Old Season One", None), (102, "Old Season Two", None),
        win_probability=False,
    )
    api.set_pairs("ROUND_OF_16", [old_pair])
    api.set_pairs_for_season(2025, "ROUND_OF_16", [])
    open_app("")
    expect(page.locator("#overviewKnockoutCards")).to_contain_text("Old Season One")

    _watch_reappearance(page, "overviewKnockoutCards", "Old Season One")

    # A previous-season pair reload stays in flight...
    api.hold_path("/api/knockout-pairs")
    page.locator("#refreshBtn").click()
    wait_until(lambda: api.held_count == 1)
    # ...and the new season's current-stage wait is held too.
    api.hold_path("/api/tournament/current-stage")
    _start_watching(page)

    page.select_option("#seasonSelect", "2025")
    wait_until(lambda: api.held_count == 2)

    _release_oldest_held(api)
    page.wait_for_timeout(250)

    assert _painted(page) is False, (
        "previous-season pairs repainted during the new-season current-stage wait"
    )

    api.release_held()
    expect(page.locator("#overviewKnockoutCards")).not_to_contain_text("Old Season One")
